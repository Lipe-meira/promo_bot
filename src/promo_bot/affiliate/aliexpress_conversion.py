"""Fail-closed AliExpress link conversion for explicit dry-run previews."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.affiliate.history_context import (
    AuditedGenerationCall,
    audited_generation_call,
    history_fingerprint,
    history_scope,
    validate_history_storage,
)
from promo_bot.database.history_repository import (
    AffiliateHistoryError,
    AffiliateLinkHistoryRepository,
    serialize_history_write,
)
from promo_bot.database.models import (
    AffiliateLinkProofModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    AffiliateCandidateTransitionConflict,
    AffiliateOfferRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import AffiliateCandidateState, RelayLinkState, Store
from promo_bot.providers.aliexpress.client import (
    AliExpressAffiliateApiClient,
    AliExpressOperationTransport,
)
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE, link_generate_payload
from promo_bot.providers.aliexpress.parsing import (
    PROMOTION_LINK_UNAVAILABLE_REVIEW_REQUIRED,
    parse_link_generate,
)
from promo_bot.providers.aliexpress.transport import AliExpressCompleteResponseError
from promo_bot.providers.base import ProviderError
from promo_bot.relay.parser import TRAILING_PUNCTUATION, URL_PATTERN, extract_links
from promo_bot.relay.retry import BackoffPolicy
from promo_bot.security.aliexpress_short_links import is_supported_aliexpress_short_input
from promo_bot.stores.urls import (
    STORE_HOSTS,
    canonicalize_store_url,
    hostname_from_url,
    is_aliexpress_redirector_url,
)

LOGGER = logging.getLogger("promo_bot.affiliate.aliexpress_conversion")
ALIEXPRESS_LINK_PROOF_TTL = timedelta(hours=24)
PROMOTION_LINK_TYPE = 0
GENERATION_LEASE = timedelta(minutes=5)
MAX_GENERATION_ATTEMPTS = 3
DEFAULT_MAX_LINKS = 3


class AliExpressConversionRejected(RuntimeError):
    """A sanitized, stable reason for refusing an unsafe conversion."""

    def __init__(self, code: str, *, failed: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.failed = failed


class _AliExpressClaimBusy(RuntimeError):
    pass


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
class AliExpressLinkCorrelation:
    source_message_link_id: int
    product_id: str
    variation_key: str
    affiliate_proof_id: int
    affiliate_link: str
    ordinal: int
    occurrence_count: int
    cache_hit: bool

    def __repr__(self) -> str:
        return (
            "AliExpressLinkCorrelation("
            f"source_message_link_id={self.source_message_link_id}, "
            f"product_id={self.product_id!r}, variation_key={self.variation_key!r}, "
            f"ordinal={self.ordinal}, occurrence_count={self.occurrence_count}, "
            f"cache_hit={self.cache_hit}, affiliate_link=<redacted>)"
        )


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
    affiliate_proof_id: int | None = None
    correlations: tuple[AliExpressLinkCorrelation, ...] = ()
    history_use_id: str | None = None

    @property
    def affiliate_host(self) -> str:
        return urlsplit(self.affiliate_link).hostname or ""

    @property
    def all_cache_hit(self) -> bool:
        return bool(self.correlations) and all(item.cache_hit for item in self.correlations)

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
            "correlation_count": len(self.correlations),
            "all_cache_hit": self.all_cache_hit,
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


@dataclass(frozen=True, slots=True)
class _LinkContext:
    link_id: int
    input_url: str
    ordinal: int
    product_id: str
    variation_key: str
    identity_url: str
    generation_url: str
    candidate_id: int

    @property
    def identity(self) -> tuple[str, str]:
        return self.product_id, self.variation_key


@dataclass(frozen=True, slots=True)
class _Claim:
    context: _LinkContext
    started_at: datetime
    attempt_count: int
    generation_id: str
    lease_token: str


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
        api_client: AliExpressOperationTransport,
        *,
        app_key: str,
        app_secret: str,
        tracking_id: str,
        safety: AliExpressConversionSafety,
        clock: Callable[[], datetime] | None = None,
        proof_ttl: timedelta = ALIEXPRESS_LINK_PROOF_TTL,
        max_links: int = DEFAULT_MAX_LINKS,
        require_safe_surface: bool = False,
        contention_wait_seconds: float = 10.0,
        contention_poll_seconds: float = 0.05,
    ) -> None:
        if not isinstance(safety, AliExpressConversionSafety):
            raise ValueError("ALIEXPRESS_CONVERSION_SAFETY_GATE_CLOSED")
        if proof_ttl <= timedelta(0):
            raise ValueError("AliExpress proof TTL must be positive")
        if not 1 <= max_links <= DEFAULT_MAX_LINKS:
            raise ValueError("AliExpress max links must be between 1 and 3")
        if contention_wait_seconds < 0 or contention_poll_seconds <= 0:
            raise ValueError("AliExpress contention timing is invalid")
        self.database = database
        self.app_secret = app_secret
        self.api_client = api_client
        self.tracking_id = tracking_id
        self.tracking_fingerprint = tracking_config_fingerprint(
            app_key=app_key,
            app_secret=app_secret,
            tracking_id=tracking_id,
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.proof_ttl = proof_ttl
        self.max_links = max_links
        self.require_safe_surface = require_safe_surface
        self.contention_wait_seconds = contention_wait_seconds
        self.contention_poll_seconds = contention_poll_seconds
        self.backoff = BackoffPolicy(initial_seconds=60, maximum_seconds=300)

    async def convert(
        self,
        source_message_id: int,
        *,
        generation_request: str | None = None,
    ) -> AliExpressDryRunPreview:
        deadline = asyncio.get_running_loop().time() + self.contention_wait_seconds
        try:
            while True:
                try:
                    return await self._convert(
                        source_message_id, generation_request=generation_request
                    )
                except _AliExpressClaimBusy:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise AliExpressConversionRejected(
                            "ALIEXPRESS_CANDIDATE_BUSY_OR_EXHAUSTED"
                        ) from None
                    await asyncio.sleep(self.contention_poll_seconds)
        except AliExpressConversionRejected:
            raise
        except (AffiliateHistoryError, ValueError) as exc:
            code = str(exc)
            if not code.startswith("AFFILIATE_HISTORY_"):
                code = "ALIEXPRESS_CONVERSION_FAILED"
            raise AliExpressConversionRejected(code, failed=True) from None
        except Exception:
            # Driver errors may include bound SQL values containing an affiliate link.
            raise AliExpressConversionRejected(
                "ALIEXPRESS_CONVERSION_FAILED",
                failed=True,
            ) from None

    async def _convert(
        self,
        source_message_id: int,
        *,
        generation_request: str | None = None,
    ) -> AliExpressDryRunPreview:
        await validate_history_storage(
            self.database, real=isinstance(self.api_client, AliExpressAffiliateApiClient)
        )
        started_at = self.clock()
        scope = history_scope(self.database)
        async with self.database.session() as session:
            await AffiliateLinkHistoryRepository(session).recover(scope=scope, now=started_at)
        async with self.database.session() as session:
            history = AffiliateLinkHistoryRepository(session)
            await serialize_history_write(session)
            source = await session.get(SourceMessageModel, source_message_id)
            if source is None:
                raise AliExpressConversionRejected("ALIEXPRESS_SOURCE_MESSAGE_NOT_FOUND")
            if source.processing_status != "COMPLETED":
                raise AliExpressConversionRejected("ALIEXPRESS_SOURCE_NOT_COMPLETED")
            visible_urls = tuple(item.url for item in extract_links(source.original_text))
            self._validate_surface(source.surface_metadata, visible_urls=visible_urls)
            provided_urls = tuple(str(item.get("url", "")) for item in source.links)
            unique_aliexpress_urls = tuple(
                dict.fromkeys(url for url in visible_urls if _is_aliexpress_input(url))
            )
            if not unique_aliexpress_urls:
                if any(_is_aliexpress_input(url) for url in provided_urls):
                    raise AliExpressConversionRejected("ALIEXPRESS_TEXT_LINK_REQUIRED")
                raise AliExpressConversionRejected("ALIEXPRESS_LINK_NOT_FOUND")
            if len(unique_aliexpress_urls) > self.max_links:
                raise AliExpressConversionRejected("ALIEXPRESS_LINK_LIMIT_EXCEEDED")

            result = await session.execute(
                select(SourceMessageLinkModel)
                .where(SourceMessageLinkModel.source_message_id == source_message_id)
                .order_by(SourceMessageLinkModel.ordinal, SourceMessageLinkModel.id)
            )
            stored_by_url = {link.input_url: link for link in result.scalars()}
            contexts = tuple(
                [
                    await self._context_for_link(session, stored_by_url.get(url), url)
                    for url in unique_aliexpress_urls
                ]
            )
            identities = tuple(dict.fromkeys(context.identity for context in contexts))
            if len(identities) > self.max_links:
                raise AliExpressConversionRejected("ALIEXPRESS_LINK_LIMIT_EXCEEDED")
            product_ids = [identity[0] for identity in identities]
            if len(set(product_ids)) != len(product_ids):
                raise AliExpressConversionRejected("ALIEXPRESS_VARIATIONS_AMBIGUOUS")

            representative = {context.identity: context for context in reversed(contexts)}
            if generation_request is not None:
                if len(identities) != 1:
                    raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
                only_context = representative[identities[0]]
                await history.validate_request(
                    generation_request,
                    scope=scope,
                    identity_key=f"canonical:{only_context.candidate_id}",
                    legacy_kind="canonical-proof",
                )
            cached: dict[tuple[str, str], AffiliateLinkProofModel] = {}
            claims: list[_Claim] = []
            offers = AffiliateOfferRepository(session)
            candidates = AffiliateCandidateRepository(session)
            call_id = str(uuid4())
            for identity in identities:
                context = representative[identity]
                try:
                    await history.check_identity(
                        scope=scope,
                        identity_key=f"canonical:{context.candidate_id}",
                        now=started_at,
                    )
                except AffiliateHistoryError as exc:
                    if str(exc) == "AFFILIATE_HISTORY_GENERATION_IN_PROGRESS":
                        raise _AliExpressClaimBusy from None
                    raise
                existing = await session.scalar(
                    select(AffiliateLinkProofModel).where(
                        AffiliateLinkProofModel.candidate_id == context.candidate_id,
                        AffiliateLinkProofModel.provider == "aliexpress_official",
                    )
                )
                if existing is not None and generation_request is None:
                    await history.validate_canonical_proof(existing, scope=scope)
                if generation_request is not None:
                    continue
                proof = await offers.find_reusable_aliexpress_proof(
                    candidate_id=context.candidate_id,
                    source_external_product_id=context.product_id,
                    canonical_url=context.identity_url,
                    promotion_link_type=PROMOTION_LINK_TYPE,
                    tracking_fingerprint=self.tracking_fingerprint,
                    now=started_at,
                )
                if (
                    proof is not None
                    and _is_valid_affiliate_link(proof.short_link)
                    and started_at < proof.responded_at + self.proof_ttl
                ):
                    cached[identity] = proof

            for context in sorted(
                (representative[item] for item in identities if item not in cached),
                key=lambda item: item.candidate_id,
            ):
                claimed = await candidates.claim_for_generation(
                    context.candidate_id,
                    now=started_at,
                    lease_until=started_at + GENERATION_LEASE,
                    max_attempts=MAX_GENERATION_ATTEMPTS,
                    generation_request=generation_request,
                )
                if claimed is None:
                    raise _AliExpressClaimBusy
                lease_token = str(uuid4())
                generation = await history.prepare(
                    scope=scope,
                    identity_key=f"canonical:{context.candidate_id}",
                    now=started_at,
                    lease_until=started_at + GENERATION_LEASE,
                    lease_token=lease_token,
                    call_id=call_id,
                    call_ordinal=len(claims),
                    input_fingerprint=history_fingerprint(
                        self.app_secret,
                        context.generation_url,
                        "affiliate-history-canonical-input-v1",
                    ),
                    tracking_fingerprint=self.tracking_fingerprint,
                    key_fingerprint=history_fingerprint(
                        self.app_secret, "key", "affiliate-history-key-v1"
                    ),
                    origin={
                        "platform": source.platform,
                        "channel_id": source.channel_id,
                        "message_id": source.message_id,
                        "source_message_id": source.id,
                        "product_id": context.product_id,
                        "variation_key": context.variation_key,
                    },
                    request_id=generation_request,
                )
                claims.append(
                    _Claim(
                        context=context,
                        started_at=started_at,
                        attempt_count=claimed.attempt_count,
                        generation_id=generation.id,
                        lease_token=lease_token,
                    )
                )
            original_text = source.original_text

        # Cache selection and claim now share the serialized write transaction.
        # A second cache check could disagree about TTL and strand PREPARED intent.
        generated = await self._generate_batch(tuple(claims)) if claims else {}
        proofs = {**cached, **generated}
        if set(proofs) != set(identities):
            raise AliExpressConversionRejected("ALIEXPRESS_PROMOTION_LINK_SOURCE_MISSING")
        preview = _preview(
            source_message_id=source_message_id,
            original_text=original_text,
            contexts=contexts,
            proofs=proofs,
            cached_identities=frozenset(cached),
        )
        if len(preview.converted_text.encode("utf-16-le")) // 2 > 4096:
            raise AliExpressConversionRejected("SHADOW_MESSAGE_TOO_LONG")
        async with self.database.session() as session:
            history = AffiliateLinkHistoryRepository(session)
            ids = tuple(dict.fromkeys(proof.generation_id for proof in proofs.values()))
            if any(identifier is None for identifier in ids):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_LINK_MISSING")
            use = await history.record_use(
                scope=scope,
                kind="PREVIEW",
                generation_ids=tuple(str(identifier) for identifier in ids),
                now=self.clock(),
                cache_hit=bool(cached),
                cache_hits={
                    str(proof.generation_id): identity in cached
                    for identity, proof in proofs.items()
                },
                origin={"source_message_id": source_message_id},
                operational_kind="conversion-preview",
                operational_id=source_message_id,
            )
            preview = replace(preview, history_use_id=use.id)
        LOGGER.info(
            "AliExpress dry-run conversion prepared",
            extra={
                "message_id": str(source_message_id),
                "stage": "affiliate_conversion",
                "result": "preview_ready",
                "store": Store.ALIEXPRESS.value,
                "product_count": len(identities),
                "cache_hit": preview.all_cache_hit,
            },
        )
        return preview

    async def _context_for_link(
        self,
        session: AsyncSession,
        link: SourceMessageLinkModel | None,
        input_url: str,
    ) -> _LinkContext:
        if link is None:
            raise AliExpressConversionRejected("ALIEXPRESS_TEXT_LINK_REQUIRED")
        if link.source_kind not in {"TEXT", "ENTITY_URL"}:
            raise AliExpressConversionRejected("ALIEXPRESS_TEXT_LINK_REQUIRED")
        if is_aliexpress_redirector_url(input_url):
            if link.expanded_url is None:
                raise AliExpressConversionRejected(
                    link.reason_code or "ALIEXPRESS_SHORT_URL_UNRESOLVED"
                )
            canonical_input = link.expanded_url
        else:
            canonical_input = input_url
            parts = urlsplit(input_url)
            try:
                port = parts.port
            except ValueError:
                port = -1
            if (
                parts.scheme != "https"
                or parts.fragment
                or parts.username
                or parts.password
                or port is not None
                or (parts.hostname or "").casefold() not in STORE_HOSTS[Store.ALIEXPRESS]
                or re.fullmatch(r"/item/[0-9]+\.html", parts.path) is None
            ):
                raise AliExpressConversionRejected("ALIEXPRESS_CANONICAL_URL_REQUIRED")
        canonical = canonicalize_store_url(canonical_input)
        if (
            canonical.state is not RelayLinkState.PENDING_AFFILIATE
            or canonical.store is not Store.ALIEXPRESS
            or canonical.external_product_id is None
            or canonical.canonical_url is None
        ):
            raise AliExpressConversionRejected(canonical.reason_code)
        if (
            link.store != Store.ALIEXPRESS.value
            or link.external_product_id is None
            or link.canonical_url is None
            or link.affiliate_candidate_id is None
        ):
            raise AliExpressConversionRejected("ALIEXPRESS_CANONICAL_URL_REQUIRED")
        candidate = await AffiliateCandidateRepository(session).get(link.affiliate_candidate_id)
        if candidate is None or candidate.store != Store.ALIEXPRESS.value:
            raise AliExpressConversionRejected("ALIEXPRESS_CANDIDATE_NOT_FOUND")
        variation_key = candidate.variation_key
        if (
            candidate.external_product_id != link.external_product_id
            or candidate.canonical_url != link.canonical_url
            or canonical.external_product_id != link.external_product_id
            or canonical.canonical_url != link.canonical_url
            or (canonical.variation_key or "") != variation_key
        ):
            raise AliExpressConversionRejected("ALIEXPRESS_CANDIDATE_IDENTITY_MISMATCH")
        if (
            candidate.state == AffiliateCandidateState.MANUAL_REVIEW.value
            and candidate.error_code == PROMOTION_LINK_UNAVAILABLE_REVIEW_REQUIRED
        ):
            raise AliExpressConversionRejected(PROMOTION_LINK_UNAVAILABLE_REVIEW_REQUIRED)
        return _LinkContext(
            link_id=link.id,
            input_url=input_url,
            ordinal=link.ordinal,
            product_id=link.external_product_id,
            variation_key=variation_key,
            identity_url=link.canonical_url,
            generation_url=_generation_url(link.external_product_id, variation_key),
            candidate_id=candidate.id,
        )

    async def _generate_batch(
        self,
        claims: tuple[_Claim, ...],
    ) -> dict[tuple[str, str], AffiliateLinkProofModel]:
        source_values = tuple(claim.context.generation_url for claim in claims)
        received = False
        call: AuditedGenerationCall | None = None
        try:
            payload = link_generate_payload(
                source_values=source_values,
                tracking_id=self.tracking_id,
                promotion_link_type=PROMOTION_LINK_TYPE,
                ship_to_country="BR",
            )
            call = AuditedGenerationCall(
                self.database,
                tuple(c.generation_id for c in claims),
                tuple(c.lease_token for c in claims),
                payload,
                self.clock,
            )
            with audited_generation_call(call):
                if not isinstance(self.api_client, AliExpressAffiliateApiClient):
                    await call.mark_started()
                response = await self.api_client.execute(LINK_GENERATE, payload)
            received = True
            mappings = parse_link_generate(
                response,
                requested_source_values=source_values,
                expected_tracking_id=self.tracking_id,
            )
            responded_at = self.clock()
            if any(responded_at < claim.started_at for claim in claims):
                raise ValueError("ALIEXPRESS_CLOCK_MOVED_BACKWARDS")
            stored: dict[tuple[str, str], AffiliateLinkProofModel] = {}
            async with self.database.session() as session:
                candidates = AffiliateCandidateRepository(session)
                offers = AffiliateOfferRepository(session)
                for claim, mapping in zip(claims, mappings, strict=True):
                    await candidates.mark_affiliate_generated(
                        claim.context.candidate_id,
                        now=responded_at,
                        expected_started_at=claim.started_at,
                        expected_attempt_count=claim.attempt_count,
                    )
                    proof = await offers.upsert_aliexpress_link_proof(
                        candidate_id=claim.context.candidate_id,
                        requested_at=claim.started_at,
                        responded_at=responded_at,
                        source_external_product_id=claim.context.product_id,
                        canonical_url=claim.context.identity_url,
                        short_link=mapping.promotion_link,
                        promotion_link_type=PROMOTION_LINK_TYPE,
                        tracking_fingerprint=self.tracking_fingerprint,
                        expires_at=responded_at + self.proof_ttl,
                    )
                    await AffiliateLinkHistoryRepository(session).confirm(
                        claim.generation_id,
                        now=responded_at,
                        generated_url=mapping.promotion_link,
                        expires_at=responded_at + self.proof_ttl,
                        contract_version=proof.contract_version,
                        correlation_mode="PRODUCT_ID_CORRELATED",
                        validation_facts={
                            "tracking_exact": True,
                            "product_correlated": True,
                            "item_count": len(claims),
                            "promotion_link_validated": True,
                            "affiliate_host": urlsplit(mapping.promotion_link).hostname,
                        },
                    )
                    proof.generation_id = claim.generation_id
                    stored[claim.context.identity] = proof
            return stored
        except AffiliateCandidateTransitionConflict:
            await self._record_history_failure(
                claims, state="UNCERTAIN", code="AFFILIATE_HISTORY_GENERATION_LEASE_LOST"
            )
            raise AliExpressConversionRejected("ALIEXPRESS_GENERATION_LEASE_LOST") from None
        except ProviderError as exc:
            received = received or isinstance(exc, AliExpressCompleteResponseError)
            await self._record_history_failure(
                claims, state="REJECTED" if received else "UNCERTAIN", code=exc.code
            )
            if not received:
                exc = ProviderError(exc.code, retryable=False, manual_review=True)
            await self._record_failures(claims, exc)
            raise AliExpressConversionRejected(exc.code, failed=True) from None
        except (TypeError, ValueError):
            error = ProviderError(
                "ALIEXPRESS_RESPONSE_INCOMPATIBLE",
                retryable=False,
                manual_review=True,
            )
            await self._record_history_failure(
                claims, state="REJECTED" if received else "UNCERTAIN", code=error.code
            )
            await self._record_failures(claims, error)
            raise AliExpressConversionRejected(error.code, failed=True) from None
        except Exception:
            error = ProviderError(
                "ALIEXPRESS_CONVERSION_FAILED", retryable=False, manual_review=True
            )
            await self._record_history_failure(claims, state="UNCERTAIN", code=error.code)
            await self._record_failures(claims, error)
            raise AliExpressConversionRejected(error.code, failed=True) from None
        except asyncio.CancelledError:
            await self._record_history_failure(
                claims, state="UNCERTAIN", code="AFFILIATE_HISTORY_GENERATION_CANCELLED"
            )
            raise

    async def _record_history_failure(
        self, claims: Sequence[_Claim], *, state: str, code: str
    ) -> None:
        try:
            async with self.database.session() as session:
                await AffiliateLinkHistoryRepository(session).fail(
                    tuple(c.generation_id for c in claims),
                    now=self.clock(),
                    state=state,
                    error_code=code,
                )
        except Exception:
            # CALL_STARTED remains durable and recovery blocks any automatic retry.
            pass

    async def _record_failures(
        self,
        claims: Sequence[_Claim],
        error: ProviderError,
    ) -> None:
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
        try:
            async with self.database.session() as session:
                repository = AffiliateCandidateRepository(session)
                for claim in claims:
                    await repository.fail(
                        claim.context.candidate_id,
                        now=now,
                        target_state=target,
                        next_attempt_at=(
                            self.backoff.next_attempt_at(now, claim.attempt_count)
                            if error.retryable
                            else None
                        ),
                        error_code=error.code,
                        expected_started_at=claim.started_at,
                        expected_attempt_count=claim.attempt_count,
                    )
        except AffiliateCandidateTransitionConflict:
            raise AliExpressConversionRejected("ALIEXPRESS_GENERATION_LEASE_LOST") from None

    def _validate_surface(
        self,
        metadata: dict[str, object],
        *,
        visible_urls: Sequence[str] = (),
    ) -> None:
        if not self.require_safe_surface:
            return
        if metadata.get("legacy_unknown"):
            raise AliExpressConversionRejected("ALIEXPRESS_MESSAGE_SURFACE_UNKNOWN")
        unsafe = (
            "has_buttons",
            "has_caption",
            "has_custom_emoji",
            "has_hidden_links",
            "has_media",
        )
        if any(bool(metadata.get(key)) for key in unsafe) or metadata.get(
            "unsupported_entity_types"
        ):
            raise AliExpressConversionRejected("ALIEXPRESS_MESSAGE_SURFACE_UNSAFE")
        if metadata.get("has_web_page_preview") and not any(
            _is_supported_visible_aliexpress_url(url) for url in visible_urls
        ):
            raise AliExpressConversionRejected("ALIEXPRESS_MESSAGE_SURFACE_UNSAFE")

    def __repr__(self) -> str:
        return (
            "AliExpressMessageConversionService("
            "tracking_id=<redacted>, tracking_fingerprint=<redacted>, dry_run=True)"
        )

    __str__ = __repr__


def _is_aliexpress_input(url: str) -> bool:
    host = hostname_from_url(url)
    return bool(host and (host == "aliexpress.com" or host.endswith(".aliexpress.com")))


def _is_supported_visible_aliexpress_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").casefold()
    if (
        parts.scheme.casefold() != "https"
        or parts.username is not None
        or parts.password is not None
    ):
        return False
    if is_supported_aliexpress_short_input(url):
        return True
    if port not in {None, 443} or host not in STORE_HOSTS[Store.ALIEXPRESS]:
        return False
    result = canonicalize_store_url(url)
    return result.store is Store.ALIEXPRESS and result.state is RelayLinkState.PENDING_AFFILIATE


def _is_valid_affiliate_link(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return bool(
        parts.scheme == "https"
        and parts.hostname == "s.click.aliexpress.com"
        and not parts.username
        and not parts.password
        and port is None
        and not parts.fragment
    )


def _generation_url(product_id: str, variation_key: str) -> str:
    query = ""
    if variation_key:
        key, value = variation_key.split(":", 1)
        if key != "sku_id" or not value.isdigit():
            raise AliExpressConversionRejected("ALIEXPRESS_VARIATION_AMBIGUOUS")
        query = urlencode({"sku_id": value})
    return urlunsplit(("https", "pt.aliexpress.com", f"/item/{product_id}.html", query, ""))


def _preview(
    *,
    source_message_id: int,
    original_text: str,
    contexts: tuple[_LinkContext, ...],
    proofs: dict[tuple[str, str], AffiliateLinkProofModel],
    cached_identities: frozenset[tuple[str, str]],
) -> AliExpressDryRunPreview:
    replacements = {context.input_url: proofs[context.identity].short_link for context in contexts}
    converted_text, replacement_count, counts = render_affiliate_link_replacements(
        original_text,
        replacements,
    )
    correlations = tuple(
        AliExpressLinkCorrelation(
            source_message_link_id=context.link_id,
            product_id=context.product_id,
            variation_key=context.variation_key,
            affiliate_proof_id=proofs[context.identity].id,
            affiliate_link=proofs[context.identity].short_link,
            ordinal=context.ordinal,
            occurrence_count=counts[context.input_url],
            cache_hit=context.identity in cached_identities,
        )
        for context in contexts
    )
    first = correlations[0]
    return AliExpressDryRunPreview(
        source_message_id=source_message_id,
        product_id=first.product_id,
        variation_key=first.variation_key,
        promotion_link_type=PROMOTION_LINK_TYPE,
        converted_text=converted_text,
        affiliate_link=first.affiliate_link,
        replacement_count=replacement_count,
        cache_hit=all(item.cache_hit for item in correlations),
        affiliate_proof_id=first.affiliate_proof_id,
        correlations=correlations,
    )


def render_affiliate_link_replacements(
    original_text: str,
    replacements: Mapping[str, str],
) -> tuple[str, int, Counter[str]]:
    """Rebuild visible text from exact URL tokens without interpreting markup."""

    replacement_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replacement_count
        raw = match.group(0)
        source_url = raw.rstrip(TRAILING_PUNCTUATION)
        affiliate_link = replacements.get(source_url)
        if affiliate_link is None:
            return raw
        replacement_count += 1
        return affiliate_link + raw[len(source_url) :]

    converted_text = URL_PATTERN.sub(replace, original_text)
    counts: Counter[str] = Counter(
        match.group(0).rstrip(TRAILING_PUNCTUATION)
        for match in URL_PATTERN.finditer(original_text)
        if match.group(0).rstrip(TRAILING_PUNCTUATION) in replacements
    )
    return converted_text, replacement_count, counts
