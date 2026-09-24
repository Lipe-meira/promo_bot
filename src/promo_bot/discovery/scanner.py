"""Bounded manual scanner for AliExpress product-query discovery."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from promo_bot.database.aliexpress_discovery_repository import (
    DiscoveryClaimDisposition,
    DiscoveryRepository,
    DiscoveryRunState,
    discovery_query_fingerprint,
    discovery_tracking_fingerprint,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.providers.aliexpress.contracts import HOTPRODUCT_QUERY, PRODUCT_QUERY
from promo_bot.providers.aliexpress.discovery import DiscoveryPage
from promo_bot.providers.base import ProviderError

LEASE_DURATION = timedelta(seconds=60)


class DiscoveryGateway(Protocol):
    async def query_page(
        self,
        *,
        keyword: str,
        category_ids: tuple[str, ...],
        ship_to_country: str,
        target_currency: str,
        target_language: str,
        page_no: int,
        page_size: int,
    ) -> DiscoveryPage: ...


@dataclass(frozen=True, slots=True)
class DiscoveryRunSummary:
    run_id: int
    state: str
    stop_reason: str
    api_call_count: int
    cache_hit_count: int
    page_count: int
    received_count: int
    unique_product_count: int
    snapshot_count: int
    error_code: str | None


class AliExpressDiscoveryScanner:
    def __init__(
        self,
        database: AffiliateShadowDatabase,
        gateway: DiscoveryGateway,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._database = database
        self._gateway = gateway
        self._now = now

    async def scan(
        self,
        *,
        profile_name: str,
        profile: DiscoveryProfile,
        app_secret: str,
        tracking_id: str,
        source_operation: str = PRODUCT_QUERY,
    ) -> DiscoveryRunSummary:
        if source_operation not in {PRODUCT_QUERY, HOTPRODUCT_QUERY}:
            raise ValueError("ALIEXPRESS_DISCOVERY_SOURCE_OPERATION_INVALID")
        started = self._now()
        async with self._database.session() as session:
            repository = DiscoveryRepository(session)
            run_id = await repository.create_run(
                profile_name=profile_name,
                profile_fingerprint=hashlib.sha256(
                    profile.model_dump_json().encode("utf-8")
                ).hexdigest(),
                page_size=profile.page_size,
                max_pages=profile.max_pages,
                max_results=profile.max_results,
                max_api_calls=profile.max_api_calls,
                minimum_drop_percent=profile.minimum_price_drop_percent,
                now=started,
                lease_until=started + LEASE_DURATION,
                source_operation=source_operation,
            )

        tracking_fingerprint = discovery_tracking_fingerprint(app_secret, tracking_id)
        unique_products = 0
        query_ordinal = 0
        final_reason = "ALL_KEYWORDS_COMPLETED"

        for keyword in profile.keywords:
            keyword_reason = "MAX_PAGES"
            for page_no in range(1, profile.max_pages + 1):
                if unique_products >= profile.max_results:
                    final_reason = "MAX_RESULTS"
                    return await self._finish(run_id, DiscoveryRunState.COMPLETED, final_reason)
                query_fingerprint = discovery_query_fingerprint(
                    app_secret,
                    operation=source_operation,
                    keyword=keyword,
                    category_ids=profile.category_ids,
                    ship_to_country=profile.ship_to_country,
                    target_currency=profile.target_currency,
                    target_language=profile.target_language,
                    page_no=page_no,
                    page_size=profile.page_size,
                    platform_product_type="ALL",
                )
                now = self._now()
                try:
                    async with self._database.session() as session:
                        claim = await DiscoveryRepository(session).claim_query(
                            run_id=run_id,
                            query_fingerprint=query_fingerprint,
                            tracking_fingerprint=tracking_fingerprint,
                            query_ordinal=query_ordinal,
                            page_no=page_no,
                            now=now,
                            lease_until=now + LEASE_DURATION,
                        )
                except ValueError as exc:
                    if str(exc) == "ALIEXPRESS_DISCOVERY_API_BUDGET_EXHAUSTED":
                        return await self._finish(
                            run_id, DiscoveryRunState.COMPLETED, "MAX_API_CALLS"
                        )
                    raise
                query_ordinal += 1
                if claim.disposition is DiscoveryClaimDisposition.IN_PROGRESS:
                    return await self._finish(
                        run_id,
                        DiscoveryRunState.STOPPED,
                        "CONCURRENT_QUERY_IN_PROGRESS",
                        error_code="CONCURRENT_QUERY_IN_PROGRESS",
                    )

                origin = "CACHE"
                if claim.disposition is DiscoveryClaimDisposition.CACHE:
                    page = DiscoveryPage(
                        claim.cached_products,
                        claim.current_record_count,
                        claim.total_record_count,
                        claim.rejected_product_count,
                    )
                else:
                    origin = "LIVE"
                    try:
                        page = await self._gateway.query_page(
                            keyword=keyword,
                            category_ids=profile.category_ids,
                            ship_to_country=profile.ship_to_country,
                            target_currency=profile.target_currency,
                            target_language=profile.target_language,
                            page_no=page_no,
                            page_size=profile.page_size,
                        )
                    except asyncio.CancelledError:
                        await self._fail_query(
                            run_id,
                            query_fingerprint,
                            tracking_fingerprint,
                            claim.lease_token,
                            DiscoveryRunState.UNCERTAIN,
                            "ALIEXPRESS_DISCOVERY_CANCELLED",
                        )
                        raise
                    except ProviderError as exc:
                        state = (
                            DiscoveryRunState.REVIEW_REQUIRED
                            if exc.manual_review or exc.code != "ALIEXPRESS_RETRY_EXHAUSTED"
                            else DiscoveryRunState.UNCERTAIN
                        )
                        return await self._fail_query(
                            run_id,
                            query_fingerprint,
                            tracking_fingerprint,
                            claim.lease_token,
                            state,
                            exc.code,
                        )
                    reached_max_results = False
                    async with self._database.session() as session:
                        repository = DiscoveryRepository(session)
                        await repository.finish_query_success(
                            run_id=run_id,
                            query_fingerprint=query_fingerprint,
                            tracking_fingerprint=tracking_fingerprint,
                            lease_token=claim.lease_token,
                            page=page,
                            now=self._now(),
                        )
                        for item in page.products:
                            if unique_products >= profile.max_results:
                                reached_max_results = True
                                break
                            recorded = await repository.record_product(
                                run_id=run_id,
                                query_fingerprint=query_fingerprint,
                                tracking_fingerprint=tracking_fingerprint,
                                product=item,
                                origin=origin,
                                observed_at=self._now(),
                            )
                            if recorded.inserted_unique:
                                unique_products += 1
                    if reached_max_results:
                        return await self._finish(
                            run_id, DiscoveryRunState.COMPLETED, "MAX_RESULTS"
                        )

                if origin == "CACHE":
                    for item in page.products:
                        if unique_products >= profile.max_results:
                            return await self._finish(
                                run_id, DiscoveryRunState.COMPLETED, "MAX_RESULTS"
                            )
                        async with self._database.session() as session:
                            recorded = await DiscoveryRepository(session).record_product(
                                run_id=run_id,
                                query_fingerprint=query_fingerprint,
                                tracking_fingerprint=tracking_fingerprint,
                                product=item,
                                origin=origin,
                                observed_at=self._now(),
                            )
                        if recorded.inserted_unique:
                            unique_products += 1

                source_item_count = len(page.products) + page.rejected_product_count
                if source_item_count < profile.page_size:
                    keyword_reason = "SHORT_PAGE"
                    break
            final_reason = keyword_reason

        return await self._finish(run_id, DiscoveryRunState.COMPLETED, final_reason)

    async def _fail_query(
        self,
        run_id: int,
        query_fingerprint: str,
        tracking_fingerprint: str,
        lease_token: str | None,
        state: DiscoveryRunState,
        error_code: str,
    ) -> DiscoveryRunSummary:
        now = self._now()
        async with self._database.session() as session:
            repository = DiscoveryRepository(session)
            await repository.abandon_query(
                query_fingerprint=query_fingerprint,
                tracking_fingerprint=tracking_fingerprint,
                lease_token=lease_token,
            )
            await repository.finish_run(
                run_id,
                state=state,
                now=now,
                stop_reason=state.value,
                error_code=error_code,
            )
        return await self._summary(run_id)

    async def _finish(
        self,
        run_id: int,
        state: DiscoveryRunState,
        stop_reason: str,
        *,
        error_code: str | None = None,
    ) -> DiscoveryRunSummary:
        async with self._database.session() as session:
            await DiscoveryRepository(session).finish_run(
                run_id,
                state=state,
                now=self._now(),
                stop_reason=stop_reason,
                error_code=error_code,
            )
        return await self._summary(run_id)

    async def _summary(self, run_id: int) -> DiscoveryRunSummary:
        async with self._database.session() as session:
            row = await DiscoveryRepository(session).get_run(run_id)
            if row is None:
                raise ValueError("ALIEXPRESS_DISCOVERY_RUN_NOT_FOUND")
            return DiscoveryRunSummary(
                run_id=row.id,
                state=row.state,
                stop_reason=row.stop_reason or "",
                api_call_count=row.api_call_count,
                cache_hit_count=row.cache_hit_count,
                page_count=row.page_count,
                received_count=row.received_count,
                unique_product_count=row.unique_product_count,
                snapshot_count=row.snapshot_count,
                error_code=row.error_code,
            )
