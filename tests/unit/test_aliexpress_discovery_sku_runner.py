from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from promo_bot.database.aliexpress_discovery_repository import (
    DiscoveryRepository,
    DiscoveryRunState,
)
from promo_bot.database.aliexpress_discovery_sku_repository import (
    SkuRefinementRepository,
    sku_query_fingerprint,
)
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import (
    AliExpressDiscoverySkuPriceSnapshotModel,
    AliExpressDiscoverySkuRefinementItemModel,
    DealModel,
    DeliveryModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.sku_runner import AliExpressSkuRefinementRunner
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.discovery import DiscoveryProduct
from promo_bot.providers.aliexpress.discovery_sku import (
    AliExpressDiscoverySkuGateway,
    DiscoverySku,
    DiscoverySkuAttribute,
    DiscoverySkuPage,
)
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.providers.base import ProviderError

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


def profile(*, max_refined_products: int = 2, max_sku_api_calls: int = 2) -> DiscoveryProfile:
    return DiscoveryProfile.model_validate(
        {
            "keywords": ["ssd"],
            "ship_to_country": "BR",
            "target_currency": "BRL",
            "target_language": "PT",
            "page_size": 5,
            "max_pages": 1,
            "max_results": 5,
            "max_api_calls": 1,
            "minimum_price_drop_percent": "5",
            "sku_refinement": {
                "max_refined_products": max_refined_products,
                "max_sku_api_calls": max_sku_api_calls,
                "requirements": [
                    {
                        "dimension": "capacity",
                        "property_names": ["ROM"],
                        "accepted_values": ["1 TB"],
                    }
                ],
            },
        }
    )


def product(product_id: str, discount: str) -> DiscoveryProduct:
    return DiscoveryProduct(
        product_id=product_id,
        title="SSD",
        image_url=None,
        first_category_id=None,
        first_category_name=None,
        second_category_id=None,
        second_category_name=None,
        shop_id=None,
        shop_name=None,
        target_brl_price=Decimal("194.21"),
        observed_prices=(),
        declared_discount_percent=Decimal(discount),
        commission_rate=None,
        hot_product_commission_rate=None,
        volume=None,
        completeness_score=1,
        diagnostics=frozenset(),
    )


def sku_page(product_id: str) -> DiscoverySkuPage:
    return DiscoverySkuPage(
        product_id=product_id,
        skus=(
            DiscoverySku(
                product_id=product_id,
                sku_id="120000000000001",
                currency="BRL",
                price_with_tax=Decimal("229.90"),
                sale_price_with_tax=Decimal("219.90"),
                discount_percent=None,
                attributes=(DiscoverySkuAttribute("ROM", "1 TB"),),
            ),
        ),
    )


class FakeGateway:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        self.error = error

    async def query_product_skus(
        self,
        *,
        product_id: str,
        ship_to_country: str,
        target_currency: str,
        target_language: str,
    ) -> DiscoverySkuPage:
        self.calls.append((product_id, ship_to_country, target_currency, target_language))
        if self.error is not None:
            raise self.error
        return sku_page(product_id)


async def setup(tmp_path: Path, chosen: DiscoveryProfile):  # type: ignore[no-untyped-def]
    path = tmp_path / "sku-runner.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    db = create_affiliate_shadow_database(path)
    async with db.session() as session:
        repo = DiscoveryRepository(session)
        source_run_id = await repo.create_run(
            profile_name="hardware-gamer-br",
            profile_fingerprint=hashlib.sha256(chosen.model_dump_json().encode()).hexdigest(),
            page_size=5,
            max_pages=1,
            max_results=5,
            max_api_calls=1,
            minimum_drop_percent=Decimal("5"),
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
        )
        await repo.record_product(
            run_id=source_run_id,
            query_fingerprint="a" * 64,
            tracking_fingerprint="b" * 64,
            product=product("1005000000000002", "10"),
            origin="CACHE",
            observed_at=NOW,
        )
        await repo.record_product(
            run_id=source_run_id,
            query_fingerprint="a" * 64,
            tracking_fingerprint="b" * 64,
            product=product("1005000000000001", "40"),
            origin="CACHE",
            observed_at=NOW,
        )
        await repo.finish_run(
            source_run_id,
            state=DiscoveryRunState.COMPLETED,
            now=NOW,
            stop_reason="ALL_KEYWORDS_COMPLETED",
        )
    return db, source_run_id


@pytest.mark.asyncio
async def test_refine_shortlists_by_product_score_and_creates_only_live_sku_snapshot(
    tmp_path: Path,
) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    try:
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "COMPLETED"
        assert result.api_call_count == 1
        assert result.snapshot_count == 1
        assert gateway.calls == [("1005000000000001", "BR", "BRL", "PT")]
        async with db.session() as session:
            items = (await session.scalars(select(AliExpressDiscoverySkuRefinementItemModel))).all()
            assert [(item.product_id, item.state, item.origin) for item in items] == [
                ("1005000000000001", "MATCHED", "LIVE")
            ]
            snapshots = (
                await session.scalars(select(AliExpressDiscoverySkuPriceSnapshotModel))
            ).all()
            assert [(item.product_id, item.sku_id, item.price) for item in snapshots] == [
                ("1005000000000001", "120000000000001", Decimal("219.90"))
            ]
            assert await session.scalar(select(func.count()).select_from(DealModel)) == 0
            assert await session.scalar(select(func.count()).select_from(DeliveryModel)) == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_cache_hit_creates_no_second_snapshot_or_top_call(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    runner = AliExpressSkuRefinementRunner(db, gateway, app_secret="local-secret", now=lambda: NOW)
    try:
        first = await runner.refine(source_run_id, "hardware-gamer-br", chosen)
        second = await runner.refine(source_run_id, "hardware-gamer-br", chosen)
        assert first.api_call_count == 1
        assert second.api_call_count == 0
        assert second.cache_hit_count == 1
        assert second.snapshot_count == 0
        assert len(gateway.calls) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_global_api_failure_stops_review_without_fallback(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway(error=ProviderError("ALIEXPRESS_API_REJECTED", retryable=False))
    try:
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "REVIEW_REQUIRED"
        assert result.api_call_count == 1
        assert result.snapshot_count == 0
        assert len(gateway.calls) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_mismatched_source_profile_refuses_refinement_before_gateway(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    try:
        with pytest.raises(ValueError, match="PROFILE_MISMATCH"):
            await AliExpressSkuRefinementRunner(
                db, gateway, app_secret="local-secret", now=lambda: NOW
            ).refine(source_run_id, "hardware-gamer-br", profile())
        assert gateway.calls == []
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_concurrent_claim_stops_without_gateway_or_polling(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    try:
        async with db.session() as session:
            repository = SkuRefinementRepository(session)
            prior_run_id = await repository.create_run(
                source_run_id=source_run_id,
                profile_name="hardware-gamer-br",
                requirements_fingerprint="c" * 64,
                max_refined_products=1,
                max_sku_api_calls=1,
                minimum_drop_percent=Decimal("5"),
                now=NOW,
                lease_until=NOW + timedelta(minutes=1),
            )
            await repository.claim_sku(
                run_id=prior_run_id,
                sku_query_fingerprint=sku_query_fingerprint(
                    "local-secret", product_id="1005000000000001"
                ),
                product_id="1005000000000001",
                now=NOW,
                lease_until=NOW + timedelta(minutes=1),
            )
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "STOPPED"
        assert result.stop_reason == "CONCURRENT_SKU_QUERY_IN_PROGRESS"
        assert result.api_call_count == 0
        assert gateway.calls == []
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_item_invalid_evidence_records_review_and_continues(tmp_path: Path) -> None:
    chosen = profile()
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway(
        error=ProviderError("ALIEXPRESS_DISCOVERY_SKU_SALE_PRICE_INVALID", retryable=False)
    )
    try:
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "COMPLETED"
        assert result.refined_count == 2
        assert result.api_call_count == 2
        assert result.snapshot_count == 0
        async with db.session() as session:
            items = (await session.scalars(select(AliExpressDiscoverySkuRefinementItemModel))).all()
            assert [item.state for item in items] == ["REVIEW_REQUIRED", "REVIEW_REQUIRED"]
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_transport_timeout_finishes_uncertain_without_retry(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway(error=TimeoutError())
    try:
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "UNCERTAIN"
        assert result.api_call_count == 1
        assert len(gateway.calls) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_httpx_transport_timeout_finishes_uncertain_without_retry(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    requests: list[httpx.Request] = []

    async def timeout(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(timeout), trust_env=False, follow_redirects=False
        ) as http_client:
            client = AliExpressAffiliateApiClient(
                AliExpressHttpTransport(http_client, max_attempts=1, durable_retry=False),
                request_builder=AliExpressTopRequestBuilder("synthetic-key", "synthetic-secret"),
                live_enabled=True,
            )
            result = await AliExpressSkuRefinementRunner(
                db,
                AliExpressDiscoverySkuGateway(client),
                app_secret="local-secret",
                now=lambda: NOW,
            ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "UNCERTAIN"
        assert result.error_code == "ALIEXPRESS_RETRY_EXHAUSTED"
        assert result.api_call_count == 1
        assert len(requests) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_call_budget_prevents_second_uncached_gateway_call(tmp_path: Path) -> None:
    chosen = profile(max_refined_products=2, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    try:
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "COMPLETED"
        assert result.stop_reason == "MAX_SKU_API_CALLS"
        assert result.api_call_count == 1
        assert len(gateway.calls) == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_sku_history_uses_only_same_sku_prior_live_snapshots_and_ranks_cache(
    tmp_path: Path,
) -> None:
    chosen = profile(max_refined_products=1, max_sku_api_calls=1)
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    try:
        async with db.session() as session:
            repository = SkuRefinementRepository(session)
            for day, price in [(2, "300.00"), (1, "320.00")]:
                observed_at = NOW - timedelta(days=day)
                older_run = await repository.create_run(
                    source_run_id=source_run_id,
                    profile_name="hardware-gamer-br",
                    requirements_fingerprint="d" * 64,
                    max_refined_products=1,
                    max_sku_api_calls=1,
                    minimum_drop_percent=Decimal("5"),
                    now=observed_at,
                    lease_until=observed_at + timedelta(minutes=1),
                )
                session.add(
                    AliExpressDiscoverySkuPriceSnapshotModel(
                        refinement_run_id=older_run,
                        product_id="1005000000000001",
                        sku_id="120000000000001",
                        price=Decimal(price),
                        currency="BRL",
                        observed_at=observed_at,
                        price_basis="SALE_PRICE_WITH_TAX",
                        source_operation="aliexpress.affiliate.product.sku.detail.get",
                    )
                )
                await repository.finish_run(
                    older_run,
                    state="COMPLETED",
                    now=observed_at,
                    stop_reason="SHORTLIST_COMPLETED",
                )
        runner = AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        )
        first = await runner.refine(source_run_id, "hardware-gamer-br", chosen)
        second = await runner.refine(source_run_id, "hardware-gamer-br", chosen)
        assert first.snapshot_count == 1
        assert second.snapshot_count == 0
        async with db.session() as session:
            items = (
                await session.scalars(
                    select(AliExpressDiscoverySkuRefinementItemModel).where(
                        AliExpressDiscoverySkuRefinementItemModel.refinement_run_id == second.run_id
                    )
                )
            ).all()
            assert len(items) == 1
            assert items[0].origin == "CACHE"
            assert items[0].history_median == Decimal("310.00")
            assert items[0].history_snapshot_count == 2
            assert items[0].classification == "SKU_HISTORY_BACKED_PRICE_DROP"
            assert items[0].rank_position == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_history_backed_sku_ranks_above_higher_product_level_score(tmp_path: Path) -> None:
    chosen = profile()
    db, source_run_id = await setup(tmp_path, chosen)
    gateway = FakeGateway()
    try:
        async with db.session() as session:
            repository = SkuRefinementRepository(session)
            for day in (2, 1):
                observed_at = NOW - timedelta(days=day)
                older_run = await repository.create_run(
                    source_run_id=source_run_id,
                    profile_name="hardware-gamer-br",
                    requirements_fingerprint="e" * 64,
                    max_refined_products=1,
                    max_sku_api_calls=1,
                    minimum_drop_percent=Decimal("5"),
                    now=observed_at,
                    lease_until=observed_at + timedelta(minutes=1),
                )
                session.add(
                    AliExpressDiscoverySkuPriceSnapshotModel(
                        refinement_run_id=older_run,
                        product_id="1005000000000002",
                        sku_id="120000000000001",
                        price=Decimal("300"),
                        currency="BRL",
                        observed_at=observed_at,
                        price_basis="SALE_PRICE_WITH_TAX",
                        source_operation="aliexpress.affiliate.product.sku.detail.get",
                    )
                )
                await repository.finish_run(
                    older_run, state="COMPLETED", now=observed_at, stop_reason="SHORTLIST_COMPLETED"
                )
        result = await AliExpressSkuRefinementRunner(
            db, gateway, app_secret="local-secret", now=lambda: NOW
        ).refine(source_run_id, "hardware-gamer-br", chosen)
        assert result.state == "COMPLETED"
        async with db.session() as session:
            ranked = (
                await session.scalars(
                    select(AliExpressDiscoverySkuRefinementItemModel)
                    .where(
                        AliExpressDiscoverySkuRefinementItemModel.refinement_run_id == result.run_id
                    )
                    .order_by(AliExpressDiscoverySkuRefinementItemModel.rank_position)
                )
            ).all()
            assert [item.product_id for item in ranked] == [
                "1005000000000002",
                "1005000000000001",
            ]
            assert ranked[0].classification == "SKU_HISTORY_BACKED_PRICE_DROP"
            assert ranked[1].classification == "SKU_BASELINE_ONLY"
            assert ranked[0].source_product_score < ranked[1].source_product_score
    finally:
        await db.dispose()
