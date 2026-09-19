"""Manual and bounded SKU refinement of completed discovery results."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select

from promo_bot.database.aliexpress_discovery_sku_repository import (
    SkuClaimDisposition,
    SkuRefinementRepository,
    sku_query_fingerprint,
)
from promo_bot.database.models import AliExpressDiscoveryRunModel, AliExpressDiscoveryRunResultModel
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.sku_matcher import SkuMatchResult, match_discovery_skus
from promo_bot.providers.aliexpress.discovery_sku import DiscoverySkuPage
from promo_bot.providers.base import ProviderError

LEASE_DURATION = timedelta(seconds=60)
ITEM_ERROR_CODES = frozenset(
    {
        "ALIEXPRESS_DISCOVERY_SKU_PRODUCT_MISMATCH",
        "ALIEXPRESS_DISCOVERY_SKU_DUPLICATE",
        "ALIEXPRESS_DISCOVERY_SKU_CURRENCY_INVALID",
        "ALIEXPRESS_DISCOVERY_SKU_SALE_PRICE_INVALID",
        "ALIEXPRESS_DISCOVERY_SKU_POSSIBLY_TRUNCATED",
        "ALIEXPRESS_DISCOVERY_SKU_ITEM_INVALID",
    }
)


class SkuGateway(Protocol):
    async def query_product_skus(
        self,
        *,
        product_id: str,
        ship_to_country: str,
        target_currency: str,
        target_language: str,
    ) -> DiscoverySkuPage: ...


@dataclass(frozen=True, slots=True)
class SkuRefinementSummary:
    run_id: int
    state: str
    stop_reason: str
    api_call_count: int
    cache_hit_count: int
    refined_count: int
    snapshot_count: int
    error_code: str | None


class AliExpressSkuRefinementRunner:
    def __init__(
        self,
        database: AffiliateShadowDatabase,
        gateway: SkuGateway,
        *,
        app_secret: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._database = database
        self._gateway = gateway
        self._app_secret = app_secret
        self._now = now

    async def refine(
        self, source_run_id: int, profile_name: str, profile: DiscoveryProfile
    ) -> SkuRefinementSummary:
        refinement = profile.sku_refinement
        if refinement is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_SKU_PROFILE_REQUIRED")
        async with self._database.session() as session:
            source = await session.get(AliExpressDiscoveryRunModel, source_run_id)
            expected_profile_fingerprint = hashlib.sha256(
                profile.model_dump_json().encode("utf-8")
            ).hexdigest()
            if source is None or source.state != "COMPLETED":
                raise ValueError("ALIEXPRESS_DISCOVERY_SKU_SOURCE_NOT_COMPLETED")
            if (
                source.profile_name != profile_name
                or source.profile_fingerprint != expected_profile_fingerprint
            ):
                raise ValueError("ALIEXPRESS_DISCOVERY_SKU_PROFILE_MISMATCH")
            candidates = (
                await session.scalars(
                    select(AliExpressDiscoveryRunResultModel).where(
                        AliExpressDiscoveryRunResultModel.run_id == source_run_id
                    )
                )
            ).all()
            shortlist = sorted(candidates, key=lambda row: (-row.total_score, int(row.product_id)))[
                : refinement.max_refined_products
            ]
        now = self._now()
        requirements_fingerprint = hashlib.sha256(
            refinement.model_dump_json().encode("utf-8")
        ).hexdigest()
        async with self._database.session() as session:
            run_id = await SkuRefinementRepository(session).create_run(
                source_run_id=source_run_id,
                profile_name=profile_name,
                requirements_fingerprint=requirements_fingerprint,
                max_refined_products=refinement.max_refined_products,
                max_sku_api_calls=refinement.max_sku_api_calls,
                minimum_drop_percent=profile.minimum_price_drop_percent,
                now=now,
                lease_until=now + LEASE_DURATION,
            )

        for candidate in shortlist:
            product_id = candidate.product_id
            fingerprint = sku_query_fingerprint(self._app_secret, product_id=product_id)
            claimed_at = self._now()
            try:
                async with self._database.session() as session:
                    claim = await SkuRefinementRepository(session).claim_sku(
                        run_id=run_id,
                        sku_query_fingerprint=fingerprint,
                        product_id=product_id,
                        now=claimed_at,
                        lease_until=claimed_at + LEASE_DURATION,
                    )
            except ValueError as exc:
                if str(exc) == "ALIEXPRESS_DISCOVERY_SKU_API_BUDGET_EXHAUSTED":
                    return await self._finish(run_id, "COMPLETED", "MAX_SKU_API_CALLS")
                raise
            if claim.disposition is SkuClaimDisposition.IN_PROGRESS:
                return await self._finish(
                    run_id,
                    "STOPPED",
                    "CONCURRENT_SKU_QUERY_IN_PROGRESS",
                    error_code="CONCURRENT_SKU_QUERY_IN_PROGRESS",
                )
            origin = "CACHE" if claim.disposition is SkuClaimDisposition.CACHE else "LIVE"
            if origin == "CACHE":
                if claim.cached_page is None:
                    raise ValueError("ALIEXPRESS_DISCOVERY_SKU_CACHE_MISSING")
                page = claim.cached_page
            else:
                try:
                    page = await self._gateway.query_product_skus(
                        product_id=product_id,
                        ship_to_country=profile.ship_to_country,
                        target_currency=profile.target_currency,
                        target_language=profile.target_language,
                    )
                except asyncio.CancelledError:
                    await self._fail(
                        run_id, fingerprint, claim.lease_token, "UNCERTAIN", "SKU_QUERY_CANCELLED"
                    )
                    raise
                except ProviderError as exc:
                    if exc.code in ITEM_ERROR_CODES:
                        await self._record_review_item(
                            run_id,
                            fingerprint,
                            claim.lease_token,
                            product_id,
                            candidate.total_score,
                            exc.code,
                        )
                        continue
                    state = "UNCERTAIN" if exc.retryable else "REVIEW_REQUIRED"
                    return await self._fail(run_id, fingerprint, claim.lease_token, state, exc.code)
                except (OSError, TimeoutError):
                    return await self._fail(
                        run_id,
                        fingerprint,
                        claim.lease_token,
                        "UNCERTAIN",
                        "SKU_QUERY_TRANSPORT_UNCERTAIN",
                    )
            if page.product_id != product_id:
                await self._record_review_item(
                    run_id,
                    fingerprint,
                    claim.lease_token,
                    product_id,
                    candidate.total_score,
                    "ALIEXPRESS_DISCOVERY_SKU_PRODUCT_MISMATCH",
                )
                continue
            match = match_discovery_skus(page, refinement.requirements)
            if match.state == "REVIEW_REQUIRED":
                await self._record_review_item(
                    run_id,
                    fingerprint,
                    claim.lease_token,
                    product_id,
                    candidate.total_score,
                    "ALIEXPRESS_DISCOVERY_SKU_ITEM_INVALID",
                )
                continue
            async with self._database.session() as session:
                repository = SkuRefinementRepository(session)
                observed_at = self._now()
                if origin == "LIVE":
                    await repository.finish_sku_success(
                        run_id=run_id,
                        sku_query_fingerprint=fingerprint,
                        lease_token=claim.lease_token,
                        page=page,
                        now=observed_at,
                    )
                await repository.record_item(
                    run_id=run_id,
                    product_id=product_id,
                    sku_query_fingerprint=fingerprint,
                    origin=origin,
                    source_product_score=candidate.total_score,
                    match=match,
                    observed_at=observed_at,
                )
        return await self._finish(run_id, "COMPLETED", "SHORTLIST_COMPLETED")

    async def _record_review_item(
        self,
        run_id: int,
        fingerprint: str,
        lease_token: str | None,
        product_id: str,
        source_product_score: int,
        error_code: str,
    ) -> None:
        async with self._database.session() as session:
            repository = SkuRefinementRepository(session)
            await repository.abandon_claim(
                sku_query_fingerprint=fingerprint, lease_token=lease_token
            )
            await repository.record_item(
                run_id=run_id,
                product_id=product_id,
                sku_query_fingerprint=fingerprint,
                origin="LIVE",
                source_product_score=source_product_score,
                match=SkuMatchResult("REVIEW_REQUIRED", None, ()),
                observed_at=self._now(),
                error_code=error_code,
            )

    async def _fail(
        self,
        run_id: int,
        fingerprint: str,
        lease_token: str | None,
        state: str,
        error_code: str,
    ) -> SkuRefinementSummary:
        async with self._database.session() as session:
            repository = SkuRefinementRepository(session)
            await repository.abandon_claim(
                sku_query_fingerprint=fingerprint, lease_token=lease_token
            )
            await repository.finish_run(
                run_id, state=state, now=self._now(), stop_reason=state, error_code=error_code
            )
        return await self._summary(run_id)

    async def _finish(
        self, run_id: int, state: str, stop_reason: str, *, error_code: str | None = None
    ) -> SkuRefinementSummary:
        async with self._database.session() as session:
            repository = SkuRefinementRepository(session)
            if state == "COMPLETED":
                await repository.rank_run(run_id, now=self._now())
            await repository.finish_run(
                run_id,
                state=state,
                now=self._now(),
                stop_reason=stop_reason,
                error_code=error_code,
            )
        return await self._summary(run_id)

    async def _summary(self, run_id: int) -> SkuRefinementSummary:
        async with self._database.session() as session:
            row = await SkuRefinementRepository(session).get_run(run_id)
            if row is None:
                raise ValueError("ALIEXPRESS_DISCOVERY_SKU_RUN_NOT_FOUND")
            return SkuRefinementSummary(
                run_id=row.id,
                state=row.state,
                stop_reason=row.stop_reason or "",
                api_call_count=row.api_call_count,
                cache_hit_count=row.cache_hit_count,
                refined_count=row.refined_count,
                snapshot_count=row.snapshot_count,
                error_code=row.error_code,
            )
