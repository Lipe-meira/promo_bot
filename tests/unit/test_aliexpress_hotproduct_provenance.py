from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from promo_bot.database.aliexpress_discovery_repository import DiscoveryRepository
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import (
    AliExpressDiscoveryPriceSnapshotModel,
    AliExpressDiscoveryQueryCacheModel,
    AliExpressDiscoveryRunModel,
    AliExpressDiscoveryRunResultModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.scanner import AliExpressDiscoveryScanner
from promo_bot.providers.aliexpress.contracts import HOTPRODUCT_QUERY, PRODUCT_QUERY
from promo_bot.providers.aliexpress.discovery import DiscoveryPage, DiscoveryProduct

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def product(price: str, *, discount: str | None = None) -> DiscoveryProduct:
    return DiscoveryProduct(
        product_id="1005000000000001",
        title="Synthetic product",
        image_url=None,
        first_category_id=None,
        first_category_name=None,
        second_category_id=None,
        second_category_name=None,
        shop_id=None,
        shop_name=None,
        target_brl_price=Decimal(price),
        observed_prices=(),
        declared_discount_percent=Decimal(discount) if discount else None,
        commission_rate=None,
        hot_product_commission_rate=None,
        volume=None,
        completeness_score=2,
        diagnostics=frozenset(),
    )


class FakeGateway:
    def __init__(self, item: DiscoveryProduct) -> None:
        self.item = item
        self.calls = 0

    async def query_page(self, **kwargs: object) -> DiscoveryPage:
        del kwargs
        self.calls += 1
        return DiscoveryPage((self.item,), 1, 1)


def profile() -> DiscoveryProfile:
    return DiscoveryProfile(
        keywords=("ssd",),
        category_ids=(),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_size=5,
        max_pages=1,
        max_results=10,
        max_api_calls=2,
        minimum_price_drop_percent=Decimal("5"),
    )


@pytest.mark.asyncio
async def test_source_specific_cache_and_history_never_cross_operations(tmp_path: Path) -> None:
    path = tmp_path / "hot.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    db = create_affiliate_shadow_database(path)
    try:
        query_gateway = FakeGateway(product("100"))
        hot_gateway = FakeGateway(product("80", discount="30"))
        query_run = await AliExpressDiscoveryScanner(db, query_gateway, now=lambda: NOW).scan(
            profile_name="sample", profile=profile(), app_secret="secret", tracking_id="tracking"
        )
        hot_run = await AliExpressDiscoveryScanner(
            db, hot_gateway, now=lambda: NOW + timedelta(seconds=1)
        ).scan(
            profile_name="sample",
            profile=profile(),
            app_secret="secret",
            tracking_id="tracking",
            source_operation=HOTPRODUCT_QUERY,
        )
        cached_gateway = FakeGateway(product("70"))
        cached_run = await AliExpressDiscoveryScanner(
            db, cached_gateway, now=lambda: NOW + timedelta(seconds=2)
        ).scan(
            profile_name="sample",
            profile=profile(),
            app_secret="secret",
            tracking_id="tracking",
            source_operation=HOTPRODUCT_QUERY,
        )
        async with db.session() as session:
            query = await session.get(AliExpressDiscoveryRunModel, query_run.run_id)
            hot = await session.get(AliExpressDiscoveryRunModel, hot_run.run_id)
            caches = (await session.scalars(select(AliExpressDiscoveryQueryCacheModel))).all()
            snapshots = (await session.scalars(select(AliExpressDiscoveryPriceSnapshotModel))).all()
            hot_result = await session.scalar(
                select(AliExpressDiscoveryRunResultModel).where(
                    AliExpressDiscoveryRunResultModel.run_id == hot_run.run_id
                )
            )
        assert query is not None and query.source_operation == PRODUCT_QUERY
        assert hot is not None and hot.source_operation == HOTPRODUCT_QUERY
        assert {cache.source_operation for cache in caches} == {PRODUCT_QUERY, HOTPRODUCT_QUERY}
        assert {snapshot.source_operation for snapshot in snapshots} == {
            PRODUCT_QUERY,
            HOTPRODUCT_QUERY,
        }
        assert hot_result is not None
        assert hot_result.history_snapshot_count == 0
        assert hot_result.classification == "PROVIDER_DISCOUNT_ONLY"
        assert query_gateway.calls == hot_gateway.calls == 1
        assert cached_gateway.calls == 0
        assert cached_run.cache_hit_count == 1
        assert cached_run.snapshot_count == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_hot_history_ignores_two_query_snapshots_and_uses_own_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    db = create_affiliate_shadow_database(path)
    try:

        async def scan_at(moment: datetime, price: str, source_operation: str) -> int:
            gateway = FakeGateway(product(price))
            summary = await AliExpressDiscoveryScanner(db, gateway, now=lambda: moment).scan(
                profile_name="sample",
                profile=profile(),
                app_secret="secret",
                tracking_id="tracking",
                source_operation=source_operation,
            )
            assert gateway.calls == 1
            return summary.run_id

        await scan_at(NOW, "100", PRODUCT_QUERY)
        await scan_at(NOW + timedelta(minutes=61), "100", PRODUCT_QUERY)
        hot_first = await scan_at(NOW + timedelta(minutes=122), "80", HOTPRODUCT_QUERY)
        await scan_at(NOW + timedelta(minutes=183), "100", HOTPRODUCT_QUERY)
        await scan_at(NOW + timedelta(minutes=244), "100", HOTPRODUCT_QUERY)
        hot_drop = await scan_at(NOW + timedelta(minutes=305), "80", HOTPRODUCT_QUERY)
        async with db.session() as session:
            first = await session.scalar(
                select(AliExpressDiscoveryRunResultModel).where(
                    AliExpressDiscoveryRunResultModel.run_id == hot_first
                )
            )
            later = await session.scalar(
                select(AliExpressDiscoveryRunResultModel).where(
                    AliExpressDiscoveryRunResultModel.run_id == hot_drop
                )
            )
        assert first is not None and first.history_snapshot_count == 0
        assert first.classification == "BASELINE_ONLY"
        assert later is not None and later.history_snapshot_count >= 2
        assert later.classification == "HISTORY_BACKED_PRICE_DROP"
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_repository_rejects_unknown_source_operation(tmp_path: Path) -> None:
    path = tmp_path / "hot.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    db = create_affiliate_shadow_database(path)
    try:
        async with db.session() as session:
            with pytest.raises(ValueError, match="DISCOVERY_SOURCE_OPERATION_INVALID"):
                await DiscoveryRepository(session).create_run(
                    profile_name="sample",
                    profile_fingerprint="a" * 64,
                    page_size=5,
                    max_pages=1,
                    max_results=10,
                    max_api_calls=2,
                    minimum_drop_percent=Decimal("5"),
                    now=NOW,
                    lease_until=NOW + timedelta(seconds=60),
                    source_operation="unknown",
                )
    finally:
        await db.dispose()
