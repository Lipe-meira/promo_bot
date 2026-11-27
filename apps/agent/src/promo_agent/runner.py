"""The loop: report what is true, receive at most one command, run it once."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from promo_agent.client import Command, NotPaired, RateLimited, SyncReply, Unreachable

_FIRST_BACKOFF = 3.0
_MAX_BACKOFF = 15.0
# While leaving: how often the process is looked at, and every how many looks the API is told.
_LOOK_SECONDS = 0.2
_LOOKS_PER_REPORT = 15


class Client(Protocol):
    def sync(
        self,
        state: str,
        completed_command_id: int | None = None,
        last_event: str | None = None,
        closing: bool = False,
    ) -> SyncReply: ...


class Process(Protocol):
    forced: bool

    @property
    def state(self) -> str: ...
    @property
    def stopping(self) -> bool: ...
    def start(self) -> None: ...
    def request_stop(self) -> None: ...
    def take_unexpected_exit(self) -> bool: ...


class Runner:
    def __init__(
        self,
        client: Client,
        process: Process,
        log: Callable[[str], None],
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._process = process
        self._log = log
        self._done: int | None = None
        self._event: str | None = None
        self._connected = False
        self._pause = pause
        self._stop_command: int | None = None  # asked, the process has not left yet

    def step(self) -> float:
        """One exchange with the API; returns how long to wait before the next."""

        if self._process.take_unexpected_exit():
            self._event = "exited"
            self._log("O processo de teste terminou sozinho. Ele não será reiniciado.")
        self._settle_stop()
        reply = self._client.sync(self._process.state, self._done, self._event)
        if not self._connected:
            self._connected = True
            self._log("Conectado à API.")
        command = reply.command
        # The same command may arrive twice (a lost report): it is never run twice.
        if command is None or command.id == self._done:
            return reply.poll_seconds
        if command.action == "stop":
            return 0.0 if self._begin_stop(command) else reply.poll_seconds
        if self._process.stopping:
            # A start waits for the old process to leave. Not acknowledged meanwhile: if
            # the API drops it before that, it is never run late.
            return reply.poll_seconds
        self._start(command)
        return 0.0  # report the result at once

    def _begin_stop(self, command: Command) -> bool:
        """Ask the process to leave; True if it already has."""

        already_asked = self._process.stopping
        self._process.request_stop()
        self._stop_command = command.id
        self._settle_stop()
        if self._done == command.id:
            return True
        if not already_asked:
            self._log("Parando o processo de teste: aguardando ele sair (até 60 s).")
        return False

    def _settle_stop(self) -> None:
        """A stop counts as done only once the process has really left."""

        if self._stop_command is None or self._process.state != "stopped":
            return
        self._done, self._stop_command = self._stop_command, None
        self._event = None
        self._log(
            "Processo de teste não saiu a tempo e foi encerrado à força."
            if self._process.forced
            else "Processo de teste parado."
        )

    def _start(self, command: Command) -> None:
        try:
            self._process.start()
            self._event = None
            self._log("Processo de teste em execução.")
        except OSError:
            self._event = "start_failed"
            self._log("Não foi possível iniciar o processo de teste.")
        self._stop_command = None
        self._done = command.id

    def run(self, wait: Callable[[float], bool]) -> bool:
        """Until `wait` returns True (asked to leave). False if the pairing was revoked."""

        # The next delay after a failure. It doubles only up to the ceiling and is never derived
        # from a count of failures, so an outage of any length costs the same.
        backoff = _FIRST_BACKOFF
        limited = False
        try:
            while True:
                try:
                    delay = self.step()
                    backoff = _FIRST_BACKOFF
                    limited = False
                except Unreachable:
                    # The process keeps its state; the panel shows the agent as disconnected.
                    if self._connected or backoff == _FIRST_BACKOFF:
                        self._log("Sem contato com a API; tentando de novo.")
                    self._connected = False
                    delay = backoff
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                except RateLimited as refusal:
                    # Wait as asked, never less than the usual interval. The pairing, the
                    # process and any result still to be reported are kept.
                    delay = max(refusal.seconds, _FIRST_BACKOFF)
                    if not limited:
                        self._log(
                            f"Muitas chamadas: a API pediu para aguardar {delay:.0f} s. "
                            "O pareamento continua."
                        )
                    limited = True
                except NotPaired:
                    self._log("O pareamento foi removido no painel. Encerrando.")
                    return False
                if wait(delay):
                    return True
        finally:
            self.close()

    def close(self) -> None:
        """Leave nothing running: ask the process to stop, wait for it, and keep telling the
        panel meanwhile, if it can still be told."""

        self._process.request_stop()
        reachable = True
        looks = 0
        while self._process.state == "running":
            if looks == 0:
                self._log("Aguardando o processo sair antes de encerrar (até 60 s).")
            if reachable and looks % _LOOKS_PER_REPORT == 0:
                try:
                    self._client.sync(self._process.state, self._done, self._event)
                except Unreachable:
                    pass
                except (NotPaired, RateLimited):
                    reachable = False
            looks += 1
            self._pause(_LOOK_SECONDS)
        self._settle_stop()
        try:
            self._client.sync(self._process.state, self._done, None, closing=True)
        except (Unreachable, NotPaired, RateLimited):
            pass
