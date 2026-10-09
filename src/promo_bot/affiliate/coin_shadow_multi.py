"""Strict occurrence contract for the opt-in multi-short shadow path."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from promo_bot.affiliate.aliexpress_conversion import render_affiliate_link_replacements
from promo_bot.affiliate.coin_shadow_generation import (
    CoinShadowGenerationOutcome,
    CoinShadowGenerationService,
)
from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected
from promo_bot.database.coin_shadow_multi_repository import (
    CoinMultiOccurrenceInput,
    CoinShadowMultiPreviewRepository,
)
from promo_bot.database.coin_shadow_repository import (
    CoinShadowFingerprintDomain,
    coin_shadow_fingerprint,
)
from promo_bot.database.history_repository import (
    AffiliateHistoryError,
    AffiliateLinkHistoryRepository,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.domain.enums import LinkSource
from promo_bot.providers.aliexpress.coin_shadow import validate_coin_short
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.parser import TRAILING_PUNCTUATION, URL_PATTERN


@dataclass(frozen=True, slots=True, repr=False)
class CoinShortOccurrence:
    ordinal: int
    start: int
    end: int
    source_value: str


def collect_coin_occurrences(
    message: IncomingMessage, *, max_occurrences: int
) -> tuple[CoinShortOccurrence, ...]:
    """Validate the entire surface before any generation or operational claim."""
    if not 1 <= max_occurrences <= 3:
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_OCCURRENCE_LIMIT_INVALID")
    if message.platform != "telegram":
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SOURCE_PLATFORM_INVALID")
    if not message.surface_metadata.is_safe_plain_text:
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SURFACE_UNSAFE")
    matches = tuple(URL_PATTERN.finditer(message.original_text))
    if not 1 <= len(matches) <= max_occurrences:
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_VISIBLE_URL_COUNT_INVALID")
    occurrences: list[CoinShortOccurrence] = []
    for ordinal, match in enumerate(matches):
        literal = match.group(0).rstrip(TRAILING_PUNCTUATION)
        try:
            validate_coin_short(literal)
        except ValueError:
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SHORT_INVALID") from None
        occurrences.append(
            CoinShortOccurrence(ordinal, match.start(), match.start() + len(literal), literal)
        )
    visible = {row.source_value for row in occurrences}
    if (
        any(link.source not in {LinkSource.TEXT, LinkSource.ENTITY_URL} for link in message.links)
        or {link.url for link in message.links} != visible
    ):
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_URL_SURFACE_MISMATCH")
    return tuple(occurrences)


class CoinMultiBudget(Protocol):
    def remaining_api_calls(self) -> int: ...
    def remaining_send_messages(self) -> int: ...
    def record_coin_inputs(self, occurrences: int, distinct: int) -> None: ...
    def record_coin_generation(self, *, cache_hit: bool) -> None: ...
    def record_coin_message(self, cache_inputs: int, distinct: int) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class CoinShadowMultiPreviewOutcome:
    preview_id: int
    cache_hit: bool
    replacement_count: int
    content_expires_at: datetime
    rendered_text: str
    correlations: tuple[CoinMultiOccurrenceInput, ...]


class CoinShadowMultiPreview:
    """Whole-message orchestration; only the existing singleton generator touches TOP."""

    def __init__(
        self,
        database: AffiliateShadowDatabase,
        generation: CoinShadowGenerationService,
        *,
        app_secret: str,
        budget: CoinMultiBudget,
        max_occurrences: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        if not 1 <= max_occurrences <= 3:
            raise ValueError("ALIEXPRESS_COIN_OCCURRENCE_LIMIT_INVALID")
        if generation.observer_wait_seconds != 0:
            raise ValueError("ALIEXPRESS_COIN_MULTI_OBSERVER_WAIT_FORBIDDEN")
        self.database, self.generation = database, generation
        self.app_secret, self.budget, self.max_occurrences = app_secret, budget, max_occurrences
        self.clock = clock or (lambda: datetime.now(UTC))

    async def prepare(self, message: IncomingMessage) -> CoinShadowMultiPreviewOutcome:
        occurrences = collect_coin_occurrences(message, max_occurrences=self.max_occurrences)
        if len(occurrences) < 2:
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_MULTI_OCCURRENCES_REQUIRED")
        inputs = tuple(dict.fromkeys(row.source_value for row in occurrences))
        self.budget.record_coin_inputs(len(occurrences), len(inputs))
        try:
            # State recovery is distinct from read-only inspection; never resumes messages.
            async with self.database.session() as session:
                await AffiliateLinkHistoryRepository(session).recover(
                    scope="shadow", now=self.clock()
                )
            inspected = [await self.generation.inspect(value) for value in inputs]
            if self.budget.remaining_send_messages() < 1:
                raise CoinShadowPreviewRejected("SHADOW_SEND_MESSAGE_LIMIT_REACHED")
            if sum(row is None for row in inspected) > self.budget.remaining_api_calls():
                raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT")
            generated: dict[str, CoinShadowGenerationOutcome] = {}
            for value in inputs:
                # Probe is not a reservation: expiry/concurrent claims are rechecked.
                current = await self.generation.inspect(value)
                if current is None and self.budget.remaining_api_calls() < 1:
                    raise CoinShadowPreviewRejected(
                        "ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT"
                    )
                outcome = await self.generation.generate(
                    value,
                    origin={
                        "platform": message.platform,
                        "channel_id": message.channel_id,
                        "message_id": str(message.message_id),
                        "occurred_at": message.occurred_at.isoformat(),
                    },
                )
                if (
                    outcome.state != "READY"
                    or not outcome.promotion_link
                    or not outcome.generation_id
                    or not outcome.tracking_confirmed
                    or outcome.expires_at is None
                ):
                    raise CoinShadowPreviewRejected(
                        outcome.error_code or "ALIEXPRESS_COIN_GENERATION_NOT_READY"
                    )
                generated[value] = outcome
                self.budget.record_coin_generation(cache_hit=outcome.cache_hit)
            replacements = {
                value: outcome.promotion_link
                for value, outcome in generated.items()
                if outcome.promotion_link is not None
            }
            rendered, count, counts = render_affiliate_link_replacements(
                message.original_text, replacements
            )
            if count != len(occurrences) or counts != Counter(
                row.source_value for row in occurrences
            ):
                raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_REPLACEMENT_MISMATCH")
            if len(rendered.encode("utf-16-le")) // 2 > 4096:
                raise CoinShadowPreviewRejected("COIN_SHADOW_MESSAGE_TOO_LONG")
            correlations = tuple(
                CoinMultiOccurrenceInput(
                    ordinal=row.ordinal,
                    start=row.start,
                    end=row.end,
                    evidence_id=generated[row.source_value].evidence_id,
                    generation_id=generated[row.source_value].generation_id or "",
                )
                for row in occurrences
            )
            fingerprint = coin_shadow_fingerprint(
                self.app_secret,
                f"{message.platform}\0{message.channel_id}\0{message.message_id}",
                CoinShadowFingerprintDomain.MESSAGE,
            )
            async with self.database.session() as session:
                preview, _ = await CoinShadowMultiPreviewRepository(session).save_ready(
                    source_message_fingerprint=fingerprint,
                    rendered_text=rendered,
                    occurrences=correlations,
                    cache_hits={
                        row.generation_id: row.cache_hit
                        for row in generated.values()
                        if row.generation_id is not None
                    },
                    now=self.clock(),
                )
            hits = sum(row.cache_hit for row in generated.values())
            self.budget.record_coin_message(hits, len(inputs))
            return CoinShadowMultiPreviewOutcome(
                preview.id,
                hits == len(inputs),
                count,
                preview.content_expires_at,
                rendered,
                correlations,
            )
        except AffiliateHistoryError as exc:
            raise CoinShadowPreviewRejected(str(exc)) from None
