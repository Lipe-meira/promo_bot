"""Bounded, provider-specific processing for continuous Telegram shadow previews."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionRejected,
    AliExpressMessageConversionService,
)
from promo_bot.database.repositories import AffiliateShadowPreviewRepository
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.domain.enums import Store
from promo_bot.providers.base import ProviderError
from promo_bot.relay.service import RelayProcessor

LOGGER = logging.getLogger("promo_bot.affiliate.aliexpress_shadow_listener")
SHADOW_PREVIEW_CONTENT_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ShadowRunLimits:
    max_messages: int
    run_seconds: float
    max_api_calls: int

    def __post_init__(self) -> None:
        if self.max_messages < 1 or self.run_seconds <= 0 or self.max_api_calls < 1:
            raise ValueError("ALIEXPRESS_SHADOW_LISTENER_LIMITS_REQUIRED")


class ShadowRunController:
    """Count post-ready events and actual provider sends, stopping at any limit."""

    def __init__(self, limits: ShadowRunLimits) -> None:
        self.limits = limits
        self.messages_received = 0
        self.api_calls = 0
        self.stop_reason: str | None = None
        self.ready = False
        self._stop = asyncio.Event()

    def mark_ready(self) -> None:
        self.ready = True

    def record_message(self) -> None:
        if not self.ready:
            return
        self.messages_received += 1
        if self.messages_received >= self.limits.max_messages:
            self._request_stop("max_messages")

    async def before_api_call(self) -> None:
        """Grant one wire request and count it immediately before HTTPX sends it."""

        if self.api_calls >= self.limits.max_api_calls:
            raise ProviderError("ALIEXPRESS_API_CALL_LIMIT_REACHED", retryable=False)
        self.api_calls += 1
        if self.api_calls >= self.limits.max_api_calls:
            self._request_stop("max_api_calls")

    async def wait_for_stop(self) -> str:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.limits.run_seconds)
        except TimeoutError:
            self._request_stop("timeout")
        assert self.stop_reason is not None
        return self.stop_reason

    def _request_stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
            self._stop.set()


class AliExpressShadowMessageProcessor:
    """Create one retained preview per new message, without publication side effects."""

    def __init__(
        self,
        database: AffiliateShadowDatabase,
        relay_processor: RelayProcessor,
        conversion: AliExpressMessageConversionService,
        *,
        clock: Callable[[], datetime] | None = None,
        content_ttl: timedelta = SHADOW_PREVIEW_CONTENT_TTL,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        if content_ttl <= timedelta(0):
            raise ValueError("AFFILIATE_SHADOW_PREVIEW_TTL_INVALID")
        self.database = database
        self.relay_processor = relay_processor
        self.conversion = conversion
        self.clock = clock or (lambda: datetime.now(UTC))
        self.content_ttl = content_ttl

    async def process(self, source_message_id: int) -> None:
        try:
            await self.relay_processor.process(source_message_id)
            preview = await self.conversion.convert(source_message_id)
            if preview.affiliate_proof_id is None:
                raise AliExpressConversionRejected("ALIEXPRESS_PROOF_CORRELATION_MISSING")
            now = self.clock()
            async with self.database.session() as session:
                repository = AffiliateShadowPreviewRepository(session)
                await repository.purge_expired_content(now=now)
                await repository.save_ready(
                    provider="aliexpress_official",
                    store=Store.ALIEXPRESS.value,
                    source_message_id=source_message_id,
                    affiliate_proof_id=preview.affiliate_proof_id,
                    replacement_count=preview.replacement_count,
                    cache_hit=preview.cache_hit,
                    affiliate_host=preview.affiliate_host,
                    rendered_text=preview.converted_text,
                    affiliate_link=preview.affiliate_link,
                    created_at=now,
                    content_ttl=self.content_ttl,
                )
        except AliExpressConversionRejected as exc:
            LOGGER.info(
                "affiliate shadow message rejected",
                extra={
                    "message_id": str(source_message_id),
                    "stage": "affiliate_shadow_listener",
                    "result": "rejected",
                    "error_code": str(exc),
                },
            )
        except Exception:
            LOGGER.error(
                "affiliate shadow message failed",
                extra={
                    "message_id": str(source_message_id),
                    "stage": "affiliate_shadow_listener",
                    "result": "failed",
                    "error_code": "AFFILIATE_SHADOW_PROCESSING_FAILED",
                },
            )
