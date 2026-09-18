from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from promo_bot.database.aliexpress_discovery_repository import DiscoveryRepository
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import AliExpressDiscoveryRunResultModel
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.discovery.ranking import HistoricalPrice, rank_discovery_product
from promo_bot.providers.aliexpress.discovery import DiscoveryProduct

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def product(
    *,
    price: str | None = "90",
    discount: str | None = None,
    commission: str | None = None,
    volume: int | None = None,
    completeness: int = 0,
) -> DiscoveryProduct:
    return DiscoveryProduct(
        product_id="1005000000000099",
        title=None,
        image_url=None,
        first_category_id=None,
        first_category_name=None,
        second_category_id=None,
        second_category_name=None,
        shop_id=None,
        shop_name=None,
        target_brl_price=Decimal(price) if price is not None else None,
        observed_prices=(),
        declared_discount_percent=Decimal(discount) if discount is not None else None,
        commission_rate=Decimal(commission) if commission is not None else None,
        hot_product_commission_rate=None,
        volume=volume,
        completeness_score=completeness,
        diagnostics=frozenset(),
    )


def history(*prices: str) -> tuple[HistoricalPrice, ...]:
    return tuple(
        HistoricalPrice(Decimal(price), NOW - timedelta(days=index + 1))
        for index, price in enumerate(prices)
    )


def test_first_observation_is_only_a_baseline() -> None:
    score = rank_discovery_product(product(price="90"), (), Decimal("5"))

    assert score.classification == "BASELINE_ONLY"
    assert score.history_snapshot_count == 0
    assert score.history_score == 0
    assert score.price_drop_percent is None


@pytest.mark.parametrize(
    ("price", "classification", "history_score"),
    [
        ("95.01", "BASELINE_ONLY", 0),
        ("95", "HISTORY_BACKED_PRICE_DROP", 10),
    ],
)
def test_two_prior_snapshots_and_minimum_drop_control_history_classification(
    price: str, classification: str, history_score: int
) -> None:
    score = rank_discovery_product(product(price=price), history("90", "110"), Decimal("5"))

    assert score.history_median == Decimal("100")
    assert score.history_snapshot_count == 2
    assert score.classification == classification
    assert score.history_score == history_score


@pytest.mark.parametrize(
    ("volume", "expected"),
    [(None, 0), (0, 0), (1, 2), (10, 4), (50, 6), (200, 8), (1000, 10)],
)
def test_volume_score_bands(volume: int | None, expected: int) -> None:
    score = rank_discovery_product(product(volume=volume), (), Decimal("5"))

    assert score.volume_score == expected


def test_provider_signals_score_but_do_not_prove_historical_promotion() -> None:
    score = rank_discovery_product(
        product(discount="40", commission="9.8", completeness=10), (), Decimal("5")
    )

    assert score.classification == "PROVIDER_DISCOUNT_ONLY"
    assert score.discount_score == 15
    assert score.commission_score == 5
    assert score.completeness_score == 10
    assert score.total_score == 30


def test_missing_brl_price_is_insufficient_even_with_provider_discount() -> None:
    score = rank_discovery_product(product(price=None, discount="50"), history("80", "100"), 5)

    assert score.classification == "INSUFFICIENT_DATA"
    assert score.history_score == 0
    assert score.history_median is None


@pytest.mark.asyncio
async def test_repository_uses_only_two_recent_prior_runs_for_median(tmp_path: Path) -> None:
    database_path = tmp_path / "ranking.sqlite3"
    await upgrade_database_async(shadow_database_url(database_path))
    database = create_affiliate_shadow_database(database_path)
    try:
        async with database.session() as session:
            repository = DiscoveryRepository(session)
            for index, (price, observed_at) in enumerate(
                (
                    ("100", NOW - timedelta(days=40)),
                    ("100", NOW - timedelta(days=10)),
                    ("120", NOW - timedelta(days=5)),
                )
            ):
                run_id = await repository.create_run(
                    profile_name=f"history-{index}",
                    profile_fingerprint=f"{index}" * 64,
                    page_size=1,
                    max_pages=1,
                    max_results=1,
                    max_api_calls=1,
                    minimum_drop_percent=Decimal("5"),
                    now=observed_at,
                    lease_until=observed_at + timedelta(seconds=60),
                )
                await repository.record_product(
                    run_id=run_id,
                    query_fingerprint=f"{index + 1}" * 64,
                    tracking_fingerprint="f" * 64,
                    product=product(price=price),
                    origin="LIVE",
                    observed_at=observed_at,
                )

            current_run = await repository.create_run(
                profile_name="current",
                profile_fingerprint="a" * 64,
                page_size=1,
                max_pages=1,
                max_results=1,
                max_api_calls=1,
                minimum_drop_percent=Decimal("5"),
                now=NOW,
                lease_until=NOW + timedelta(seconds=60),
            )
            await repository.record_product(
                run_id=current_run,
                query_fingerprint="b" * 64,
                tracking_fingerprint="f" * 64,
                product=product(price="99"),
                origin="LIVE",
                observed_at=NOW,
            )
            result = await session.scalar(
                select(AliExpressDiscoveryRunResultModel).where(
                    AliExpressDiscoveryRunResultModel.run_id == current_run
                )
            )

        assert result is not None
        assert result.history_snapshot_count == 2
        assert result.history_median == Decimal("110")
        assert result.price_drop_percent == Decimal("10")
        assert result.classification == "HISTORY_BACKED_PRICE_DROP"
        assert result.history_score == 20
    finally:
        await database.dispose()
