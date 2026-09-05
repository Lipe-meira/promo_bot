"""One-shot Telegram-to-AliExpress conversion isolated from the main database."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionRejected,
    AliExpressDryRunPreview,
    AliExpressMessageConversionService,
)
from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.session import Database
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.relay.service import RelayProcessor
from promo_bot.security.urls import SafeUrlError, UrlExpansionResult
from promo_bot.telegram.monitor import TelegramMessageReference

LOGGER = logging.getLogger("promo_bot.affiliate.aliexpress_shadow")


class TelegramMessageReader(Protocol):
    async def fetch(self, reference: TelegramMessageReference) -> IncomingMessage: ...


class ShadowNoRedirectExpander:
    """Prevent the shadow path from resolving any short or redirecting URL."""

    async def expand(self, url: str) -> UrlExpansionResult:
        del url
        raise SafeUrlError("SHADOW_REDIRECT_RESOLUTION_DISABLED")


class AliExpressTelegramShadowService:
    """Read and convert one Telegram message without background workers or publication."""

    def __init__(
        self,
        database: Database,
        reader: TelegramMessageReader,
        conversion: AliExpressMessageConversionService,
        *,
        relay_config: TelegramRelayConfig,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.reader = reader
        self.conversion = conversion
        self.clock = clock or (lambda: datetime.now(UTC))
        processor = RelayProcessor(
            database,
            relay_config,
            expander=ShadowNoRedirectExpander(),
            clock=self.clock,
        )
        self.relay = DurableRelayQueue(
            database,
            relay_config,
            processor=processor,
            clock=self.clock,
        )

    async def preview(self, reference: TelegramMessageReference) -> AliExpressDryRunPreview:
        message = await self.reader.fetch(reference)
        if message.platform != "telegram" or message.message_id != reference.message_id:
            raise AliExpressConversionRejected("TELEGRAM_MESSAGE_IDENTITY_MISMATCH")
        persisted = await self.relay.persist_without_enqueue(message)
        if not persisted.content_matches:
            raise AliExpressConversionRejected("TELEGRAM_SOURCE_CONTENT_MISMATCH")
        await self.relay.processor.process(persisted.internal_id)
        preview = await self.conversion.convert(persisted.internal_id)
        LOGGER.info(
            "AliExpress Telegram shadow preview prepared",
            extra={
                "channel_id": message.channel_id,
                "message_id": str(message.message_id),
                "stage": "aliexpress_telegram_shadow",
                "result": "preview_ready",
                "cache_hit": preview.cache_hit,
            },
        )
        return preview

    def __repr__(self) -> str:
        return "AliExpressTelegramShadowService(preview=<redacted>, publication=False)"

    __str__ = __repr__


def resolve_shadow_database_path(
    settings: EnvironmentSettings,
    explicit_path: Path | None = None,
) -> Path:
    """Resolve a dedicated SQLite file and reject every path inside the Git workspace."""

    path = explicit_path or settings.resolved_runtime_dir / "shadow" / "aliexpress-shadow.sqlite3"
    resolved = path.expanduser().resolve()
    repository_root = Path(__file__).resolve().parents[3]
    if resolved == repository_root or repository_root in resolved.parents:
        raise ValueError("ALIEXPRESS_SHADOW_DATABASE_MUST_BE_EXTERNAL")
    if resolved.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError("ALIEXPRESS_SHADOW_DATABASE_EXTENSION_INVALID")
    return resolved


def shadow_database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"
