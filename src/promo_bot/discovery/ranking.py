"""Pure, explainable ranking for AliExpress discovery products."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from promo_bot.providers.aliexpress.discovery import DiscoveryProduct


@dataclass(frozen=True, slots=True)
class HistoricalPrice:
    price: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DiscoveryScore:
    history_median: Decimal | None
    history_snapshot_count: int
    price_drop_percent: Decimal | None
    history_score: int
    discount_score: int
    volume_score: int
    commission_score: int
    completeness_score: int
    total_score: int
    classification: str


def rank_discovery_product(
    product: DiscoveryProduct,
    prior_snapshots: tuple[HistoricalPrice, ...],
    minimum_drop_percent: Decimal,
) -> DiscoveryScore:
    minimum_drop = Decimal(minimum_drop_percent)
    discount_score = _bounded_floor(product.declared_discount_percent, divisor=2, maximum=15)
    commission_score = _bounded_floor(product.commission_rate, divisor=1, maximum=5)
    volume_score = _volume_score(product.volume)
    completeness_score = product.completeness_score

    history_median: Decimal | None = None
    price_drop_percent: Decimal | None = None
    history_score = 0
    history_count = 0
    if product.target_brl_price is not None:
        prices = sorted(snapshot.price for snapshot in prior_snapshots if snapshot.price > 0)
        history_count = len(prices)
        if history_count >= 2:
            history_median = _median(prices)
            price_drop_percent = (
                (history_median - product.target_brl_price) / history_median * Decimal("100")
            )
            if price_drop_percent >= minimum_drop:
                history_score = min(60, _floor(price_drop_percent * 2))

    if product.target_brl_price is None:
        classification = "INSUFFICIENT_DATA"
        history_median = None
        price_drop_percent = None
        history_count = 0
    elif (
        history_count >= 2 and price_drop_percent is not None and price_drop_percent >= minimum_drop
    ):
        classification = "HISTORY_BACKED_PRICE_DROP"
    elif product.declared_discount_percent is not None and product.declared_discount_percent > 0:
        classification = "PROVIDER_DISCOUNT_ONLY"
    else:
        classification = "BASELINE_ONLY"

    total_score = (
        history_score + discount_score + volume_score + commission_score + completeness_score
    )
    return DiscoveryScore(
        history_median=history_median,
        history_snapshot_count=history_count,
        price_drop_percent=price_drop_percent,
        history_score=history_score,
        discount_score=discount_score,
        volume_score=volume_score,
        commission_score=commission_score,
        completeness_score=completeness_score,
        total_score=total_score,
        classification=classification,
    )


def _median(prices: list[Decimal]) -> Decimal:
    middle = len(prices) // 2
    if len(prices) % 2:
        return prices[middle]
    return (prices[middle - 1] + prices[middle]) / 2


def _bounded_floor(value: Decimal | None, *, divisor: int, maximum: int) -> int:
    if value is None or value <= 0:
        return 0
    return min(maximum, _floor(value / Decimal(divisor)))


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _volume_score(volume: int | None) -> int:
    if volume is None or volume <= 0:
        return 0
    if volume < 10:
        return 2
    if volume < 50:
        return 4
    if volume < 200:
        return 6
    if volume < 1000:
        return 8
    return 10
