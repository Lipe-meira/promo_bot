from __future__ import annotations

from decimal import Decimal

import pytest

from promo_bot.discovery.config import SkuRefinementRequirement
from promo_bot.discovery.sku_matcher import match_discovery_skus
from promo_bot.providers.aliexpress.discovery_sku import (
    DiscoverySku,
    DiscoverySkuAttribute,
    DiscoverySkuPage,
)


def sku(
    sku_id: str,
    *attributes: tuple[str, str],
    price: Decimal = Decimal("119.90"),
) -> DiscoverySku:
    return DiscoverySku(
        product_id="1005000000000001",
        sku_id=sku_id,
        currency="BRL",
        price_with_tax=None,
        sale_price_with_tax=price,
        discount_percent=None,
        attributes=tuple(DiscoverySkuAttribute(name, value) for name, value in attributes),
    )


def requirement(dimension: str, names: list[str], values: list[str]) -> SkuRefinementRequirement:
    return SkuRefinementRequirement(
        dimension=dimension, property_names=names, accepted_values=values
    )


def test_matches_all_explicit_requirements_on_the_same_sku() -> None:
    page = DiscoverySkuPage(
        product_id="1005000000000001",
        skus=(
            sku("101", ("ROM", "1 TB"), ("Color", "Preto")),
            sku("102", ("ROM", "250 GB"), ("Color", "Preto")),
        ),
    )
    result = match_discovery_skus(
        page,
        (
            requirement("capacity", ["ROM", "Capacity"], ["1 TB"]),
            requirement("color", ["Color"], ["Preto"]),
        ),
    )
    assert result.state == "MATCHED"
    assert result.selected is page.skus[0]
    assert result.alternatives == (page.skus[0],)


def test_does_not_join_requirements_across_skus_or_convert_units() -> None:
    page = DiscoverySkuPage(
        product_id="1005000000000001",
        skus=(
            sku("101", ("ROM", "1024 GB"), ("Color", "Preto")),
            sku("102", ("ROM", "1 TB"), ("Color", "Azul")),
        ),
    )
    result = match_discovery_skus(
        page,
        (
            requirement("capacity", ["ROM"], ["1 TB"]),
            requirement("color", ["Color"], ["Preto"]),
        ),
    )
    assert result.state == "NO_MATCH"
    assert result.selected is None
    assert result.alternatives == ()


def test_ambiguous_matches_preserve_alternatives_but_select_nothing() -> None:
    page = DiscoverySkuPage(
        product_id="1005000000000001",
        skus=(sku("101", ("ROM", "1 TB")), sku("102", ("ROM", "1 TB"))),
    )
    result = match_discovery_skus(page, (requirement("capacity", ["ROM"], ["1 TB"]),))
    assert result.state == "AMBIGUOUS"
    assert result.selected is None
    assert result.alternatives == page.skus


@pytest.mark.parametrize(
    "page",
    [
        DiscoverySkuPage(product_id="1005000000000001", skus=()),
        DiscoverySkuPage(
            product_id="1005000000000001",
            skus=(sku("101", ("ROM", "1 TB"), price=Decimal("0")),),
        ),
        DiscoverySkuPage(
            product_id="1005000000000001",
            skus=(sku("101", ("ROM", "1 TB"), ("rom", "250 GB")),),
        ),
    ],
)
def test_incomplete_or_conflicting_evidence_requires_review(page: DiscoverySkuPage) -> None:
    result = match_discovery_skus(page, (requirement("capacity", ["ROM"], ["1 TB"]),))
    assert result.state == "REVIEW_REQUIRED"
    assert result.selected is None


def test_preserves_internal_whitespace_during_comparison() -> None:
    page = DiscoverySkuPage(
        product_id="1005000000000001",
        skus=(sku("101", (" ROM ", " 1  TB ")),),
    )
    result = match_discovery_skus(page, (requirement("capacity", ["ROM"], ["1 TB"]),))
    assert result.state == "NO_MATCH"
