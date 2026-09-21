from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.shadow import shadow_database_url


@pytest.mark.asyncio
async def test_sku_refinement_schema_is_additive_and_shadow_only(tmp_path: Path) -> None:
    path = tmp_path / "sku-shadow.sqlite3"
    await upgrade_database_async(shadow_database_url(path))

    with sqlite3.connect(path) as connection:
        names = {
            name
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "aliexpress_discovery_sku_refinement_runs",
            "aliexpress_discovery_sku_claims",
            "aliexpress_discovery_sku_cache",
            "aliexpress_discovery_sku_cache_items",
            "aliexpress_discovery_sku_refinement_items",
            "aliexpress_discovery_sku_matches",
            "aliexpress_discovery_sku_price_snapshots",
        } <= names
        assert {
            "aliexpress_discovery_runs",
            "aliexpress_discovery_price_snapshots",
        } <= names
        for name in names:
            if not name.startswith("aliexpress_discovery_sku_"):
                continue
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({name})")}
            assert not {"promotion_link", "product_detail_url", "shop_url"} & columns


@pytest.mark.asyncio
async def test_matched_sku_selection_constraint_rejects_incomplete_direct_sql(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sku-matched-constraint.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    statement = (
        "INSERT INTO aliexpress_discovery_sku_refinement_items "
        "(refinement_run_id, product_id, sku_query_fingerprint, origin, state, "
        "source_product_score, selected_sku_id, sale_price_with_tax, currency, "
        "history_snapshot_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    valid = (
        1,
        "1005000000000001",
        "f" * 64,
        "LIVE",
        "MATCHED",
        80,
        "120000000000001",
        "119.90",
        "BRL",
        0,
    )

    with sqlite3.connect(path) as connection:
        for index, invalid in (
            (6, None),
            (7, None),
            (8, None),
            (7, "0"),
            (7, "-1"),
            (8, "USD"),
        ):
            values = list(valid)
            values[index] = invalid
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, values)

        connection.execute(statement, valid)
        assert connection.execute(
            "SELECT selected_sku_id, sale_price_with_tax, currency "
            "FROM aliexpress_discovery_sku_refinement_items"
        ).fetchone() == ("120000000000001", 119.9, "BRL")
