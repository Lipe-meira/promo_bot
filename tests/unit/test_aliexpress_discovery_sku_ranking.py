from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from promo_bot.discovery.sku_ranking import HistoricalSkuPrice, rank_sku_price

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


def prior(
    run_id: int,
    price: str,
    *,
    product_id: str = "1005000000000001",
    sku_id: str = "120000000000001",
    observed_at: datetime = NOW - timedelta(days=1),
) -> HistoricalSkuPrice:
    return HistoricalSkuPrice(run_id, product_id, sku_id, Decimal(price), observed_at)


def test_two_distinct_recent_live_sku_snapshots_support_median_drop() -> None:
    result = rank_sku_price(
        product_id="1005000000000001",
        sku_id="120000000000001",
        current_run_id=3,
        sale_price_with_tax=Decimal("80"),
        prior_snapshots=(prior(1, "100"), prior(2, "120")),
        observed_at=NOW,
        minimum_drop_percent=Decimal("5"),
    )
    assert result.classification == "SKU_HISTORY_BACKED_PRICE_DROP"
    assert result.history_median == Decimal("110")
    assert result.history_snapshot_count == 2
    assert result.price_drop_percent == Decimal("300") / Decimal("11")


def test_cross_sku_old_current_and_duplicate_run_snapshots_never_establish_baseline() -> None:
    result = rank_sku_price(
        product_id="1005000000000001",
        sku_id="120000000000001",
        current_run_id=4,
        sale_price_with_tax=Decimal("80"),
        prior_snapshots=(
            prior(1, "100"),
            prior(1, "105"),
            prior(2, "200", sku_id="120000000000002"),
            prior(3, "200", observed_at=NOW - timedelta(days=31)),
            prior(4, "200"),
        ),
        observed_at=NOW,
        minimum_drop_percent=Decimal("5"),
    )
    assert result.classification == "SKU_BASELINE_ONLY"
    assert result.history_snapshot_count == 1
    assert result.history_median is None
    assert result.price_drop_percent is None


def test_provider_product_discount_does_not_make_sku_history_backed() -> None:
    result = rank_sku_price(
        product_id="1005000000000001",
        sku_id="120000000000001",
        current_run_id=3,
        sale_price_with_tax=Decimal("98"),
        prior_snapshots=(prior(1, "100"), prior(2, "100")),
        observed_at=NOW,
        minimum_drop_percent=Decimal("5"),
    )
    assert result.classification == "SKU_BASELINE_ONLY"
    assert result.price_drop_percent == Decimal("2")
