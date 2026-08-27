"""Bot API boundary restricted to a fixed synthetic Phase 2 test."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TimedOut

from promo_bot.config.settings import EnvironmentSettings
from promo_bot.relay.formatter import RenderedMessage, render_synthetic_test
from promo_bot.relay.models import RelayProcessingError
from promo_bot.relay.retry import BackoffPolicy


class SyntheticBotSender:
    """Expose no general promotion-send method while providers are absent."""

    def __init__(
        self,
        settings: EnvironmentSettings,
        *,
        max_attempts: int = 3,
        backoff: BackoffPolicy | None = None,
    ) -> None:
        if settings.telegram_bot_token is None or settings.telegram_target_chat_id is None:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID are required")
        self.token = settings.telegram_bot_token.get_secret_value()
        self.chat_id = settings.telegram_target_chat_id
        self.max_attempts = max_attempts
        self.backoff = backoff or BackoffPolicy(2, 30)

    async def send_test(self) -> RenderedMessage:
        rendered = render_synthetic_test()
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(rendered.button_label, url=rendered.button_url)]]
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                async with Bot(self.token) as bot:
                    await bot.send_message(
                        chat_id=self.chat_id,
                        text=rendered.text,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                return rendered
            except RetryAfter as exc:
                if attempt >= self.max_attempts:
                    raise RelayProcessingError("BOT_RETRY_EXHAUSTED", retryable=False) from exc
                retry_after = exc.retry_after
                delay = (
                    retry_after.total_seconds()
                    if isinstance(retry_after, timedelta)
                    else float(retry_after)
                )
                await asyncio.sleep(min(delay, float(self.backoff.maximum_seconds)))
            except (BadRequest, Forbidden, InvalidToken) as exc:
                raise RelayProcessingError("BOT_PERMANENT_REJECTION", retryable=False) from exc
            except (TimedOut, NetworkError) as exc:
                if attempt >= self.max_attempts:
                    raise RelayProcessingError("BOT_RETRY_EXHAUSTED", retryable=False) from exc
                await asyncio.sleep(self.backoff.delay_seconds(attempt))
        raise AssertionError("unreachable")
