"""Fail-closed AliExpress link conversion for explicit dry-run previews."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from promo_bot.database.models import SourceMessageLinkModel, SourceMessageModel
from promo_bot.database.repositories import AffiliateCandidateRepository, AffiliateOfferRepository
from promo_bot.database.session import Database
from promo_bot.domain.enums import AffiliateCandidateState, Store
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE, link_generate_payload
from promo_bot.providers.aliexpress.parsing import parse_link_generate
from promo_bot.providers.base import ProviderError
from promo_bot.stores.urls import hostname_from_url, store_for_host

LOGGER = logging.getLogger("promo_bot.affiliate.aliexpress_conversion")
ALIEXPRESS_LINK_PROOF_TTL = timedelta(hours=24)
PROMOTION_LINK_TYPE = 0
GENERATION_LEASE = timedelta(minutes=5)
MAX_GENERATION_ATTEMPTS = 3


class AliExpressConversionRejected(RuntimeError):
    """A sanitized, stable reason for refusing an unsafe conversion."""


@dataclass(frozen=True, slots=True)
class AliExpressConversionSafety:
    dry_run: bool
    publish_real_deals: bool
    publish_without_affiliate: bool
    search_enabled: bool

    def __post_init__(self) -> None:
        if (
            not self.dry_run
            or self.publish_real_deals
            or self.publish_without_affiliate
            or self.search_enabled
        ):
            raise ValueError("ALIEXPRESS_CONVERSION_SAFETY_GATE_CLOSED")


@dataclass(frozen=True, slots=True, repr=False)
class AliExpressDryRunPreview:
    source_message_id: int
    product_id: str
    variation_key: str
    promotion_link_type: int
    converted_text: str
    affiliate_link: str
    replacement_count: int
    cache_hit: bool

    @property
    def affiliate_host(self) -> str:
        return urlsplit(self.affiliate_link).hostname or ""

    def explicit_output(self) -> dict[str, object]:
        """Return sensitive preview content only for the explicit local CLI command."""
        return {
            "status": "preview",
            "dry_run": True,
            "source_message_id": self.source_message_id,
            "product_id": self.product_id,
            "variation_key": self.variation_key,
            "promotion_link_type": self.promotion_link_type,
            "converted_text": self.converted_text,
            "affiliate_link": self.affiliate_link,
            "affiliate_host": self.affiliate_host,
            "replacement_count": self.replacement_count,
            "cache_hit": self.cache_hit,
            "telegram_delivery": False,
            "database_deal_created": False,
        }

    def __repr__(self) -> str:
        return (
            "AliExpressDryRunPreview("
            f"source_message_id={self.source_message_id}, product_id={self.product_id!r}, "
            "converted_text=<redacted>, affiliate_link=<redacted>, "
            f"cache_hit={self.cache_hit})"
        )

    __str__ = __repr__


def tracking_config_fingerprint(*, app_key: str, app_secret: str, tracking_id: str) -> str:
    """Create a keyed, non-reversible cache dimension without exposing tracking config."""
    if not app_key or not app_secret or not tracking_id:
        raise ValueError("AliExpress tracking configuration must be complete")
    message = f"aliexpress-link-generate\0{app_key}\0{tracking_id}".encode()
    return hmac.new(app_secret.encode(), message, hashlib.sha256).hexdigest()


class AliExpressMessageConversionService:
    """Convert one persisted Telegram message without publishing or creating a deal."""

    def __init__(
        self,
        database: Database,
        api_client: AliExpressAffiliateApiClient,
        *,
        app_key: str,
        app_secret: str,
        tracking_id: str,
        safety: AliExpressConversionSafety,
        clock: Callable[[], datetime] | None = None,
        proof_ttl: timedelta = ALIEXPRESS_LINK_PROOF_TTL,
    ) -> None:
        del safety
        if proof_ttl <= timedelta(0):
            raise ValueError("AliExpress proof TTL must be positive")
        self.database = database
        self.api_client = api_client
        self.tracking_id = tracking_id
        self.tracking_fingerprint = tracking_config_fingerprint(
            app_key=app_key,
            app_secret=app_secret,
            tracking_id=tracking_id,
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.proof_ttl = proof_ttl

    async def convert(self, source_message_id: int) -> AliExpressDryRunPreview:
        now = self.clock()
        async with self.database.session() as session:
            source = await session.get(SourceMessageModel, source_message_id)
            if source is None:
                raise AliExpressConversionRejected("ALIEXPRESS_SOURCE_MESSAGE_NOT_FOUND")
            result = await session.execute(
                select(SourceMessageLinkModel)
                .where(SourceMessageLinkModel.source_message_id == source_message_id)
                .order_by(SourceMessageLinkModel.ordinal, SourceMessageLinkModel.id)
            )
            links = list(result.scalars())
            aliexpress_links = [link for link in links if _is_aliexpress_input(link.input_url)]
            if not aliexpress_links:
                raise AliExpressConversionRejected("ALIEXPRESS_LINK_NOT_FOUND")
            if len(aliexpress_links) != 1:
                raise AliExpressConversionRejected("ALIEXPRESS_MULTIPLE_LINKS_AMBIGUOUS")
            link = aliexpress_links[0]
            if (
                link.store != Store.ALIEXPRESS.value
                or link.external_product_id is None
                or link.canonical_url is None
                or link.affiliate_candidate_id is None
            ):
                reason = link.reason_code or "ALIEXPRESS_CANONICAL_URL_REQUIRED"
                raise AliExpressConversionRejected(reason)
            product_id = link.external_product_id
            canonical_url = link.canonical_url
            variation_key = ""
            candidate = await AffiliateCandidateRepository(session).get(link.affiliate_candidate_id)
            if candidate is None or candidate.store != Store.ALIEXPRESS.value:
                raise AliExpressConversionRejected("ALIEXPRESS_CANDIDATE_NOT_FOUND")
            variation_key = candidate.variation_key
            proof = await AffiliateOfferRepository(session).find_reusable_aliexpress_proof(
                candidate_id=candidate.id,
                source_external_product_id=product_id,
                canonical_url=canonical_url,
                promotion_link_type=PROMOTION_LINK_TYPE,
                tracking_fingerprint=self.tracking_fingerprint,
                now=now,
            )
            if proof is not None and _is_valid_affiliate_link(proof.short_link):
                return _preview(
                    source_message_id=source_message_id,
                    product_id=product_id,
                    variation_key=variation_key,
                    original_text=source.original_text,
                    source_url=link.input_url,
                    affiliate_link=proof.short_link,
                    cache_hit=True,
                )
            claimed = await AffiliateCandidateRepository(session).claim_for_generation(
                candidate.id,
                now=now,
                lease_until=now + GENERATION_LEASE,
                max_attempts=MAX_GENERATION_ATTEMPTS,
            )
            if claimed is None:
                raise AliExpressConversionRejected("ALIEXPRESS_CANDIDATE_BUSY_OR_EXHAUSTED")
            candidate_id = candidate.id
            original_text = source.original_text
            source_url = link.input_url

        try:
            response = await self.api_client.execute(
                LINK_GENERATE,
                link_generate_payload(
                    source_values=(canonical_url,),
                    tracking_id=self.tracking_id,
                    promotion_link_type=PROMOTION_LINK_TYPE,
                    ship_to_country="BR",
                ),
            )
            mappings = parse_link_generate(response, requested_source_values=(canonical_url,))
            if len(mappings) != 1:
                raise ProviderError("ALIEXPRESS_PROMOTION_LINK_COUNT_MISMATCH", retryable=False)
            affiliate_link = mappings[0].promotion_link
            responded_at = self.clock()
            async with self.database.session() as session:
                await AffiliateOfferRepository(session).upsert_aliexpress_link_proof(
                    candidate_id=candidate_id,
                    requested_at=now,
                    responded_at=responded_at,
                    source_external_product_id=product_id,
                    canonical_url=canonical_url,
                    short_link=affiliate_link,
                    promotion_link_type=PROMOTION_LINK_TYPE,
                    tracking_fingerprint=self.tracking_fingerprint,
                    expires_at=responded_at + self.proof_ttl,
                )
                await AffiliateCandidateRepository(session).mark_affiliate_generated(
                    candidate_id,
                    now=responded_at,
                )
        except ProviderError as exc:
            await self._record_failure(candidate_id, exc)
            raise AliExpressConversionRejected(exc.code) from exc
        except (TypeError, ValueError) as exc:
            error = ProviderError(
                "ALIEXPRESS_RESPONSE_INCOMPATIBLE",
                retryable=False,
                manual_review=True,
            )
            await self._record_failure(candidate_id, error)
            raise AliExpressConversionRejected(error.code) from exc

        LOGGER.info(
            "AliExpress dry-run conversion prepared",
            extra={
                "message_id": str(source_message_id),
                "stage": "affiliate_conversion",
                "result": "preview_ready",
                "store": Store.ALIEXPRESS.value,
                "product_id": product_id,
                "cache_hit": False,
            },
        )
        return _preview(
            source_message_id=source_message_id,
            product_id=product_id,
            variation_key=variation_key,
            original_text=original_text,
            source_url=source_url,
            affiliate_link=affiliate_link,
            cache_hit=False,
        )

    async def _record_failure(self, candidate_id: int, error: ProviderError) -> None:
        now = self.clock()
        target = (
            AffiliateCandidateState.FAILED_RETRYABLE
            if error.retryable
            else (
                AffiliateCandidateState.MANUAL_REVIEW
                if error.manual_review
                else AffiliateCandidateState.FAILED_PERMANENT
            )
        )
        async with self.database.session() as session:
            await AffiliateCandidateRepository(session).fail(
                candidate_id,
                now=now,
                target_state=target,
                next_attempt_at=now + timedelta(minutes=1) if error.retryable else None,
                error_code=error.code,
            )

    def __repr__(self) -> str:
        return (
            "AliExpressMessageConversionService("
            "tracking_id=<redacted>, tracking_fingerprint=<redacted>, dry_run=True)"
        )

    __str__ = __repr__


def _is_aliexpress_input(url: str) -> bool:
    host = hostname_from_url(url)
    return bool(
        host
        and (
            store_for_host(host) is Store.ALIEXPRESS
            or host
            in {
                "a.aliexpress.com",
                "s.click.aliexpress.com",
            }
        )
    )


def _is_valid_affiliate_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname == "s.click.aliexpress.com"


def _preview(
    *,
    source_message_id: int,
    product_id: str,
    variation_key: str,
    original_text: str,
    source_url: str,
    affiliate_link: str,
    cache_hit: bool,
) -> AliExpressDryRunPreview:
    replacement_count = original_text.count(source_url)
    converted_text = original_text.replace(source_url, affiliate_link)
    return AliExpressDryRunPreview(
        source_message_id=source_message_id,
        product_id=product_id,
        variation_key=variation_key,
        promotion_link_type=PROMOTION_LINK_TYPE,
        converted_text=converted_text,
        affiliate_link=affiliate_link,
        replacement_count=replacement_count,
        cache_hit=cache_hit,
    )
