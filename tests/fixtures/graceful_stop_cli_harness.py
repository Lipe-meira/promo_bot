"""Offline subprocess harness: the real `promo-bot` CLI with a fake Telegram client.

Nothing leaves the PC: the Telegram client is fake, processing a link only waits, and external
sockets raise. Behaviour is chosen by environment variables:

- HARNESS_MESSAGES: comma-separated ids served by the catch-up (history) of the source channel.
- HARNESS_LIVE_MESSAGES: ids delivered as new messages once the listener is connected.
- HARNESS_PROCESS_SECONDS: how long processing one message takes.

Markers go to stdout, one per line: `HARNESS ready`, `HARNESS start <id>`, `HARNESS end <id>`,
`HARNESS disconnected`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon.tl.types import PeerChannel

import promo_bot.relay.service as relay_service
import promo_bot.telegram.monitor as telegram_monitor
from promo_bot import cli

CHANNEL = PeerChannel(1234567890)  # chat id -1001234567890
CHAT_ID = -1001234567890
# As in Telegram, a higher id is a newer message.
STARTED = datetime.now(UTC) - timedelta(minutes=5)


class _ExternalNetworkBlockedSocket(socket.socket):
    def connect(self, address: Any) -> None:
        if not _is_loopback(address):
            raise AssertionError("subprocess attempted an external connection")
        super().connect(address)

    def connect_ex(self, address: Any) -> int:
        if not _is_loopback(address):
            raise AssertionError("subprocess attempted an external connection")
        return super().connect_ex(address)


def _is_loopback(address: object) -> bool:
    if not isinstance(address, tuple) or not address:
        return False
    try:
        return ipaddress.ip_address(str(address[0])).is_loopback
    except ValueError:
        return False


def say(message: str) -> None:
    print(f"HARNESS {message}", flush=True)


def _ids(name: str) -> list[int]:
    return [int(value) for value in os.environ.get(name, "").split(",") if value]


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.date = STARTED + timedelta(seconds=message_id)
        self.raw_text = f"offer {message_id} https://example.test/p/{message_id}"
        self.out = False
        self.buttons = None
        self.media = None

    def get_entities_text(self) -> list[tuple[object, str]]:
        return []


class FakeEvent:
    def __init__(self, message_id: int) -> None:
        self.chat_id = CHAT_ID
        self.message = FakeMessage(message_id)


class FakeTelegramClient:
    def __init__(self) -> None:
        self._handler: Any = None
        self._disconnected = asyncio.Event()
        self._live: list[asyncio.Task[None]] = []

    async def connect(self) -> None:
        pass

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, reference: object) -> object:
        return CHANNEL

    def add_event_handler(self, callback: Any, builder: object) -> None:
        self._handler = callback

    def remove_event_handler(self, callback: Any, builder: object) -> None:
        self._handler = None

    async def iter_messages(self, entity: object, limit: int) -> Any:
        for message_id in sorted(_ids("HARNESS_MESSAGES"), reverse=True)[:limit]:
            yield FakeMessage(message_id)

    async def run_until_disconnected(self) -> None:
        for message_id in _ids("HARNESS_LIVE_MESSAGES"):
            if self._handler is not None:
                await self._handler(FakeEvent(message_id))
        say("ready")
        await self._disconnected.wait()

    async def disconnect(self) -> None:
        self._disconnected.set()
        say("disconnected")


async def _slow_process_link(self: Any, source_message_id: int, link: Any) -> None:
    message_id = link.url.rsplit("/", 1)[-1]
    say(f"start {message_id}")
    await asyncio.sleep(float(os.environ.get("HARNESS_PROCESS_SECONDS", "0")))
    say(f"end {message_id}")


def main() -> int:
    socket.__dict__["socket"] = _ExternalNetworkBlockedSocket
    telegram_monitor.build_telegram_user_client = lambda *a, **k: FakeTelegramClient()
    relay_service.RelayProcessor._process_link = _slow_process_link
    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
