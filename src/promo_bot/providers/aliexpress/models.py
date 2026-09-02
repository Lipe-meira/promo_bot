"""Internal AliExpress DTOs independent from Open Platform response envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlsplit

from promo_bot.domain.models import Money, ensure_utc


class PriceScope(StrEnum):
    PRODUCT = "PRODUCT"
    SKU = "SKU"
    RANGE = "RANGE"


class PriceDisplayMode(StrEnum):
    EXACT = "EXACT"
    STARTING_AT = "STARTING_AT"
    RANGE = "RANGE"


@dataclass(frozen=True, slots=True)
class AliExpressProductReference:
    external_product_id: str
    canonical_url: str
    requested_sku_id: str | None = None

    def __post_init__(self) -> None:
        if not self.external_product_id.isdigit():
            raise ValueError("AliExpress product ID must contain only digits")
        expected = f"https://www.aliexpress.com/item/{self.external_product_id}.html"
        if self.requested_sku_id is not None:
            expected = f"{expected}?sku_id={self.requested_sku_id}"
        if self.canonical_url != expected:
            raise ValueError("AliExpress reference must use the canonical product URL")
        if self.requested_sku_id is not None and not self.requested_sku_id.isdigit():
            raise ValueError("AliExpress SKU ID must contain only digits")

    @property
    def variation_key(self) -> str:
        return f"sku_id:{self.requested_sku_id}" if self.requested_sku_id else ""


@dataclass(frozen=True, slots=True)
class AliExpressProduct:
    product_id: str
    title: str | None
    detail_url: str | None
    original_price: Money | None = None
    sale_price: Money | None = None
    target_sale_price: Money | None = None
    app_sale_price: Money | None = None
    target_app_sale_price: Money | None = None
    image_url: str | None = None
    seller: str | None = None
    shop_id: str | None = None
    sku_id: str | None = None
    commission_rate: Decimal | None = None
    hot_product_commission_rate: Decimal | None = None
    relevant_market_commission_rate: Decimal | None = None
    tax_rate: Decimal | None = None
    queried_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.product_id.isdigit():
            raise ValueError("AliExpress product ID must contain only digits")
        if self.sku_id is not None and not self.sku_id.isdigit():
            raise ValueError("AliExpress SKU ID must contain only digits")
        if self.shop_id is not None and not self.shop_id.isdigit():
            raise ValueError("AliExpress shop ID must contain only digits")
        if self.queried_at is not None:
            object.__setattr__(self, "queried_at", ensure_utc(self.queried_at))


@dataclass(frozen=True, slots=True)
class AliExpressSku:
    product_id: str
    sku_id: str
    price_with_tax: Money | None
    sale_price_with_tax: Money | None
    shipping_fee: Money | None = None
    tax_rate: Decimal | None = None
    color: str | None = None
    size: str | None = None
    properties: str | None = None
    image_url: str | None = None
    ship_from_country: str | None = None
    delivery_days: str | None = None
    min_delivery_days: str | None = None
    max_delivery_days: str | None = None

    def __post_init__(self) -> None:
        if not self.product_id.isdigit() or not self.sku_id.isdigit():
            raise ValueError("AliExpress product and SKU IDs must contain only digits")
        currencies = {
            money.currency
            for money in (self.price_with_tax, self.sale_price_with_tax, self.shipping_fee)
            if money is not None
        }
        if len(currencies) > 1:
            raise ValueError("SKU monetary values must use one currency")


@dataclass(frozen=True, slots=True)
class AliExpressShipping:
    fee: Money | None
    delivery_days: str | None
    min_delivery_days: str | None
    max_delivery_days: str | None
    ship_from_country: str | None


@dataclass(frozen=True, slots=True)
class AliExpressPromotion:
    product_id: str
    redemption_channel: str | None
    minimum_purchase: Money | None
    money_off: Money | None
    generic_code: str | None
    product_applicability: str | None
    value_type: str | None
    offer_type: str | None
    title: str | None
    effective_dates: str | None


@dataclass(frozen=True, slots=True)
class PromotionLinkMapping:
    source_value: str
    promotion_link: str


@dataclass(frozen=True, slots=True)
class AffiliateLinkProof:
    operation: str
    requested_at: datetime
    responded_at: datetime
    source_external_product_id: str
    canonical_url: str
    short_link: str
    official_endpoint_host: str
    credential_profile_id: str
    contract_version: str
    official_response_validated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_at", ensure_utc(self.requested_at))
        object.__setattr__(self, "responded_at", ensure_utc(self.responded_at))
        if self.responded_at < self.requested_at:
            raise ValueError("affiliate response cannot precede the request")
        if not self.source_external_product_id.isdigit():
            raise ValueError("affiliate proof must preserve the AliExpress product ID")
        required = (
            self.operation,
            self.canonical_url,
            self.short_link,
            self.official_endpoint_host,
            self.credential_profile_id,
            self.contract_version,
        )
        if not all(value.strip() for value in required):
            raise ValueError("affiliate proof fields cannot be empty")
        _require_safe_https(self.canonical_url)
        _require_safe_https(self.short_link)


@dataclass(frozen=True, slots=True)
class AliExpressProductSnapshot:
    reference: AliExpressProductReference
    title: str
    price_min: Money
    price_max: Money
    price_scope: PriceScope
    queried_at: datetime
    selected_sku_id: str | None = None
    selected_price: Money | None = None
    available: bool | None = None
    image_url: str | None = None
    seller: str | None = None
    commission_rate: Decimal | None = None
    commission_amount: Money | None = None
    shipping_fee: Money | None = None
    source_operation: str = "aliexpress.affiliate.productdetail.get"

    def __post_init__(self) -> None:
        object.__setattr__(self, "queried_at", ensure_utc(self.queried_at))
        if not self.title.strip():
            raise ValueError("product title cannot be empty")
        if self.price_min.currency != self.price_max.currency:
            raise ValueError("price range must use one currency")
        if self.price_min.amount > self.price_max.amount:
            raise ValueError("price_min cannot exceed price_max")
        if self.selected_sku_id is not None and not self.selected_sku_id.isdigit():
            raise ValueError("selected SKU ID must contain only digits")
        if (self.selected_sku_id is None) != (self.selected_price is None):
            raise ValueError("selected SKU and selected price must be supplied together")
        for money in (self.selected_price, self.commission_amount, self.shipping_fee):
            if money is not None and money.currency != self.price_min.currency:
                raise ValueError("snapshot monetary values must use one currency")

    @property
    def currency(self) -> str:
        return self.price_min.currency


@dataclass(frozen=True, slots=True)
class EnrichedAffiliateOffer:
    product: AliExpressProductSnapshot
    affiliate_proof: AffiliateLinkProof

    def __post_init__(self) -> None:
        reference = self.product.reference
        proof = self.affiliate_proof
        if not proof.official_response_validated:
            raise ValueError("an enriched offer requires a validated official response")
        if reference.external_product_id != proof.source_external_product_id:
            raise ValueError("affiliate proof belongs to another product")
        if reference.canonical_url != proof.canonical_url:
            raise ValueError("affiliate proof belongs to another canonical URL")


def _require_safe_https(url: str) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError) as exc:
        raise ValueError("affiliate proof URL is malformed") from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise ValueError("affiliate proof URL must be a safe HTTPS URL")
