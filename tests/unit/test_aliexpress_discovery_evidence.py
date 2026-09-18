from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.database.aliexpress_discovery_repository import (
    DiscoveryClaimDisposition,
    DiscoveryRepository,
    DiscoveryRunState,
    discovery_query_fingerprint,
    discovery_tracking_fingerprint,
)
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import DealModel, DeliveryModel
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.providers.aliexpress.discovery import DiscoveryPage, DiscoveryProduct

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def product(product_id: str = "1005000000000001") -> DiscoveryProduct:
    return DiscoveryProduct(
        product_id=product_id,
        title="SSD",
        image_url=None,
        first_category_id="7",
        first_category_name="Hardware",
        second_category_id=None,
        second_category_name=None,
        shop_id="42",
        shop_name="Loja",
        target_brl_price=Decimal("79.90"),
        observed_prices=(),
        declared_discount_percent=Decimal("20"),
        commission_rate=Decimal("4.25"),
        hot_product_commission_rate=None,
        volume=0,
        completeness_score=6,
        diagnostics=frozenset(),
    )


async def database(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "discovery.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    return create_affiliate_shadow_database(path)


async def create_run(repository: DiscoveryRepository, *, now: datetime = NOW) -> int:
    return await repository.create_run(
        profile_name="hardware-gamer-br",
        profile_fingerprint="a" * 64,
        page_size=5,
        max_pages=2,
        max_results=25,
        max_api_calls=4,
        minimum_drop_percent=Decimal("5"),
        now=now,
        lease_until=now + timedelta(seconds=60),
    )


def test_query_and_tracking_fingerprints_use_distinct_contexts() -> None:
    query = discovery_query_fingerprint(
        "app-secret",
        operation="aliexpress.affiliate.product.query",
        keyword="ssd",
        category_ids=("21", "7"),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=5,
        platform_product_type="ALL",
    )
    tracking_a = discovery_tracking_fingerprint("app-secret", "tracking-a")
    tracking_b = discovery_tracking_fingerprint("app-secret", "tracking-b")

    assert len(query) == len(tracking_a) == len(tracking_b) == 64
    assert query != tracking_a
    assert tracking_a != tracking_b
    assert query == discovery_query_fingerprint(
        "app-secret",
        operation="aliexpress.affiliate.product.query",
        keyword="ssd",
        category_ids=("7", "21"),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=5,
        platform_product_type="ALL",
    )


@pytest.mark.asyncio
async def test_active_claim_stops_b_immediately_and_c_uses_a_cache(tmp_path: Path) -> None:
    db = await database(tmp_path)
    query_fp = "1" * 64
    tracking_fp = "2" * 64
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            run_a = await create_run(repository)
            claim_a = await repository.claim_query(
                run_id=run_a,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                query_ordinal=0,
                page_no=1,
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            run_b = await create_run(repository)
            claim_b = await repository.claim_query(
                run_id=run_b,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                query_ordinal=0,
                page_no=1,
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )

        assert claim_a.disposition is DiscoveryClaimDisposition.CALL
        assert claim_b.disposition is DiscoveryClaimDisposition.IN_PROGRESS
        assert (
            sum(claim.disposition is DiscoveryClaimDisposition.CALL for claim in (claim_a, claim_b))
            == 1
        )

        async with db.session() as session:
            repository = DiscoveryRepository(session)
            await repository.finish_query_success(
                run_id=run_a,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                lease_token=claim_a.lease_token,
                page=DiscoveryPage((product(),), 1, 137796),
                now=NOW + timedelta(seconds=1),
            )
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            run_c = await create_run(repository, now=NOW + timedelta(seconds=2))
            claim_c = await repository.claim_query(
                run_id=run_c,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                query_ordinal=0,
                page_no=1,
                now=NOW + timedelta(seconds=2),
                lease_until=NOW + timedelta(seconds=62),
            )

        assert claim_c.disposition is DiscoveryClaimDisposition.CACHE
        assert [item.product_id for item in claim_c.cached_products] == ["1005000000000001"]
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_only_one_claim_wins_immediately_after_cache_expiry(tmp_path: Path) -> None:
    db = await database(tmp_path)
    query_fp = "3" * 64
    tracking_fp = "4" * 64
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            original = await create_run(repository)
            first = await repository.claim_query(
                run_id=original,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                query_ordinal=0,
                page_no=1,
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )
            await repository.finish_query_success(
                run_id=original,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                lease_token=first.lease_token,
                page=DiscoveryPage((product(),), 1, 1),
                now=NOW,
            )

        expired = NOW + timedelta(minutes=61)
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            run_a = await create_run(repository, now=expired)
            claim_a = await repository.claim_query(
                run_id=run_a,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                query_ordinal=0,
                page_no=1,
                now=expired,
                lease_until=expired + timedelta(seconds=60),
            )
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            run_b = await create_run(repository, now=expired)
            claim_b = await repository.claim_query(
                run_id=run_b,
                query_fingerprint=query_fp,
                tracking_fingerprint=tracking_fp,
                query_ordinal=0,
                page_no=1,
                now=expired,
                lease_until=expired + timedelta(seconds=60),
            )

        assert claim_a.disposition is DiscoveryClaimDisposition.CALL
        assert claim_b.disposition is DiscoveryClaimDisposition.IN_PROGRESS
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_expired_lease_marks_owner_uncertain_and_allows_manual_new_run(
    tmp_path: Path,
) -> None:
    db = await database(tmp_path)
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            old_run = await create_run(repository)
            await repository.claim_query(
                run_id=old_run,
                query_fingerprint="5" * 64,
                tracking_fingerprint="6" * 64,
                query_ordinal=0,
                page_no=1,
                now=NOW,
                lease_until=NOW + timedelta(seconds=1),
            )
        later = NOW + timedelta(seconds=2)
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            new_run = await create_run(repository, now=later)
            claim = await repository.claim_query(
                run_id=new_run,
                query_fingerprint="5" * 64,
                tracking_fingerprint="6" * 64,
                query_ordinal=0,
                page_no=1,
                now=later,
                lease_until=later + timedelta(seconds=60),
            )
            old = await repository.get_run(old_run)

        assert old is not None and old.state == DiscoveryRunState.UNCERTAIN.value
        assert claim.disposition is DiscoveryClaimDisposition.CALL
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_run_that_crashes_before_first_claim_is_recovered_as_uncertain(
    tmp_path: Path,
) -> None:
    db = await database(tmp_path)
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            abandoned_run = await create_run(repository)

        later = NOW + timedelta(seconds=61)
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            new_run = await create_run(repository, now=later)
            claim = await repository.claim_query(
                run_id=new_run,
                query_fingerprint="7" * 64,
                tracking_fingerprint="8" * 64,
                query_ordinal=0,
                page_no=1,
                now=later,
                lease_until=later + timedelta(seconds=60),
            )
            abandoned = await repository.get_run(abandoned_run)

        assert abandoned is not None
        assert abandoned.state == DiscoveryRunState.UNCERTAIN.value
        assert abandoned.error_code == "ALIEXPRESS_DISCOVERY_LEASE_EXPIRED"
        assert claim.disposition is DiscoveryClaimDisposition.CALL
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_tracking_rotation_is_a_cache_miss_for_the_same_query(tmp_path: Path) -> None:
    db = await database(tmp_path)
    query_fp = "9" * 64
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            owner = await create_run(repository)
            first = await repository.claim_query(
                run_id=owner,
                query_fingerprint=query_fp,
                tracking_fingerprint="a" * 64,
                query_ordinal=0,
                page_no=1,
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )
            await repository.finish_query_success(
                run_id=owner,
                query_fingerprint=query_fp,
                tracking_fingerprint="a" * 64,
                lease_token=first.lease_token,
                page=DiscoveryPage((product(),), 1, 1),
                now=NOW,
            )

        async with db.session() as session:
            repository = DiscoveryRepository(session)
            rotated = await create_run(repository, now=NOW + timedelta(seconds=1))
            claim = await repository.claim_query(
                run_id=rotated,
                query_fingerprint=query_fp,
                tracking_fingerprint="b" * 64,
                query_ordinal=0,
                page_no=1,
                now=NOW + timedelta(seconds=1),
                lease_until=NOW + timedelta(seconds=61),
            )

        assert claim.disposition is DiscoveryClaimDisposition.CALL
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_discovery_repository_never_creates_productive_records(tmp_path: Path) -> None:
    db = await database(tmp_path)
    try:
        async with db.session() as session:
            repository = DiscoveryRepository(session)
            await create_run(repository)
            assert await session.scalar(select(func.count()).select_from(DealModel)) == 0
            assert await session.scalar(select(func.count()).select_from(DeliveryModel)) == 0
    finally:
        await db.dispose()
