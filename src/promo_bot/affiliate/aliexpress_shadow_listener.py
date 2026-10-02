"""Bounded, provider-specific processing for continuous Telegram shadow previews."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionRejected,
    AliExpressMessageConversionService,
)
from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService
from promo_bot.affiliate.coin_shadow_multi import CoinShadowMultiPreview, collect_coin_occurrences
from promo_bot.affiliate.coin_shadow_preview import (
    CoinShadowPreviewRejected,
    CoinShadowPreviewService,
)
from promo_bot.affiliate.shadow_delivery import (
    AutomaticShadowDeliveryAuthorization,
    ShadowDeliveryService,
)
from promo_bot.database.repositories import (
    AffiliateShadowPreviewLinkInput,
    AffiliateShadowPreviewRepository,
    SourceMessageRepository,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.domain.enums import Store
from promo_bot.providers.base import ProviderError
from promo_bot.relay.models import ExtractedLink, IncomingMessage, MessageSurfaceMetadata
from promo_bot.relay.parser import TRAILING_PUNCTUATION, URL_PATTERN
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
        self.coin_multi: dict[str, int] | None = None
        self.skipped = 0
        self.rejection_codes: list[str] = []
        self.skip_codes: list[str] = []
        self.stop_reason: str | None = None
        self.ready = False
        self.accepting = False
        self._terminal_events = 0
        self._stop = asyncio.Event()
        self._run_deadline: float | None = None

    def start_run_timer(self) -> None:
        if self._run_deadline is None:
            self._run_deadline = asyncio.get_running_loop().time() + self.limits.run_seconds

    def remaining_run_seconds(self) -> float:
        self.start_run_timer()
        assert self._run_deadline is not None
        return max(0.0, self._run_deadline - asyncio.get_running_loop().time())

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

    def record_skipped(self, code: str) -> None:
        self.processed += 1
        self.skipped += 1
        self._terminal_events += 1
        safe_code = _safe_counter_code(code)
        if safe_code not in self.skip_codes:
            self.skip_codes.append(safe_code)

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

    def enable_coin_multi(self) -> None:
        self.coin_multi = dict.fromkeys(
            (
                "occurrences_admitted",
                "distinct_inputs_admitted",
                "cache_distinct_inputs",
                "generated_distinct_inputs_confirmed",
                "in_message_reuses",
                "all_cache_messages",
                "partial_cache_messages",
            ),
            0,
        )

    def remaining_api_calls(self) -> int:
        return max(0, self.limits.max_api_calls - self.api_calls)

    def remaining_send_messages(self) -> int:
        return max(0, self.limits.max_send_messages - self.send_messages)

    def record_coin_inputs(self, occurrences: int, distinct: int) -> None:
        assert self.coin_multi is not None
        self.coin_multi["occurrences_admitted"] += occurrences
        self.coin_multi["distinct_inputs_admitted"] += distinct
        self.coin_multi["in_message_reuses"] += occurrences - distinct

    def record_coin_generation(self, *, cache_hit: bool) -> None:
        assert self.coin_multi is not None
        key = "cache_distinct_inputs" if cache_hit else "generated_distinct_inputs_confirmed"
        self.coin_multi[key] += 1

    def record_coin_message(self, cache_inputs: int, distinct: int) -> None:
        assert self.coin_multi is not None
        if cache_inputs == distinct:
            self.coin_multi["all_cache_messages"] += 1
        elif cache_inputs:
            self.coin_multi["partial_cache_messages"] += 1

    async def wait_for_stop(self) -> str:
        if self._stop.is_set():
            assert self.stop_reason is not None
            return self.stop_reason
        try:
            await asyncio.wait_for(
                self._stop.wait(),
                timeout=self.remaining_run_seconds(),
            )
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
        coin_preview: CoinShadowPreviewService | None = None,
        coin_delivery: CoinShadowDeliveryService | None = None,
        coin_multi_preview: CoinShadowMultiPreview | None = None,
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
        self.coin_preview = coin_preview
        self.coin_delivery = coin_delivery
        self.coin_multi_preview = coin_multi_preview
        if (delivery is None) != (destination is None) or (delivery is None) != (
            delivery_authorization is None
        ):
            raise ValueError("ALIEXPRESS_SHADOW_DELIVERY_WIRING_INCOMPLETE")
        if (coin_preview is None) != (coin_delivery is None):
            raise ValueError("ALIEXPRESS_COIN_AUTO_WIRING_INCOMPLETE")
        if coin_multi_preview is not None and (coin_preview is None or coin_delivery is None):
            raise ValueError("ALIEXPRESS_COIN_MULTI_WIRING_INCOMPLETE")

    async def process(self, source_message_id: int) -> None:
        if self.coin_preview is not None:
            async with self.database.session() as session:
                source = await SourceMessageRepository(session).get(source_message_id)
                coin_candidate = source is not None and (
                    _has_coin_host(source.original_text)
                    or (
                        self.coin_multi_preview is not None
                        and len(tuple(URL_PATTERN.finditer(source.original_text))) > 1
                    )
                )
            if coin_candidate:
                await self._process_coin(source_message_id)
                return
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

    async def _process_coin(self, source_message_id: int) -> None:
        now = self.clock()
        async with self.database.session() as session:
            source = await SourceMessageRepository(session).claim(
                source_message_id,
                now=now,
                lease_until=now + timedelta(minutes=5),
                max_attempts=1,
            )
            if source is None:
                self.controller.record_skipped("COIN_SHADOW_SOURCE_ALREADY_CLAIMED")
                return
            message = IncomingMessage(
                platform=source.platform,
                message_id=int(source.message_id),
                channel_id=source.channel_id,
                occurred_at=source.occurred_at,
                original_text=source.original_text,
                links=tuple(ExtractedLink.from_dict(item) for item in source.links),
                surface_metadata=MessageSurfaceMetadata.from_dict(source.surface_metadata),
            )
        try:
            assert self.coin_preview is not None
            assert self.coin_delivery is not None
            assert self.destination is not None
            assert self.delivery_authorization is not None
            visible_urls = tuple(
                match.group(0).rstrip(TRAILING_PUNCTUATION)
                for match in URL_PATTERN.finditer(message.original_text)
            )
            if self.coin_multi_preview is not None and len(visible_urls) > 1:
                multi_preview = await self.coin_multi_preview.prepare(message)
                preview_id, cache_hit = multi_preview.preview_id, multi_preview.cache_hit
                delivery = await self.coin_delivery.deliver_multi_automatic(
                    preview_id,
                    self.destination,
                    authorization=self.delivery_authorization,
                    before_send=self.controller.before_send_message,
                )
            else:
                if len(visible_urls) != 1 or any(
                    link.url != visible_urls[0] for link in message.links
                ):
                    raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_VISIBLE_URL_COUNT_INVALID")
                if self.coin_multi_preview is not None:
                    collect_coin_occurrences(message, max_occurrences=1)
                    self.controller.record_coin_inputs(1, 1)
                preview = await self.coin_preview.prepare(message)
                preview_id, cache_hit = preview.preview_id, preview.cache_hit
                if self.coin_multi_preview is not None:
                    self.controller.record_coin_generation(cache_hit=cache_hit)
                    self.controller.record_coin_message(int(cache_hit), 1)
                delivery = await self.coin_delivery.deliver_automatic(
                    preview_id,
                    self.destination,
                    authorization=self.delivery_authorization,
                    before_send=self.controller.before_send_message,
                )
            if delivery.status != "sent":
                code = _safe_counter_code(delivery.error_code or "COIN_SHADOW_DELIVERY_FAILED")
                await self._finish_coin_source(source_message_id, error_code=code)
                self.controller.record_rejected(code, failed=True, processed=True)
                return
            async with self.database.session() as session:
                await SourceMessageRepository(session).complete(source_message_id, now=self.clock())
            self.controller.record_delivery_sent()
            self.controller.record_processed(cache_hit=cache_hit, preview_created=True)
        except CoinShadowPreviewRejected as exc:
            code = _safe_counter_code(str(exc))
            await self._finish_coin_source(source_message_id, error_code=code)
            self.controller.record_rejected(code, failed=False, processed=True)
        except asyncio.CancelledError:
            await self._finish_coin_source(
                source_message_id, error_code="COIN_SHADOW_PROCESSING_UNCERTAIN"
            )
            raise
        except Exception:
            await self._finish_coin_source(
                source_message_id, error_code="COIN_SHADOW_PROCESSING_UNCERTAIN"
            )
            self.controller.record_rejected(
                "COIN_SHADOW_PROCESSING_UNCERTAIN", failed=True, processed=True
            )

    async def _finish_coin_source(self, source_message_id: int, *, error_code: str) -> None:
        async with self.database.session() as session:
            await SourceMessageRepository(session).fail(
                source_message_id,
                retryable=False,
                max_attempts=1,
                next_attempt_at=None,
                error_code=error_code,
            )


def _has_coin_host(text: str) -> bool:
    for match in URL_PATTERN.finditer(text):
        try:
            host = urlsplit(match.group(0)).hostname
        except ValueError:
            continue
        if host in {"a.aliexpress.com", "s.click.aliexpress.com"}:
            return True
    return False


def _safe_counter_code(code: str) -> str:
    if code and all(
        character.isupper() or character.isdigit() or character == "_" for character in code
    ):
        return code
    return "AFFILIATE_SHADOW_REJECTED"
