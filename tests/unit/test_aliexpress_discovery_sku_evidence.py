from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from promo_bot.database.aliexpress_discovery_repository import DiscoveryRepository
from promo_bot.database.aliexpress_discovery_sku_repository import (
    SkuClaimDisposition,
    SkuRefinementRepository,
    sku_query_fingerprint,
)
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.providers.aliexpress.discovery_sku import (
    DiscoverySku,
    DiscoverySkuAttribute,
    DiscoverySkuPage,
)

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


def page() -> DiscoverySkuPage:
    return DiscoverySkuPage(
        product_id="1005000000000001",
        skus=(
            DiscoverySku(
                product_id="1005000000000001",
                sku_id="120000000000001",
                currency="BRL",
                price_with_tax=Decimal("129.90"),
                sale_price_with_tax=Decimal("119.90"),
                discount_percent=Decimal("8"),
                attributes=(DiscoverySkuAttribute("ROM", "1 TB"),),
            ),
        ),
    )


async def database(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "sku-shadow.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    return create_affiliate_shadow_database(path)


async def create_source_and_refinement(
    db,
    *,
    now: datetime = NOW,  # type: ignore[no-untyped-def]
) -> int:
    async with db.session() as session:
        source = await DiscoveryRepository(session).create_run(
            profile_name="hardware-gamer-br",
            profile_fingerprint="a" * 64,
            page_size=5,
            max_pages=1,
            max_results=5,
            max_api_calls=1,
            minimum_drop_percent=Decimal("5"),
            now=now,
            lease_until=now + timedelta(minutes=1),
        )
        return await SkuRefinementRepository(session).create_run(
            source_run_id=source,
            profile_name="hardware-gamer-br",
            requirements_fingerprint="b" * 64,
            max_refined_products=5,
            max_sku_api_calls=5,
            minimum_drop_percent=Decimal("5"),
            now=now,
            lease_until=now + timedelta(minutes=1),
        )


def test_sku_query_fingerprint_has_separate_context_and_secret_rotation() -> None:
    a = sku_query_fingerprint("secret-a", product_id="1005000000000001")
    b = sku_query_fingerprint("secret-b", product_id="1005000000000001")
    c = sku_query_fingerprint("secret-a", product_id="1005000000000002")
    assert len(a) == 64
    assert a != b != c
    assert "1005000000000001" not in a


@pytest.mark.asyncio
async def test_a_claims_b_stops_immediately_and_c_uses_cache(tmp_path: Path) -> None:
    db = await database(tmp_path)
    fingerprint = "1" * 64
    try:
        run_a = await create_source_and_refinement(db)
        run_b = await create_source_and_refinement(db)
        async with db.session() as session:
            claim_a = await SkuRefinementRepository(session).claim_sku(
                run_id=run_a,
                sku_query_fingerprint=fingerprint,
                product_id="1005000000000001",
                now=NOW,
                lease_until=NOW + timedelta(minutes=1),
            )
        async with db.session() as session:
            claim_b = await SkuRefinementRepository(session).claim_sku(
                run_id=run_b,
                sku_query_fingerprint=fingerprint,
                product_id="1005000000000001",
                now=NOW,
                lease_until=NOW + timedelta(minutes=1),
            )
        assert claim_a.disposition is SkuClaimDisposition.CALL
        assert claim_b.disposition is SkuClaimDisposition.IN_PROGRESS
        async with db.session() as session:
            await SkuRefinementRepository(session).finish_sku_success(
                run_id=run_a,
                sku_query_fingerprint=fingerprint,
                lease_token=claim_a.lease_token,
                page=page(),
                now=NOW + timedelta(seconds=1),
            )
        run_c = await create_source_and_refinement(db, now=NOW + timedelta(seconds=2))
        async with db.session() as session:
            claim_c = await SkuRefinementRepository(session).claim_sku(
                run_id=run_c,
                sku_query_fingerprint=fingerprint,
                product_id="1005000000000001",
                now=NOW + timedelta(seconds=2),
                lease_until=NOW + timedelta(minutes=1),
            )
        assert claim_c.disposition is SkuClaimDisposition.CACHE
        assert claim_c.cached_page == page()
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_cache_expires_after_exactly_fifteen_minutes(tmp_path: Path) -> None:
    db = await database(tmp_path)
    fingerprint = "2" * 64
    try:
        run_a = await create_source_and_refinement(db)
        async with db.session() as session:
            repository = SkuRefinementRepository(session)
            claim = await repository.claim_sku(
                run_id=run_a,
                sku_query_fingerprint=fingerprint,
                product_id="1005000000000001",
                now=NOW,
                lease_until=NOW + timedelta(minutes=1),
            )
            await repository.finish_sku_success(
                run_id=run_a,
                sku_query_fingerprint=fingerprint,
                lease_token=claim.lease_token,
                page=page(),
                now=NOW,
            )
        later = NOW + timedelta(minutes=15)
        run_b = await create_source_and_refinement(db, now=later)
        run_c = await create_source_and_refinement(db, now=later)
        async with db.session() as session:
            claim_b = await SkuRefinementRepository(session).claim_sku(
                run_id=run_b,
                sku_query_fingerprint=fingerprint,
                product_id="1005000000000001",
                now=later,
                lease_until=later + timedelta(minutes=1),
            )
        async with db.session() as session:
            claim_c = await SkuRefinementRepository(session).claim_sku(
                run_id=run_c,
                sku_query_fingerprint=fingerprint,
                product_id="1005000000000001",
                now=later,
                lease_until=later + timedelta(minutes=1),
            )
        assert claim_b.disposition is SkuClaimDisposition.CALL
        assert claim_c.disposition is SkuClaimDisposition.IN_PROGRESS
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_expired_lease_marks_prior_run_uncertain(tmp_path: Path) -> None:
    db = await database(tmp_path)
    try:
        run_a = await create_source_and_refinement(db)
        async with db.session() as session:
            await SkuRefinementRepository(session).claim_sku(
                run_id=run_a,
                sku_query_fingerprint="3" * 64,
                product_id="1005000000000001",
                now=NOW,
                lease_until=NOW + timedelta(seconds=1),
            )
        later = NOW + timedelta(minutes=2)
        run_b = await create_source_and_refinement(db, now=later)
        async with db.session() as session:
            repository = SkuRefinementRepository(session)
            claim = await repository.claim_sku(
                run_id=run_b,
                sku_query_fingerprint="3" * 64,
                product_id="1005000000000001",
                now=later,
                lease_until=later + timedelta(minutes=1),
            )
            old = await repository.get_run(run_a)
        assert claim.disposition is SkuClaimDisposition.CALL
        assert old is not None and old.state == "UNCERTAIN"
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_sku_snapshot_constraints_reject_invalid_history_and_duplicate_identity(
    tmp_path: Path,
) -> None:
    db = await database(tmp_path)
    try:
        run_id = await create_source_and_refinement(db)
    finally:
        await db.dispose()

    statement = (
        "INSERT INTO aliexpress_discovery_sku_price_snapshots "
        "(refinement_run_id,product_id,sku_id,price,currency,observed_at,price_basis,"
        "source_operation) VALUES (?,?,?,?,?,?,?,?)"
    )
    valid = (
        run_id,
        "1005000000000001",
        "120000000000001",
        "119.90",
        "BRL",
        NOW.isoformat(),
        "SALE_PRICE_WITH_TAX",
        "aliexpress.affiliate.product.sku.detail.get",
    )
    with sqlite3.connect(tmp_path / "sku-shadow.sqlite3") as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for index, invalid in [(3, "0"), (4, "USD"), (6, "PRODUCT_MINIMUM"), (7, "unknown")]:
            values = list(valid)
            values[index] = invalid
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, values)
        connection.execute(statement, valid)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, valid)
        assert connection.execute(
            "SELECT COUNT(*) FROM aliexpress_discovery_price_snapshots"
        ).fetchone() == (0,)
