"""Single-attempt coordinator for direct AliExpress coin-short generation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

from promo_bot.database.coin_shadow_repository import (
    CoinShadowClaim,
    CoinShadowClaimDisposition,
    CoinShadowEvidenceRepository,
    CoinShadowEvidenceState,
    CoinShadowFingerprintDomain,
    CoinShadowTransitionConflict,
    coin_shadow_fingerprint,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.providers.aliexpress.coin_shadow import parse_coin_shadow_link_generate
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE, link_generate_payload
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

    async def generate(self, source_value: str) -> CoinShadowGenerationOutcome:
        input_fp = coin_shadow_fingerprint(
            self.app_secret, source_value, CoinShadowFingerprintDomain.INPUT
        )
        tracking_fp = coin_shadow_fingerprint(
            self.app_secret, self.tracking_id, CoinShadowFingerprintDomain.TRACKING
        )
        now = self.clock()
        async with self.database.session() as session:
            claim = await CoinShadowEvidenceRepository(session).claim(
                input_fingerprint=input_fp,
                tracking_fingerprint=tracking_fp,
                promotion_link_type=0,
                now=now,
                lease_until=now + timedelta(minutes=5),
            )

        if claim.disposition is CoinShadowClaimDisposition.READY:
            return self._outcome(claim, cache_hit=True)
        if claim.disposition is CoinShadowClaimDisposition.BLOCKED:
            return self._outcome(claim, cache_hit=False)
        if claim.disposition is CoinShadowClaimDisposition.IN_PROGRESS:
            return await self._observe(claim.evidence_id)
        return await self._generate_winner(claim, source_value)

    async def _generate_winner(
        self, claim: CoinShadowClaim, source_value: str
    ) -> CoinShadowGenerationOutcome:
        try:
            response = await self.client.execute(
                LINK_GENERATE,
                link_generate_payload(
                    source_values=(source_value,),
                    tracking_id=self.tracking_id,
                    promotion_link_type=0,
                    ship_to_country="BR",
                ),
            )
        except asyncio.CancelledError:
            await self._finish_uncertain(claim, "ALIEXPRESS_COIN_GENERATION_CANCELLED")
            raise
        except Exception:
            await self._finish_uncertain(claim, "ALIEXPRESS_COIN_GENERATION_UNCERTAIN")
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
            async with self.database.session() as session:
                await CoinShadowEvidenceRepository(session).finish_review_required(
                    claim.evidence_id,
                    claim.lease_token,
                    now=self.clock(),
                    error_code=code[:80],
                )
            return CoinShadowGenerationOutcome(
                evidence_id=claim.evidence_id,
                state=CoinShadowEvidenceState.REVIEW_REQUIRED.value,
                cache_hit=False,
                error_code=code[:80],
            )

        generated_at = self.clock()
        try:
            async with self.database.session() as session:
                await CoinShadowEvidenceRepository(session).finish_ready(
                    claim.evidence_id,
                    claim.lease_token,
                    now=generated_at,
                    expires_at=generated_at + timedelta(hours=24),
                    promotion_link=parsed.promotion_link,
                    affiliate_host=urlsplit(parsed.promotion_link).hostname or "",
                    correlation_mode=parsed.correlation_mode,
                )
        except CoinShadowTransitionConflict:
            await self._finish_uncertain(claim, "ALIEXPRESS_COIN_PERSISTENCE_UNCERTAIN")
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
        )

    async def _observe(self, evidence_id: int) -> CoinShadowGenerationOutcome:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.observer_wait_seconds
        while loop.time() < deadline:
            await asyncio.sleep(self.observer_poll_seconds)
            async with self.database.session() as session:
                observed = await CoinShadowEvidenceRepository(session).get(evidence_id)
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

    async def _finish_uncertain(self, claim: CoinShadowClaim, error_code: str) -> None:
        try:
            async with self.database.session() as session:
                await CoinShadowEvidenceRepository(session).finish_uncertain(
                    claim.evidence_id,
                    claim.lease_token,
                    now=self.clock(),
                    error_code=error_code,
                )
        except CoinShadowTransitionConflict:
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
            error_code=None
            if claim.state is CoinShadowEvidenceState.READY
            else f"ALIEXPRESS_COIN_{claim.state.value}",
        )
