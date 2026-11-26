"""Asking a long-running command to stop in order, on Windows and elsewhere.

A stop is asked for by Ctrl+C, Ctrl+Break, a termination signal or, for a supervising program,
by closing the standard input of the process (opt-in). The first request starts the ordered
stop; a second signal gives up waiting and interrupts.

The standard input exists as a trigger because a Windows supervisor that starts the bot without
sharing its console cannot send it console control events.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType

LOGGER = logging.getLogger("promo_bot.shutdown")

#: How long the work already in progress may take to finish after a stop is asked for.
DEFAULT_STOP_GRACE_SECONDS = 20.0

_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, name)
)


class StopRequest:
    """Set once, by whoever asks first. Create and use it inside one event loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: str | None = None

    @property
    def requested(self) -> bool:
        return self.reason is not None

    def request(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
            LOGGER.info(
                "ordered stop requested",
                extra={"stage": "shutdown", "result": reason},
            )
            self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


def _watch_standard_input(ask: Callable[[str], None]) -> None:
    try:
        # The raw descriptor, never `sys.stdin`: a thread blocked on a buffered reader would
        # break interpreter shutdown. Anything written is ignored; only the end matters.
        while os.read(0, 4096):
            pass
    except OSError:
        pass
    ask("input_closed")


@contextmanager
def stop_triggers(stop: StopRequest, *, stdin_eof: bool) -> Iterator[None]:
    """Route signals (and, if asked, the end of standard input) into `stop` while inside."""

    loop = asyncio.get_running_loop()
    signalled = False

    def ask(reason: str) -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(stop.request, reason)

    def on_signal(signum: int, frame: FrameType | None) -> None:
        nonlocal signalled
        if signalled:  # asked twice: stop waiting
            raise KeyboardInterrupt
        signalled = True
        ask("signal")

    previous = {number: signal.signal(number, on_signal) for number in _SIGNALS}
    if stdin_eof:
        threading.Thread(
            target=_watch_standard_input, args=(ask,), daemon=True, name="stop-on-stdin-eof"
        ).start()
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
