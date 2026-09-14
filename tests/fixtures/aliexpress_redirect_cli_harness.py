"""Offline subprocess harness for the real shadow-auto-deliver CLI entrypoint."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
from telethon.tl.types import MessageEntityUrl

import promo_bot.cli as cli
import promo_bot.telegram.monitor as telegram_monitor
from promo_bot.security.aliexpress_short_links import (
    AliExpressRedirectHop,
    AliExpressShortLinkResolver,
)

SOURCE_CHAT_ID = -1001234567890
DESTINATION_CHAT_ID = -1009876543210
SOURCE_URL = "https://A.ALIEXPRESS.COM/_Ab12Cd34"


class _ExternalNetworkBlockedSocket(socket.socket):
    _promo_bot_external_network_blocked = True

    def connect(self, address: Any) -> None:
        if self.family != getattr(socket, "AF_UNIX", None) and not _is_loopback_address(address):
            raise AssertionError("subprocess attempted an external connection")
        super().connect(address)

    def connect_ex(self, address: Any) -> int:
        if self.family != getattr(socket, "AF_UNIX", None) and not _is_loopback_address(address):
            raise AssertionError("subprocess attempted an external connection")
        return super().connect_ex(address)


def _is_loopback_address(address: object) -> bool:
    if not isinstance(address, tuple) or not address:
        return False
    try:
        return ipaddress.ip_address(str(address[0])).is_loopback
    except ValueError:
        return False


def _install_external_network_guard() -> None:
    socket.__dict__["socket"] = _ExternalNetworkBlockedSocket


def _assert_external_network_is_blocked() -> None:
    candidate = socket.socket()
    try:
        assert getattr(candidate, "_promo_bot_external_network_blocked", False)
    finally:
        candidate.close()


class _Message:
    id = 901
    date = datetime(2026, 9, 14, 12, tzinfo=UTC)
    raw_text = f"PRIVATE_MESSAGE_FIXTURE\n{SOURCE_URL}"
    out = False
    buttons = None
    media = None

    @staticmethod
    def get_entities_text() -> list[tuple[object, str]]:
        return [
            (
                MessageEntityUrl(
                    offset=len("PRIVATE_MESSAGE_FIXTURE\n"),
                    length=len(SOURCE_URL),
                ),
                SOURCE_URL,
            )
        ]


class _TelegramClient:
    def __init__(self) -> None:
        self.handler_tasks: list[asyncio.Task[None]] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        pending = [task for task in self.handler_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, _reference: str | int) -> object:
        return SimpleNamespace(id=1234567890)

    def add_event_handler(self, callback: Any, _builder: object) -> None:
        async def emit() -> None:
            await asyncio.sleep(0)
            await callback(SimpleNamespace(chat_id=SOURCE_CHAT_ID, message=_Message()))

        self.handler_tasks.append(asyncio.create_task(emit()))

    def remove_event_handler(self, _callback: object, _builder: object) -> None:
        return None


class _BotTransport:
    def __init__(self, _token: str) -> None:
        pass

    async def __aenter__(self) -> _BotTransport:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_chat(self, chat_id: str) -> object:
        return SimpleNamespace(
            id=int(chat_id),
            type="channel",
            username=None,
            active_usernames=(),
        )

    async def send_text(self, _chat_id: str, _text: str) -> str:
        raise AssertionError("redirect rejection must not send a message")


class _DnsResolver:
    async def resolve(self, _hostname: str, _port: int) -> frozenset[str]:
        return frozenset({"8.8.8.8"})


class _RedirectRequester:
    async def fetch(
        self,
        _url: str,
        *,
        method: str,
        allowed_ips: frozenset[str],
    ) -> AliExpressRedirectHop:
        _assert_external_network_is_blocked()
        assert method == "GET"
        assert allowed_ips == frozenset({"8.8.8.8"})
        return AliExpressRedirectHop(
            302,
            {"location": ("https://BÜCHER.example/private/path?token=SYNTHETIC_REDIRECT_SECRET")},
        )


class _OfflineRejectedResolver(AliExpressShortLinkResolver):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(
            resolver=_DnsResolver(),
            requester=_RedirectRequester(),
            timeout_seconds=float(kwargs.get("timeout_seconds", 10.0)),
            max_redirects=int(kwargs.get("max_redirects", 5)),
        )


async def _forbid_affiliate_api(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"redirect rejection must not call Affiliate API: {request.method}")


def run() -> None:
    _install_external_network_guard()
    cli.build_telegram_user_client = lambda *_args, **_kwargs: _TelegramClient()
    cli.build_offline_safe_http_client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(_forbid_affiliate_api),
        trust_env=False,
        follow_redirects=False,
    )
    cli.ShadowBotTransport = _BotTransport
    cli.AliExpressShortLinkResolver = _OfflineRejectedResolver
    telegram_monitor.utils.get_peer_id = lambda _entity: SOURCE_CHAT_ID
    cli.entrypoint()


if __name__ == "__main__":
    run()
