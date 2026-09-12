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
from promo_bot.affiliate.shadow_delivery import (
    AutomaticShadowDeliveryAuthorization,
    ShadowDeliveryService,
)
from promo_bot.database.repositories import (
    AffiliateShadowPreviewLinkInput,
    AffiliateShadowPreviewRepository,
)
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
    max_send_messages: int = 0
    shutdown_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            self.max_messages < 1
            or self.run_seconds <= 0
            or self.max_api_calls < 1
            or self.max_send_messages < 0
            or self.shutdown_seconds <= 0
        ):
            raise ValueError("ALIEXPRESS_SHADOW_LISTENER_LIMITS_REQUIRED")


class ShadowRunController:
    """Count post-ready events and actual provider sends, stopping at any limit."""

    def __init__(self, limits: ShadowRunLimits) -> None:
        self.limits = limits
        self.shutdown_seconds = limits.shutdown_seconds
        self.messages_received = 0
        self.api_calls = 0
        self.send_messages = 0
        self.deliveries_sent = 0
        self.processed = 0
        self.rejected = 0
        self.failed = 0
        self.cache_hits = 0
        self.previews_created = 0
        self.rejection_codes: list[str] = []
        self.stop_reason: str | None = None
        self.ready = False
        self.accepting = False
        self._terminal_events = 0
        self._stop = asyncio.Event()

    def mark_ready(self) -> None:
        self.ready = True
        self.accepting = self.stop_reason is None

    def try_admit_message(self) -> bool:
        """Atomically admit one event on the listener's single asyncio loop."""

        if not self.ready or not self.accepting:
            return False
        self.messages_received += 1
        if self.messages_received >= self.limits.max_messages:
            self._request_stop("max_messages")
        return True

    def close_admission(self, reason: str | None = None) -> None:
        self.accepting = False
        if reason is not None:
            self._request_stop(reason)

    def record_processed(self, *, cache_hit: bool, preview_created: bool) -> None:
        self.processed += 1
        self._terminal_events += 1
        if cache_hit:
            self.cache_hits += 1
        if preview_created:
            self.previews_created += 1

    def record_rejected(
        self,
        code: str,
        *,
        failed: bool,
        processed: bool,
    ) -> None:
        self.rejected += 1
        self.failed += int(failed)
        self.processed += int(processed)
        self._terminal_events += 1
        safe_code = _safe_counter_code(code)
        if safe_code not in self.rejection_codes:
            self.rejection_codes.append(safe_code)

    def reconcile_unfinished(self, code: str) -> int:
        unfinished = max(0, self.messages_received - self._terminal_events)
        if unfinished:
            self.rejected += unfinished
            self.failed += unfinished
            self._terminal_events += unfinished
            safe_code = _safe_counter_code(code)
            if safe_code not in self.rejection_codes:
                self.rejection_codes.append(safe_code)
        return unfinished

    async def before_api_call(self) -> None:
        """Grant one wire request and count it immediately before HTTPX sends it."""

        if self.api_calls >= self.limits.max_api_calls:
            raise ProviderError("ALIEXPRESS_API_CALL_LIMIT_REACHED", retryable=False)
        self.api_calls += 1
        if self.api_calls >= self.limits.max_api_calls:
            self._request_stop("max_api_calls")

    async def before_send_message(self) -> None:
        """Count a Bot API send immediately before dispatching it."""

        if self.limits.max_send_messages < 1:
            raise ProviderError("SHADOW_SEND_MESSAGE_LIMIT_DISABLED", retryable=False)
        if self.send_messages >= self.limits.max_send_messages:
            raise ProviderError("SHADOW_SEND_MESSAGE_LIMIT_REACHED", retryable=False)
        self.send_messages += 1
        if self.send_messages >= self.limits.max_send_messages:
            self._request_stop("max_send_messages")

    def record_delivery_sent(self) -> None:
        self.deliveries_sent += 1

    async def wait_for_stop(self) -> str:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.limits.run_seconds)
        except TimeoutError:
            self._request_stop("timeout")
        assert self.stop_reason is not None
        return self.stop_reason

    def _request_stop(self, reason: str) -> None:
        self.accepting = False
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
        controller: ShadowRunController,
        *,
        clock: Callable[[], datetime] | None = None,
        content_ttl: timedelta = SHADOW_PREVIEW_CONTENT_TTL,
        delivery: ShadowDeliveryService | None = None,
        destination: str | None = None,
        delivery_authorization: AutomaticShadowDeliveryAuthorization | None = None,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        if content_ttl <= timedelta(0):
            raise ValueError("AFFILIATE_SHADOW_PREVIEW_TTL_INVALID")
        self.database = database
        self.relay_processor = relay_processor
        self.conversion = conversion
        self.controller = controller
        self.clock = clock or (lambda: datetime.now(UTC))
        self.content_ttl = content_ttl
        self.delivery = delivery
        self.destination = destination
        self.delivery_authorization = delivery_authorization
        if (delivery is None) != (destination is None) or (delivery is None) != (
            delivery_authorization is None
        ):
            raise ValueError("ALIEXPRESS_SHADOW_DELIVERY_WIRING_INCOMPLETE")

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
                stored_preview = await repository.save_ready(
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
                    link_correlations=tuple(
                        AffiliateShadowPreviewLinkInput(
                            source_message_link_id=item.source_message_link_id,
                            affiliate_proof_id=item.affiliate_proof_id,
                            ordinal=item.ordinal,
                            occurrence_count=item.occurrence_count,
                            cache_hit=item.cache_hit,
                        )
                        for item in preview.correlations
                    ),
                )
            if self.delivery is not None:
                assert self.destination is not None
                assert self.delivery_authorization is not None
                report = await self.delivery.deliver_automatic(
                    stored_preview.id,
                    self.destination,
                    authorization=self.delivery_authorization,
                    before_send=self.controller.before_send_message,
                )
                if report.get("status") != "sent":
                    self.controller.record_rejected(
                        str(report.get("error_code") or "SHADOW_AUTO_DELIVERY_FAILED"),
                        failed=True,
                        processed=True,
                    )
                    return
                self.controller.record_delivery_sent()
            self.controller.record_processed(
                cache_hit=preview.cache_hit,
                preview_created=True,
            )
        except AliExpressConversionRejected as exc:
            self.controller.record_rejected(
                exc.code,
                failed=exc.failed,
                processed=True,
            )
            LOGGER.info(
                "affiliate shadow message rejected",
                extra={
                    "message_id": str(source_message_id),
                    "stage": "affiliate_shadow_listener",
                    "result": "rejected",
                    "error_code": _safe_counter_code(exc.code),
                },
            )
        except Exception:
            self.controller.record_rejected(
                "AFFILIATE_SHADOW_PROCESSING_FAILED",
                failed=True,
                processed=True,
            )
            LOGGER.error(
                "affiliate shadow message failed",
                extra={
                    "message_id": str(source_message_id),
                    "stage": "affiliate_shadow_listener",
                    "result": "failed",
                    "error_code": "AFFILIATE_SHADOW_PROCESSING_FAILED",
                },
            )


def _safe_counter_code(code: str) -> str:
    if code and all(
        character.isupper() or character.isdigit() or character == "_" for character in code
    ):
        return code
    return "AFFILIATE_SHADOW_REJECTED"
