"""The loop: report what is true, receive at most one command, run it once."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from promo_agent.client import Command, NotPaired, SyncReply, Unreachable

_FIRST_BACKOFF = 3.0
_MAX_BACKOFF = 15.0


class Client(Protocol):
    def sync(
        self,
        state: str,
        completed_command_id: int | None = None,
        last_event: str | None = None,
        closing: bool = False,
    ) -> SyncReply: ...


class Process(Protocol):
    @property
    def state(self) -> str: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def take_unexpected_exit(self) -> bool: ...


class Runner:
    def __init__(self, client: Client, process: Process, log: Callable[[str], None]) -> None:
        self._client = client
        self._process = process
        self._log = log
        self._done: int | None = None
        self._event: str | None = None
        self._connected = False

    def step(self) -> float:
        """One exchange with the API; returns how long to wait before the next."""

        if self._process.take_unexpected_exit():
            self._event = "exited"
            self._log("O processo de teste terminou sozinho. Ele não será reiniciado.")
        reply = self._client.sync(self._process.state, self._done, self._event)
        if not self._connected:
            self._connected = True
            self._log("Conectado à API.")
        command = reply.command
        # The same command may arrive twice (a lost report): it is never run twice.
        if command is None or command.id == self._done:
            return reply.poll_seconds
        self._execute(command)
        return 0.0  # report the result at once

    def _execute(self, command: Command) -> None:
        if command.action == "start":
            try:
                self._process.start()
                self._event = None
                self._log("Processo de teste em execução.")
            except OSError:
                self._event = "start_failed"
                self._log("Não foi possível iniciar o processo de teste.")
        else:
            self._process.stop()
            self._event = None
            self._log("Processo de teste parado.")
        self._done = command.id

    def run(self, wait: Callable[[float], bool]) -> bool:
        """Until `wait` returns True (asked to leave). False if the pairing was revoked."""

        failures = 0
        try:
            while True:
                try:
                    delay = self.step()
                    failures = 0
                except Unreachable:
                    # The process keeps its state; the panel shows the agent as disconnected.
                    if self._connected or failures == 0:
                        self._log("Sem contato com a API; tentando de novo.")
                    self._connected = False
                    delay = min(_FIRST_BACKOFF * 2**failures, _MAX_BACKOFF)
                    failures += 1
                except NotPaired:
                    self._log("O pareamento foi removido no painel. Encerrando.")
                    return False
                if wait(delay):
                    return True
        finally:
            self.close()

    def close(self) -> None:
        """Leave nothing running and tell the panel, if it can still be told."""

        self._process.stop()
        try:
            self._client.sync(self._process.state, self._done, None, closing=True)
        except (Unreachable, NotPaired):
            pass
