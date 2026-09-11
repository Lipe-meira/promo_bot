"""Literal, single-message Bot API adapter using the project's existing PTB library."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from telegram import Bot
from telegram.error import BadRequest, Forbidden, InvalidToken, RetryAfter
from telegram.request import HTTPXRequest

from promo_bot.affiliate.shadow_delivery import DefinitiveSendRejection
from promo_bot.observability.shadow import mute_shadow_payload_logs


class ShadowBotRequest(HTTPXRequest):
    """Validate rejection evidence, not just PTB's HTTP-status exception mapping."""

    definitive_send_rejection: bool = False

    async def do_request(self, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        self.definitive_send_rejection = False
        status, payload = await super().do_request(*args, **kwargs)
        url = kwargs.get("url", args[0] if args else "")
        if str(url).endswith("/sendMessage") and status in {400, 401, 403, 404, 429}:
            try:
                envelope = json.loads(payload)
            except (ValueError, UnicodeError):
                envelope = None
            self.definitive_send_rejection = (
                isinstance(envelope, dict)
                and envelope.get("ok") is False
                and envelope.get("error_code") == status
                and isinstance(envelope.get("description"), str)
                and bool(envelope["description"].strip())
                and "result" not in envelope
            )
        return status, payload


def build_request() -> HTTPXRequest:
    return ShadowBotRequest(
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
        pool_timeout=5,
        httpx_kwargs={"trust_env": False, "follow_redirects": False},
    )


@dataclass(frozen=True, repr=False)
class PrivateChannelInfo:
    id: int
    type: str
    username: str | None
    active_usernames: tuple[str, ...]


class ShadowBotTransport:
    def __init__(self, token: str) -> None:
        self._token = token
        self._bot: Bot | None = None
        self._request: HTTPXRequest | None = None
        self._logs = ExitStack()

    async def __aenter__(self) -> ShadowBotTransport:
        self._logs.enter_context(mute_shadow_payload_logs())
        try:
            self._request = build_request()
            await self._request.initialize()
            # Manage only the request lifecycle: Bot.initialize would also issue getMe.
            self._bot = Bot(self._token, request=self._request, get_updates_request=self._request)
        except Exception:
            if self._request is not None:
                await self._request.shutdown()
            self._logs.close()
            raise RuntimeError("SHADOW_BOT_INITIALIZATION_FAILED") from None
        return self

    async def __aexit__(self, *args: Any) -> None:
        try:
            if self._request is not None:
                await self._request.shutdown()
        finally:
            self._bot = None
            self._logs.close()

    async def get_chat(self, chat_id: str) -> PrivateChannelInfo:
        if self._bot is None:
            raise RuntimeError("SHADOW_BOT_NOT_INITIALIZED")
        try:
            chat = await self._bot.get_chat(chat_id=chat_id)
            return PrivateChannelInfo(chat.id, chat.type, chat.username, chat.active_usernames)
        except Exception:
            raise RuntimeError("SHADOW_GET_CHAT_FAILED") from None

    async def send_text(self, chat_id: str, text: str) -> str:
        if self._bot is None:
            raise RuntimeError("SHADOW_BOT_NOT_INITIALIZED")
        try:
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                entities=None,
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except (BadRequest, Forbidden, InvalidToken, RetryAfter):
            if (
                isinstance(self._request, ShadowBotRequest)
                and self._request.definitive_send_rejection
            ):
                raise DefinitiveSendRejection("SHADOW_SEND_REJECTED") from None
            raise RuntimeError("SHADOW_SEND_UNCERTAIN") from None
        except Exception:
            raise RuntimeError("SHADOW_SEND_UNCERTAIN") from None
        if str(message.chat.id) != chat_id:
            raise RuntimeError("SHADOW_SEND_UNCERTAIN")
        return str(message.message_id)

    def __repr__(self) -> str:
        return "ShadowBotTransport(token=<redacted>)"
