from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.database.aliexpress_discovery_repository import (
    DiscoveryRepository,
    discovery_query_fingerprint,
    discovery_tracking_fingerprint,
)
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import (
    AliExpressDiscoveryPriceSnapshotModel,
    AliExpressDiscoveryQueryCacheModel,
    AliExpressDiscoveryQueryClaimModel,
    AliExpressDiscoveryRunResultModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.scanner import AliExpressDiscoveryScanner
from promo_bot.providers.aliexpress.contracts import PRODUCT_QUERY
from promo_bot.providers.aliexpress.discovery import DiscoveryPage, DiscoveryProduct
from promo_bot.providers.base import ProviderError

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
APP_SECRET = "synthetic-app-secret"
TRACKING = "synthetic-tracking"


class FakeGateway:
    def __init__(self, pages: list[DiscoveryPage]) -> None:
        self.pages = deque(pages)
        self.calls: list[tuple[str, int]] = []

    async def query_page(self, **kwargs: object) -> DiscoveryPage:
        self.calls.append((str(kwargs["keyword"]), int(kwargs["page_no"])))
        return self.pages.popleft()


class FailingGateway:
    def __init__(self, error: ProviderError) -> None:
        self.error = error
        self.call_count = 0

    async def query_page(self, **kwargs: object) -> DiscoveryPage:
        del kwargs
        self.call_count += 1
        raise self.error


def profile(*, keywords: tuple[str, ...], max_api_calls: int = 4) -> DiscoveryProfile:
    return DiscoveryProfile(
        keywords=keywords,
        category_ids=(),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_size=1,
        max_pages=1,
        max_results=25,
        max_api_calls=max_api_calls,
        minimum_price_drop_percent=Decimal("5"),
    )


def product(product_id: str, *, title: str, price: str) -> DiscoveryProduct:
    return DiscoveryProduct(
        product_id=product_id,
        title=title,
        image_url=None,
        first_category_id=None,
        first_category_name=None,
        second_category_id=None,
        second_category_name=None,
        shop_id=None,
        shop_name=None,
        target_brl_price=Decimal(price),
        observed_prices=(),
        declared_discount_percent=None,
        commission_rate=None,
        hot_product_commission_rate=None,
        volume=None,
        completeness_score=2,
        diagnostics=frozenset(),
    )


async def make_database(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "scanner.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    return create_affiliate_shadow_database(path)


async def seed_cache(
    db,  # type: ignore[no-untyped-def]
    *,
    keyword: str,
    cached_product: DiscoveryProduct,
) -> None:
    query_fp = discovery_query_fingerprint(
        APP_SECRET,
        operation=PRODUCT_QUERY,
        keyword=keyword,
        category_ids=(),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=1,
        platform_product_type="ALL",
    )
    tracking_fp = discovery_tracking_fingerprint(APP_SECRET, TRACKING)
    async with db.session() as session:
        repository = DiscoveryRepository(session)
        run_id = await repository.create_run(
            profile_name="seed",
            profile_fingerprint="f" * 64,
            page_size=1,
            max_pages=1,
            max_results=10,
            max_api_calls=1,
            minimum_drop_percent=Decimal("5"),
            now=NOW,
            lease_until=NOW + timedelta(seconds=60),
        )
        claim = await repository.claim_query(
            run_id=run_id,
            query_fingerprint=query_fp,
            tracking_fingerprint=tracking_fp,
            query_ordinal=0,
            page_no=1,
            now=NOW,
            lease_until=NOW + timedelta(seconds=60),
        )
        await repository.finish_query_success(
            run_id=run_id,
            query_fingerprint=query_fp,
            tracking_fingerprint=tracking_fp,
            lease_token=claim.lease_token,
            page=DiscoveryPage((cached_product,), 1, 1),
            now=NOW,
        )


async def scan_results(db, run_id: int):  # type: ignore[no-untyped-def]
    async with db.session() as session:
        results = (
            await session.scalars(
                select(AliExpressDiscoveryRunResultModel).where(
                    AliExpressDiscoveryRunResultModel.run_id == run_id
                )
            )
        ).all()
        snapshots = await session.scalar(
            select(func.count())
            .select_from(AliExpressDiscoveryPriceSnapshotModel)
            .where(AliExpressDiscoveryPriceSnapshotModel.run_id == run_id)
        )
        return results, snapshots


@pytest.mark.asyncio
async def test_cache_then_live_upgrades_result_and_creates_one_snapshot(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    product_id = "1005000000000001"
    try:
        await seed_cache(
            db,
            keyword="cached",
            cached_product=product(product_id, title="cached", price="90"),
        )
        gateway = FakeGateway(
            [DiscoveryPage((product(product_id, title="live", price="80"),), 1, 1)]
        )
        summary = await AliExpressDiscoveryScanner(
            db, gateway, now=lambda: NOW + timedelta(seconds=10)
        ).scan(
            profile_name="test",
            profile=profile(keywords=("cached", "live")),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        results, snapshots = await scan_results(db, summary.run_id)
        assert gateway.calls == [("live", 1)]
        assert len(results) == 1
        assert results[0].origin == "LIVE"
        assert results[0].title == "live"
        assert results[0].target_brl_price == Decimal("80")
        assert snapshots == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_live_then_cache_never_replaces_live_result(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    product_id = "1005000000000002"
    try:
        await seed_cache(
            db,
            keyword="cached",
            cached_product=product(product_id, title="cached", price="90"),
        )
        gateway = FakeGateway(
            [DiscoveryPage((product(product_id, title="live", price="80"),), 1, 1)]
        )
        summary = await AliExpressDiscoveryScanner(
            db, gateway, now=lambda: NOW + timedelta(seconds=10)
        ).scan(
            profile_name="test",
            profile=profile(keywords=("live", "cached")),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        results, snapshots = await scan_results(db, summary.run_id)
        assert results[0].origin == "LIVE"
        assert results[0].title == "live"
        assert snapshots == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_first_live_occurrence_wins_and_snapshot_is_unique(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    product_id = "1005000000000003"
    try:
        gateway = FakeGateway(
            [
                DiscoveryPage((product(product_id, title="first", price="80"),), 1, 1),
                DiscoveryPage((product(product_id, title="second", price="70"),), 1, 1),
            ]
        )
        summary = await AliExpressDiscoveryScanner(
            db, gateway, now=lambda: NOW + timedelta(seconds=10)
        ).scan(
            profile_name="test",
            profile=profile(keywords=("first", "second")),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        results, snapshots = await scan_results(db, summary.run_id)
        assert results[0].title == "first"
        assert results[0].target_brl_price == Decimal("80")
        assert results[0].matched_query_count == 2
        assert snapshots == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_cache_only_never_creates_price_snapshot(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    try:
        await seed_cache(
            db,
            keyword="cached",
            cached_product=product("1005000000000004", title="cached", price="90"),
        )
        gateway = FakeGateway([])
        summary = await AliExpressDiscoveryScanner(
            db, gateway, now=lambda: NOW + timedelta(seconds=10)
        ).scan(
            profile_name="test",
            profile=profile(keywords=("cached",)),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        results, snapshots = await scan_results(db, summary.run_id)
        assert gateway.calls == []
        assert results[0].origin == "CACHE"
        assert snapshots == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_short_page_stops_without_total_page_number_or_retry(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    try:
        gateway = FakeGateway([DiscoveryPage((), None, 137796)])
        summary = await AliExpressDiscoveryScanner(
            db, gateway, now=lambda: NOW + timedelta(seconds=10)
        ).scan(
            profile_name="test",
            profile=profile(keywords=("ssd",)),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        assert gateway.calls == [("ssd", 1)]
        assert summary.api_call_count == 1
        assert summary.stop_reason == "SHORT_PAGE"
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_rejected_item_on_full_raw_page_does_not_stop_pagination(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    selected = DiscoveryProfile(
        keywords=("ssd",),
        category_ids=(),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_size=2,
        max_pages=2,
        max_results=25,
        max_api_calls=2,
        minimum_price_drop_percent=Decimal("5"),
    )
    try:
        gateway = FakeGateway(
            [
                DiscoveryPage(
                    (product("1005000000000010", title="valid", price="80"),),
                    2,
                    2,
                    rejected_product_count=1,
                ),
                DiscoveryPage((), 0, 2),
            ]
        )
        summary = await AliExpressDiscoveryScanner(db, gateway, now=lambda: NOW).scan(
            profile_name="rejected",
            profile=selected,
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        assert gateway.calls == [("ssd", 1), ("ssd", 2)]
        assert summary.api_call_count == 2
        assert summary.received_count == 2

        cached_gateway = FakeGateway([])
        cached = await AliExpressDiscoveryScanner(
            db, cached_gateway, now=lambda: NOW + timedelta(seconds=10)
        ).scan(
            profile_name="rejected-cache",
            profile=selected,
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )
        assert cached_gateway.calls == []
        assert cached.api_call_count == 0
        assert cached.cache_hit_count == 2
        assert cached.received_count == 2
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_live_cache_and_results_rollback_together_on_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = await make_database(tmp_path)
    gateway = FakeGateway(
        [DiscoveryPage((product("1005000000000011", title="live", price="80"),), 1, 1)]
    )

    async def fail_record(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic persistence failure")

    monkeypatch.setattr(DiscoveryRepository, "record_product", fail_record)
    try:
        with pytest.raises(RuntimeError, match="synthetic persistence failure"):
            await AliExpressDiscoveryScanner(db, gateway, now=lambda: NOW).scan(
                profile_name="atomic",
                profile=profile(keywords=("ssd",)),
                app_secret=APP_SECRET,
                tracking_id=TRACKING,
            )

        async with db.session() as session:
            caches = await session.scalar(
                select(func.count()).select_from(AliExpressDiscoveryQueryCacheModel)
            )
            claims = await session.scalar(
                select(func.count()).select_from(AliExpressDiscoveryQueryClaimModel)
            )
        assert caches == 0
        assert claims == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_active_query_claim_stops_scanner_without_gateway_call(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    selected = profile(keywords=("ssd",))
    query_fp = discovery_query_fingerprint(
        APP_SECRET,
        operation=PRODUCT_QUERY,
        keyword="ssd",
        category_ids=(),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=1,
        platform_product_type="ALL",
    )
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            owner = await repository.create_run(
                profile_name="owner",
                profile_fingerprint="e" * 64,
                page_size=1,
                max_pages=1,
                max_results=25,
                max_api_calls=1,
                minimum_drop_percent=Decimal("5"),
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )
            await repository.claim_query(
                run_id=owner,
                query_fingerprint=query_fp,
                tracking_fingerprint=discovery_tracking_fingerprint(APP_SECRET, TRACKING),
                query_ordinal=0,
                page_no=1,
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )
        gateway = FakeGateway([])
        summary = await AliExpressDiscoveryScanner(db, gateway, now=lambda: NOW).scan(
            profile_name="blocked",
            profile=selected,
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        assert gateway.calls == []
        assert summary.state == "STOPPED"
        assert summary.stop_reason == "CONCURRENT_QUERY_IN_PROGRESS"
        assert summary.error_code == "CONCURRENT_QUERY_IN_PROGRESS"
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_api_call_budget_stops_before_a_second_gateway_call(tmp_path: Path) -> None:
    db = await make_database(tmp_path)
    try:
        gateway = FakeGateway(
            [DiscoveryPage((product("1005000000000005", title="first", price="80"),), 1, 1)]
        )
        summary = await AliExpressDiscoveryScanner(db, gateway, now=lambda: NOW).scan(
            profile_name="bounded",
            profile=profile(keywords=("first", "second"), max_api_calls=1),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        assert gateway.calls == [("first", 1)]
        assert summary.api_call_count == 1
        assert summary.stop_reason == "MAX_API_CALLS"
    finally:
        await db.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        (
            ProviderError(
                "ALIEXPRESS_RESPONSE_INCOMPATIBLE",
                retryable=False,
                manual_review=True,
            ),
            "REVIEW_REQUIRED",
        ),
        (
            ProviderError("ALIEXPRESS_RETRY_EXHAUSTED", retryable=False),
            "UNCERTAIN",
        ),
    ],
)
async def test_gateway_failure_is_single_attempt_and_ends_in_safe_state(
    tmp_path: Path,
    error: ProviderError,
    expected_state: str,
) -> None:
    db = await make_database(tmp_path)
    try:
        gateway = FailingGateway(error)
        summary = await AliExpressDiscoveryScanner(db, gateway, now=lambda: NOW).scan(
            profile_name="failure",
            profile=profile(keywords=("ssd",)),
            app_secret=APP_SECRET,
            tracking_id=TRACKING,
        )

        assert gateway.call_count == 1
        assert summary.api_call_count == 1
        assert summary.state == expected_state
        assert summary.error_code == error.code
    finally:
        await db.dispose()
