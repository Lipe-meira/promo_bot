"""Pure SKU-price history evaluation, distinct from product-level ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class HistoricalSkuPrice:
    refinement_run_id: int
    product_id: str
    sku_id: str
    price: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SkuPriceHistory:
    history_median: Decimal | None
    history_snapshot_count: int
    price_drop_percent: Decimal | None
    classification: str


def rank_sku_price(
    *,
    product_id: str,
    sku_id: str,
    current_run_id: int,
    sale_price_with_tax: Decimal,
    prior_snapshots: tuple[HistoricalSkuPrice, ...],
    observed_at: datetime,
    minimum_drop_percent: Decimal,
) -> SkuPriceHistory:
    if sale_price_with_tax <= 0 or minimum_drop_percent <= 0:
        raise ValueError("ALIEXPRESS_DISCOVERY_SKU_RANK_INPUT_INVALID")
    eligible: dict[int, Decimal] = {}
    for prior in prior_snapshots:
        if (
            prior.product_id == product_id
            and prior.sku_id == sku_id
            and prior.refinement_run_id != current_run_id
            and observed_at - timedelta(days=30) <= prior.observed_at < observed_at
            and prior.price > 0
        ):
            eligible.setdefault(prior.refinement_run_id, prior.price)
    prices = sorted(eligible.values())
    if len(prices) < 2:
        return SkuPriceHistory(None, len(prices), None, "SKU_BASELINE_ONLY")
    middle = len(prices) // 2
    median = prices[middle] if len(prices) % 2 else (prices[middle - 1] + prices[middle]) / 2
    drop = (median - sale_price_with_tax) / median * Decimal("100")
    classification = (
        "SKU_HISTORY_BACKED_PRICE_DROP" if drop >= minimum_drop_percent else "SKU_BASELINE_ONLY"
    )
    return SkuPriceHistory(median, len(prices), drop, classification)
