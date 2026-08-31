"""Fail-closed presentation policy for Shopee product snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from promo_bot.domain.models import Money
from promo_bot.providers.base import ProviderError
from promo_bot.providers.shopee.models import ProductSnapshot


class PriceDisplayMode(StrEnum):
    EXACT = "EXACT"
    STARTING_AT = "STARTING_AT"


@dataclass(frozen=True, slots=True)
class PricePresentation:
    price: Money
    mode: PriceDisplayMode
    variation_id: str | None = None


def select_publishable_price(snapshot: ProductSnapshot) -> PricePresentation:
    if snapshot.currency != "BRL":
        raise ProviderError("SHOPEE_CURRENCY_NOT_SUPPORTED", retryable=False)
    if not snapshot.available:
        raise ProviderError("SHOPEE_PRODUCT_UNAVAILABLE", retryable=False)

    requested_variation = snapshot.reference.requested_variation_id
    if requested_variation is not None:
        if (
            snapshot.selected_variation_id != requested_variation
            or snapshot.selected_variation_price is None
            or snapshot.selected_variation_available is None
        ):
            raise ProviderError(
                "SHOPEE_VARIATION_NOT_CONFIRMED",
                retryable=False,
                manual_review=True,
            )
        if not snapshot.selected_variation_available:
            raise ProviderError("SHOPEE_VARIATION_UNAVAILABLE", retryable=False)
        return PricePresentation(
            snapshot.selected_variation_price,
            PriceDisplayMode.EXACT,
            variation_id=requested_variation,
        )

    if snapshot.price_min.amount == snapshot.price_max.amount:
        return PricePresentation(snapshot.price_min, PriceDisplayMode.EXACT)
    if snapshot.range_semantics_confirmed:
        return PricePresentation(snapshot.price_min, PriceDisplayMode.STARTING_AT)
    raise ProviderError(
        "SHOPEE_PRICE_RANGE_REQUIRES_REVIEW",
        retryable=False,
        manual_review=True,
    )


def validated_official_image_url(url: str | None, *, allowed_hosts: frozenset[str]) -> str | None:
    if url is None or not allowed_hosts:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return None
    if (
        parts.scheme != "https"
        or not host
        or host.casefold() not in allowed_hosts
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        return None
    return url
