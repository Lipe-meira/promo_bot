"""Dedicated Bot API adapter for isolated coin-shadow delivery."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from telegram import Bot
from telegram.error import BadRequest, Forbidden, InvalidToken, RetryAfter
from telegram.request import HTTPXRequest

from promo_bot.affiliate.shadow_delivery import DefinitiveSendRejection
from promo_bot.observability.shadow import mute_shadow_payload_logs
from promo_bot.telegram.shadow_bot import ShadowBotRequest, build_request


@dataclass(frozen=True, slots=True, repr=False)
class CoinShadowChannelAccessInfo:
    id: int
    type: str
    username: str | None
    active_usernames: tuple[str, ...]
    bot_membership_status: str
    can_post_messages: bool


class CoinShadowBotTransport:
    def __init__(self, token: str) -> None:
        self._token = token
        self._bot: Bot | None = None
        self._request: HTTPXRequest | None = None
        self._logs = ExitStack()

    async def __aenter__(self) -> CoinShadowBotTransport:
        self._logs.enter_context(mute_shadow_payload_logs())
        try:
            self._request = build_request()
            await self._request.initialize()
            self._bot = Bot(
                self._token,
                request=self._request,
                get_updates_request=self._request,
            )
        except Exception:
            if self._request is not None:
                await self._request.shutdown()
            self._logs.close()
            raise RuntimeError("COIN_SHADOW_BOT_INITIALIZATION_FAILED") from None
        return self

    async def __aexit__(self, *args: Any) -> None:
        try:
            if self._request is not None:
                await self._request.shutdown()
        finally:
            self._bot = None
            self._logs.close()

    async def inspect_private_channel(self, chat_id: str) -> CoinShadowChannelAccessInfo:
        if self._bot is None:
            raise RuntimeError("COIN_SHADOW_BOT_NOT_INITIALIZED")
        try:
            chat = await self._bot.get_chat(chat_id=chat_id)
            bot_user = await self._bot.get_me()
            member = await self._bot.get_chat_member(chat_id=chat_id, user_id=bot_user.id)
        except Exception:
            raise RuntimeError("COIN_SHADOW_CHANNEL_PREFLIGHT_FAILED") from None
        status = str(member.status)
        can_post = (
            status in {"creator", "owner"} or getattr(member, "can_post_messages", False) is True
        )
        return CoinShadowChannelAccessInfo(
            id=chat.id,
            type=chat.type,
            username=chat.username,
            active_usernames=chat.active_usernames,
            bot_membership_status=status,
            can_post_messages=can_post,
        )

    async def send_text(self, chat_id: str, text: str) -> str:
        if self._bot is None:
            raise RuntimeError("COIN_SHADOW_BOT_NOT_INITIALIZED")
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
                raise DefinitiveSendRejection("COIN_SHADOW_SEND_REJECTED") from None
            raise RuntimeError("COIN_SHADOW_SEND_UNCERTAIN") from None
        except Exception:
            raise RuntimeError("COIN_SHADOW_SEND_UNCERTAIN") from None
        if str(message.chat.id) != chat_id:
            raise RuntimeError("COIN_SHADOW_SEND_UNCERTAIN")
        return str(message.message_id)

    def __repr__(self) -> str:
        return "CoinShadowBotTransport(token=<redacted>)"
