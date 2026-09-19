"""Conservative, offline matching of structured SKU requirements."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from promo_bot.discovery.config import SkuRefinementRequirement
from promo_bot.providers.aliexpress.discovery_sku import DiscoverySku, DiscoverySkuPage

SkuMatchState = Literal["MATCHED", "NO_MATCH", "AMBIGUOUS", "REVIEW_REQUIRED"]


@dataclass(frozen=True, slots=True)
class SkuMatchResult:
    state: SkuMatchState
    selected: DiscoverySku | None
    alternatives: tuple[DiscoverySku, ...]


def match_discovery_skus(
    page: DiscoverySkuPage, requirements: Sequence[SkuRefinementRequirement]
) -> SkuMatchResult:
    """Choose only an unambiguous SKU from complete, structured evidence."""
    if not requirements or not 1 <= len(page.skus) < 20:
        return SkuMatchResult("REVIEW_REQUIRED", None, ())
    for sku in page.skus:
        if not _valid_sku(sku, page.product_id):
            return SkuMatchResult("REVIEW_REQUIRED", None, ())
    alternatives = tuple(sku for sku in page.skus if _matches(sku, requirements))
    if len(alternatives) == 1:
        return SkuMatchResult("MATCHED", alternatives[0], alternatives)
    if alternatives:
        return SkuMatchResult("AMBIGUOUS", None, alternatives)
    return SkuMatchResult("NO_MATCH", None, ())


def _valid_sku(sku: DiscoverySku, product_id: str) -> bool:
    if sku.product_id != product_id or sku.currency != "BRL" or sku.sale_price_with_tax <= 0:
        return False
    attributes: dict[str, str] = {}
    if not sku.attributes:
        return False
    for attribute in sku.attributes:
        name, value = attribute.name.strip().casefold(), attribute.value.strip().casefold()
        if not name or not value or (name in attributes and attributes[name] != value):
            return False
        attributes[name] = value
    return True


def _matches(sku: DiscoverySku, requirements: Sequence[SkuRefinementRequirement]) -> bool:
    attributes = tuple(
        (attribute.name.strip().casefold(), attribute.value.strip().casefold())
        for attribute in sku.attributes
    )
    return all(
        any(
            name in requirement.property_names and value in requirement.accepted_values
            for name, value in attributes
        )
        for requirement in requirements
    )
