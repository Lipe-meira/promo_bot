"""Fail-closed publication policy for AliExpress snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from promo_bot.domain.models import Money
from promo_bot.providers.aliexpress.models import (
    AliExpressProductSnapshot,
    PriceDisplayMode,
    PriceScope,
)
from promo_bot.providers.base import ProviderError

OFFICIAL_IMAGE_HOSTS = frozenset({"ae01.alicdn.com", "ae-pic-a1.aliexpress-media.com"})


@dataclass(frozen=True, slots=True)
class PricePresentation:
    minimum: Money
    maximum: Money
    mode: PriceDisplayMode
    selected_sku_id: str | None = None


def select_publishable_price(snapshot: AliExpressProductSnapshot) -> PricePresentation:
    if snapshot.currency != "BRL":
        raise ProviderError("ALIEXPRESS_CURRENCY_INCOMPATIBLE", retryable=False)
    if snapshot.available is not True:
        raise ProviderError(
            "ALIEXPRESS_AVAILABILITY_UNCONFIRMED",
            retryable=False,
            manual_review=snapshot.available is None,
        )
    if snapshot.price_min.amount <= 0 or snapshot.price_max.amount <= 0:
        raise ProviderError("ALIEXPRESS_NON_POSITIVE_PRICE", retryable=False)

    if snapshot.reference.requested_sku_id is not None:
        if (
            snapshot.selected_sku_id != snapshot.reference.requested_sku_id
            or snapshot.selected_price is None
            or snapshot.price_scope is not PriceScope.SKU
        ):
            raise ProviderError(
                "ALIEXPRESS_SKU_NOT_CONFIRMED",
                retryable=False,
                manual_review=True,
            )
        if snapshot.selected_price.amount <= 0:
            raise ProviderError("ALIEXPRESS_NON_POSITIVE_PRICE", retryable=False)
        return PricePresentation(
            minimum=snapshot.selected_price,
            maximum=snapshot.selected_price,
            mode=PriceDisplayMode.EXACT,
            selected_sku_id=snapshot.selected_sku_id,
        )

    if snapshot.price_min.amount != snapshot.price_max.amount:
        if snapshot.price_scope is not PriceScope.RANGE:
            raise ProviderError(
                "ALIEXPRESS_PRICE_RANGE_REQUIRES_REVIEW",
                retryable=False,
                manual_review=True,
            )
        return PricePresentation(
            minimum=snapshot.price_min,
            maximum=snapshot.price_max,
            mode=PriceDisplayMode.RANGE,
        )

    mode = (
        PriceDisplayMode.EXACT
        if snapshot.price_scope is PriceScope.SKU
        else PriceDisplayMode.STARTING_AT
    )
    return PricePresentation(snapshot.price_min, snapshot.price_max, mode)


def validated_official_image_url(url: str | None) -> str | None:
    if url is None:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        return None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.hostname.casefold() not in OFFICIAL_IMAGE_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        return None
    return url
