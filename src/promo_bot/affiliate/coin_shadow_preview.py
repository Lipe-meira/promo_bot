"""Create short-lived coin-shadow previews without entering canonical flows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
from promo_bot.database.coin_shadow_repository import (
    CoinShadowFingerprintDomain,
    CoinShadowPreviewRepository,
    coin_shadow_fingerprint,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.providers.aliexpress.coin_shadow import validate_coin_short
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.parser import TRAILING_PUNCTUATION, URL_PATTERN

LOGGER = logging.getLogger("promo_bot.affiliate.coin_shadow_preview")


class CoinShadowPreviewRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class CoinShadowPreviewOutcome:
    preview_id: int
    evidence_id: int
    state: str
    cache_hit: bool
    correlation_mode: str
    tracking_confirmed: bool
    attribution_unverified: bool
    route_preservation_manually_observed: bool
    replacement_count: int
    content_expires_at: datetime
    rendered_text: str

    def explicit_output(self, *, include_content: bool) -> dict[str, object]:
        report: dict[str, object] = {
            "status": "coin_shadow_preview",
            "preview_id": self.preview_id,
            "evidence_id": self.evidence_id,
            "state": self.state,
            "cache_hit": self.cache_hit,
            "correlation_mode": self.correlation_mode,
            "tracking_confirmed": self.tracking_confirmed,
            "attribution_unverified": self.attribution_unverified,
            "route_preservation_manually_observed": (self.route_preservation_manually_observed),
            "replacement_count": self.replacement_count,
            "content_expires_at": self.content_expires_at.isoformat(),
            "content_included": include_content,
            "production_publication": False,
        }
        if include_content:
            report["rendered_text"] = self.rendered_text
        return report


class CoinShadowPreviewService:
    def __init__(
        self,
        database: AffiliateShadowDatabase,
        generation: CoinShadowGenerationService,
        *,
        app_secret: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.database = database
        self.generation = generation
        self.app_secret = app_secret
        self.clock = clock or (lambda: datetime.now(UTC))

    async def prepare(self, message: IncomingMessage) -> CoinShadowPreviewOutcome:
        if message.platform != "telegram":
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SOURCE_PLATFORM_INVALID")
        if not message.surface_metadata.is_safe_plain_text:
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SURFACE_UNSAFE")
        visible_urls = tuple(
            _trim_visible_url(match.group(0))
            for match in URL_PATTERN.finditer(message.original_text)
        )
        if len(visible_urls) != 1:
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_VISIBLE_URL_COUNT_INVALID")
        try:
            source_value = validate_coin_short(visible_urls[0])
        except ValueError:
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SHORT_INVALID") from None

        generation = await self.generation.generate(source_value)
        if (
            generation.state != "READY"
            or not generation.promotion_link
            or not generation.correlation_mode
            or not generation.tracking_confirmed
            or generation.expires_at is None
        ):
            raise CoinShadowPreviewRejected(
                generation.error_code or "ALIEXPRESS_COIN_GENERATION_NOT_READY"
            )
        rendered_text = message.original_text.replace(source_value, generation.promotion_link, 1)
        message_identity = f"{message.platform}\0{message.channel_id}\0{message.message_id}"
        message_fingerprint = coin_shadow_fingerprint(
            self.app_secret,
            message_identity,
            CoinShadowFingerprintDomain.MESSAGE,
        )
        now = self.clock()
        async with self.database.session() as session:
            preview, created = await CoinShadowPreviewRepository(session).save_ready(
                evidence_id=generation.evidence_id,
                source_message_fingerprint=message_fingerprint,
                rendered_text=rendered_text,
                now=now,
                content_expires_at=generation.expires_at,
            )
        LOGGER.info(
            "AliExpress coin-shadow preview prepared",
            extra={
                "stage": "aliexpress_coin_shadow_preview",
                "result": "preview_ready",
                "cache_hit": generation.cache_hit or not created,
                "preview_id": preview.id,
            },
        )
        return CoinShadowPreviewOutcome(
            preview_id=preview.id,
            evidence_id=generation.evidence_id,
            state="READY",
            cache_hit=generation.cache_hit or not created,
            correlation_mode=generation.correlation_mode,
            tracking_confirmed=True,
            attribution_unverified=True,
            route_preservation_manually_observed=False,
            replacement_count=1,
            content_expires_at=preview.content_expires_at,
            rendered_text=preview.rendered_text,
        )


def _trim_visible_url(value: str) -> str:
    while value and value[-1] in TRAILING_PUNCTUATION:
        value = value[:-1]
    return value
