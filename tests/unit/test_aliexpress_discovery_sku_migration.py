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
