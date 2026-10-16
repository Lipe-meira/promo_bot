"""Single-attempt coordinator for direct AliExpress coin-short generation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import delete, select, update

from promo_bot.affiliate.history_context import (
    AuditedGenerationCall,
    audited_generation_call,
    history_fingerprint,
    validate_history_storage,
)
from promo_bot.database.coin_shadow_repository import (
    CoinShadowClaim,
    CoinShadowClaimDisposition,
    CoinShadowEvidenceRepository,
    CoinShadowEvidenceState,
    CoinShadowFingerprintDomain,
    coin_shadow_fingerprint,
)
from promo_bot.database.history_repository import (
    AffiliateHistoryError,
    AffiliateLinkHistoryRepository,
    serialize_history_write,
)
from promo_bot.database.models import (
    AliExpressCoinShadowDeliveryModel,
    AliExpressCoinShadowEvidenceModel,
    AliExpressCoinShadowPreviewModel,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.coin_shadow import (
    parse_coin_shadow_link_generate,
    validate_coin_short,
)
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE, link_generate_payload
from promo_bot.providers.aliexpress.transport import AliExpressCompleteResponseError
from promo_bot.providers.base import ProviderError


class CoinShadowClient(Protocol):
    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True, repr=False)
class CoinShadowGenerationOutcome:
    evidence_id: int
    state: str
    cache_hit: bool
    promotion_link: str | None = None
    correlation_mode: str | None = None
    tracking_confirmed: bool = False
    attribution_unverified: bool = True
    route_preservation_manually_observed: bool = False
    error_code: str | None = None
    expires_at: datetime | None = None
    generation_id: str | None = None


class CoinShadowGenerationService:
    def __init__(
        self,
        database: AffiliateShadowDatabase,
        client: CoinShadowClient,
        *,
        app_secret: str,
        tracking_id: str,
        clock: Callable[[], datetime] | None = None,
        observer_wait_seconds: float = 1,
        observer_poll_seconds: float = 0.02,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.database = database
        self.client = client
        self.app_secret = app_secret
        self.tracking_id = tracking_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.observer_wait_seconds = observer_wait_seconds
        self.observer_poll_seconds = observer_poll_seconds

    async def generate(
        self,
        source_value: str,
        *,
        generation_request: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> CoinShadowGenerationOutcome:
        validate_coin_short(source_value)
        await validate_history_storage(
            self.database, real=isinstance(self.client, AliExpressAffiliateApiClient)
        )
        input_fp = coin_shadow_fingerprint(
            self.app_secret, source_value, CoinShadowFingerprintDomain.INPUT
        )
        tracking_fp = coin_shadow_fingerprint(
            self.app_secret, self.tracking_id, CoinShadowFingerprintDomain.TRACKING
        )
        now = self.clock()
        key_fp = history_fingerprint(self.app_secret, "key", "affiliate-history-key-v1")
        identity = f"coin:{input_fp}"
        generation_id: str | None = None
        # Recovery commits independently, so a subsequent safe refusal cannot roll
        # back the fact that an expired in-flight generation is now UNCERTAIN.
        async with self.database.session() as session:
            await AffiliateLinkHistoryRepository(session).recover(scope="shadow", now=now)
        async with self.database.session() as session:
            history = AffiliateLinkHistoryRepository(session)
            await serialize_history_write(session)
            await history.check_key_rotation(
                scope="shadow",
                key_fingerprint=key_fp,
                input_fingerprint=input_fp,
                tracking_fingerprint=tracking_fp,
            )
            rows = list(
                await session.scalars(
                    select(AliExpressCoinShadowEvidenceModel).where(
                        AliExpressCoinShadowEvidenceModel.input_fingerprint == input_fp,
                    )
                )
            )
            if generation_request is not None:
                request = await history.validate_request(
                    generation_request,
                    scope="shadow",
                    identity_key=identity,
                    legacy_kind="coin-evidence",
                )
                from promo_bot.database.coin_shadow_multi_repository import purge_multi_for_evidence

                assert request.legacy_id is not None
                await purge_multi_for_evidence(session, [request.legacy_id], now=now)
                preview_ids = select(AliExpressCoinShadowPreviewModel.id).where(
                    AliExpressCoinShadowPreviewModel.evidence_id == request.legacy_id,
                )
                await session.execute(
                    update(AliExpressCoinShadowDeliveryModel)
                    .where(
                        AliExpressCoinShadowDeliveryModel.preview_id.in_(preview_ids),
                    )
                    .values(preview_id=None, updated_at=now)
                )
                await session.execute(
                    delete(AliExpressCoinShadowPreviewModel).where(
                        AliExpressCoinShadowPreviewModel.evidence_id == request.legacy_id,
                    )
                )
                await session.execute(
                    delete(AliExpressCoinShadowEvidenceModel).where(
                        AliExpressCoinShadowEvidenceModel.id == request.legacy_id,
                    )
                )
                rows = [row for row in rows if row.id != request.legacy_id]
            for row in rows:
                if row.state == "UNCERTAIN":
                    raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED")
                if row.state == "READY":
                    await history.validate_coin_evidence(row)
            if not any(
                row.state == "GENERATING" and row.lease_until and row.lease_until > now
                for row in rows
            ):
                await history.check_identity(scope="shadow", identity_key=identity, now=now)
            claim = await CoinShadowEvidenceRepository(session).claim(
                input_fingerprint=input_fp,
                tracking_fingerprint=tracking_fp,
                promotion_link_type=0,
                now=now,
                lease_until=now + timedelta(minutes=5),
            )
            if claim.disposition is CoinShadowClaimDisposition.GENERATE:
                generation = await history.prepare(
                    scope="shadow",
                    identity_key=identity,
                    now=now,
                    lease_until=now + timedelta(minutes=5),
                    lease_token=claim.lease_token or "",
                    call_id=str(uuid4()),
                    call_ordinal=0,
                    input_fingerprint=input_fp,
                    tracking_fingerprint=tracking_fp,
                    key_fingerprint=key_fp,
                    request_id=generation_request,
                    origin=origin,
                )
                generation_id = generation.id
                evidence = await session.get(AliExpressCoinShadowEvidenceModel, claim.evidence_id)
                assert evidence is not None
                evidence.generation_id = generation_id

        if claim.disposition is CoinShadowClaimDisposition.READY:
            return self._outcome(claim, cache_hit=True)
        if claim.disposition is CoinShadowClaimDisposition.BLOCKED:
            return self._outcome(claim, cache_hit=False)
        if claim.disposition is CoinShadowClaimDisposition.IN_PROGRESS:
            return await self._observe(claim.evidence_id)
        assert generation_id is not None
        return await self._generate_winner(claim, source_value, generation_id)

    async def inspect(self, source_value: str) -> CoinShadowGenerationOutcome | None:
        """Read-only cache eligibility. Never claims, purges, recovers or calls TOP."""
        validate_coin_short(source_value)
        input_fp = coin_shadow_fingerprint(
            self.app_secret, source_value, CoinShadowFingerprintDomain.INPUT
        )
        tracking_fp = coin_shadow_fingerprint(
            self.app_secret, self.tracking_id, CoinShadowFingerprintDomain.TRACKING
        )
        key_fp = history_fingerprint(self.app_secret, "key", "affiliate-history-key-v1")
        now = self.clock()
        async with self.database.session() as session:
            history = AffiliateLinkHistoryRepository(session)
            await history.check_key_rotation(
                scope="shadow",
                key_fingerprint=key_fp,
                input_fingerprint=input_fp,
                tracking_fingerprint=tracking_fp,
            )
            rows = list(
                await session.scalars(
                    select(AliExpressCoinShadowEvidenceModel).where(
                        AliExpressCoinShadowEvidenceModel.input_fingerprint == input_fp
                    )
                )
            )
            for row in rows:
                if row.state == "UNCERTAIN" or (
                    row.state == "GENERATING"
                    and (row.lease_until is None or row.lease_until <= now)
                ):
                    raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED")
                if row.state == "GENERATING":
                    raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_IN_PROGRESS")
                if row.state == "READY":
                    await history.validate_coin_evidence(row)
            await history.check_identity(scope="shadow", identity_key=f"coin:{input_fp}", now=now)
            for row in rows:
                if row.tracking_fingerprint != tracking_fp or row.promotion_link_type != 0:
                    continue
                if row.state != "READY":
                    raise AffiliateHistoryError(row.error_code or f"ALIEXPRESS_COIN_{row.state}")
                if row.expires_at is not None and row.expires_at > now:
                    return self._outcome(
                        CoinShadowEvidenceRepository._claim_for_existing(row), cache_hit=True
                    )
        return None

    async def _generate_winner(
        self, claim: CoinShadowClaim, source_value: str, generation_id: str
    ) -> CoinShadowGenerationOutcome:
        try:
            payload = link_generate_payload(
                source_values=(source_value,),
                tracking_id=self.tracking_id,
                promotion_link_type=0,
                ship_to_country="BR",
            )
            call = AuditedGenerationCall(
                self.database, (generation_id,), (claim.lease_token or "",), payload, self.clock
            )
            with audited_generation_call(call):
                if not isinstance(self.client, AliExpressAffiliateApiClient):
                    await call.mark_started()
                response = await self.client.execute(LINK_GENERATE, payload)
        except AliExpressCompleteResponseError as exc:
            return await self._finish_rejected(claim, generation_id, exc.code)
        except asyncio.CancelledError:
            await self._finish_uncertain(
                claim, "ALIEXPRESS_COIN_GENERATION_CANCELLED", generation_id
            )
            raise
        except Exception:
            await self._finish_uncertain(
                claim, "ALIEXPRESS_COIN_GENERATION_UNCERTAIN", generation_id
            )
            return CoinShadowGenerationOutcome(
                evidence_id=claim.evidence_id,
                state=CoinShadowEvidenceState.UNCERTAIN.value,
                cache_hit=False,
                error_code="ALIEXPRESS_COIN_GENERATION_UNCERTAIN",
            )

        try:
            parsed = parse_coin_shadow_link_generate(
                response,
                sent_source_value=source_value,
                expected_tracking_id=self.tracking_id,
            )
        except (ProviderError, ValueError) as exc:
            code = exc.code if isinstance(exc, ProviderError) else str(exc)
            return await self._finish_rejected(claim, generation_id, code[:80])

        generated_at = self.clock()
        try:
            async with self.database.session() as session:
                await AffiliateLinkHistoryRepository(session).confirm(
                    generation_id,
                    now=generated_at,
                    generated_url=parsed.promotion_link,
                    expires_at=generated_at + timedelta(hours=24),
                    contract_version="coin-short-v1",
                    correlation_mode=parsed.correlation_mode.value,
                    validation_facts={
                        "tracking_exact": True,
                        "item_count": 1,
                        "promotion_link_validated": True,
                        "affiliate_host": urlsplit(parsed.promotion_link).hostname,
                        "route_preservation_manually_observed": False,
                    },
                )
                await CoinShadowEvidenceRepository(session).finish_ready(
                    claim.evidence_id,
                    claim.lease_token,
                    now=generated_at,
                    expires_at=generated_at + timedelta(hours=24),
                    promotion_link=parsed.promotion_link,
                    affiliate_host=urlsplit(parsed.promotion_link).hostname or "",
                    correlation_mode=parsed.correlation_mode,
                )
        except Exception:
            await self._finish_uncertain(
                claim, "ALIEXPRESS_COIN_PERSISTENCE_UNCERTAIN", generation_id
            )
            return CoinShadowGenerationOutcome(
                evidence_id=claim.evidence_id,
                state=CoinShadowEvidenceState.UNCERTAIN.value,
                cache_hit=False,
                error_code="ALIEXPRESS_COIN_PERSISTENCE_UNCERTAIN",
            )
        return CoinShadowGenerationOutcome(
            evidence_id=claim.evidence_id,
            state=CoinShadowEvidenceState.READY.value,
            cache_hit=False,
            promotion_link=parsed.promotion_link,
            correlation_mode=parsed.correlation_mode.value,
            tracking_confirmed=True,
            expires_at=generated_at + timedelta(hours=24),
            generation_id=generation_id,
        )

    async def _observe(self, evidence_id: int) -> CoinShadowGenerationOutcome:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.observer_wait_seconds
        while loop.time() < deadline:
            await asyncio.sleep(self.observer_poll_seconds)
            async with self.database.session() as session:
                observed = await CoinShadowEvidenceRepository(session).get(evidence_id)
                if observed is not None and observed.state is CoinShadowEvidenceState.READY:
                    evidence = await session.get(AliExpressCoinShadowEvidenceModel, evidence_id)
                    assert evidence is not None
                    await AffiliateLinkHistoryRepository(session).validate_coin_evidence(evidence)
            if observed is None:
                break
            if observed.state is not CoinShadowEvidenceState.GENERATING:
                return self._outcome(
                    observed,
                    cache_hit=observed.state is CoinShadowEvidenceState.READY,
                )
        return CoinShadowGenerationOutcome(
            evidence_id=evidence_id,
            state=CoinShadowEvidenceState.GENERATING.value,
            cache_hit=False,
            error_code="ALIEXPRESS_COIN_GENERATION_IN_PROGRESS",
        )

    async def _finish_rejected(
        self, claim: CoinShadowClaim, generation_id: str, code: str
    ) -> CoinShadowGenerationOutcome:
        try:
            async with self.database.session() as session:
                await AffiliateLinkHistoryRepository(session).fail(
                    (generation_id,), now=self.clock(), state="REJECTED", error_code=code
                )
                await CoinShadowEvidenceRepository(session).finish_review_required(
                    claim.evidence_id, claim.lease_token, now=self.clock(), error_code=code
                )
        except Exception:
            code = "ALIEXPRESS_COIN_PERSISTENCE_UNCERTAIN"
            await self._finish_uncertain(claim, code, generation_id)
            return CoinShadowGenerationOutcome(
                evidence_id=claim.evidence_id, state="UNCERTAIN", cache_hit=False, error_code=code
            )
        return CoinShadowGenerationOutcome(
            evidence_id=claim.evidence_id, state="REVIEW_REQUIRED", cache_hit=False, error_code=code
        )

    async def _finish_uncertain(
        self, claim: CoinShadowClaim, error_code: str, generation_id: str | None = None
    ) -> None:
        try:
            async with self.database.session() as session:
                if generation_id:
                    await AffiliateLinkHistoryRepository(session).fail(
                        (generation_id,), now=self.clock(), state="UNCERTAIN", error_code=error_code
                    )
                await CoinShadowEvidenceRepository(session).finish_uncertain(
                    claim.evidence_id,
                    claim.lease_token,
                    now=self.clock(),
                    error_code=error_code,
                )
        except Exception:
            pass

    @staticmethod
    def _outcome(claim: CoinShadowClaim, *, cache_hit: bool) -> CoinShadowGenerationOutcome:
        return CoinShadowGenerationOutcome(
            evidence_id=claim.evidence_id,
            state=claim.state.value,
            cache_hit=cache_hit,
            promotion_link=claim.promotion_link,
            correlation_mode=claim.correlation_mode,
            tracking_confirmed=claim.state is CoinShadowEvidenceState.READY,
            expires_at=claim.expires_at,
            generation_id=claim.generation_id,
            error_code=None
            if claim.state is CoinShadowEvidenceState.READY
            else f"ALIEXPRESS_COIN_{claim.state.value}",
        )
