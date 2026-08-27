"""Core immutable domain values for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from promo_bot.domain.enums import (
    ConfidenceLevel,
    CouponStatus,
    DealState,
    DiscoveryOrigin,
    PaymentMethod,
    Store,
)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include timezone information")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "BRL"

    def __post_init__(self) -> None:
        try:
            normalized = self.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError("money amount must be finite") from exc
        if not normalized.is_finite() or normalized < 0:
            raise ValueError("money amount must be finite and non-negative")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        object.__setattr__(self, "amount", normalized)
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True, slots=True)
class PaymentCondition:
    method: PaymentMethod
    installments: int = 1
    interest_free: bool | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if self.installments < 1:
            raise ValueError("installments must be positive")


@dataclass(frozen=True, slots=True)
class SourceMessage:
    platform: str
    message_id: str
    channel_id: str
    occurred_at: datetime
    original_text: str
    links: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))


@dataclass(frozen=True, slots=True)
class Product:
    store: Store
    external_id: str
    title: str
    canonical_url: str
    currency: str = "BRL"
    image_url: str | None = None
    category: str | None = None
    seller: str | None = None


@dataclass(frozen=True, slots=True)
class Coupon:
    store: Store
    code: str | None
    status: CouponStatus
    source: str
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    fixed_discount: Money | None = None
    percentage_discount: Decimal | None = None
    minimum_purchase: Money | None = None
    maximum_discount: Money | None = None
    allowed_categories: tuple[str, ...] = ()
    allowed_products: tuple[str, ...] = ()
    account_restrictions: str | None = None
    app_only: bool = False
    payment_restrictions: str | None = None
    last_validated_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("starts_at", "ends_at", "last_validated_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, ensure_utc(value))
        if self.percentage_discount is not None and not 0 <= self.percentage_discount <= 100:
            raise ValueError("percentage discount must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class Deal:
    product: Product
    current_price: Money
    final_price: Money
    payment_condition: PaymentCondition
    source: str
    discovery_origin: DiscoveryOrigin
    discovered_at: datetime
    state: DealState = DealState.DISCOVERED
    previous_price: Money | None = None
    freight: Money | None = None
    coupon: Coupon | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    score: int = 0
    discount_percent: Decimal | None = None
    affiliate_link: str | None = None
    last_validated_at: datetime | None = None
    send_status: str = "NOT_SENT"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "discovered_at", ensure_utc(self.discovered_at))
        if self.last_validated_at is not None:
            object.__setattr__(self, "last_validated_at", ensure_utc(self.last_validated_at))
        if not 0 <= self.score <= 100:
            raise ValueError("deal score must be between 0 and 100")
        if self.discount_percent is not None and not 0 <= self.discount_percent <= 100:
            raise ValueError("deal discount percent must be between 0 and 100")
        prices = [self.current_price, self.final_price, self.previous_price, self.freight]
        currencies = {price.currency for price in prices if price is not None}
        if len(currencies) > 1:
            raise ValueError("all deal monetary values must use the same currency")
        if self.state in {DealState.READY, DealState.SENT} and not self.affiliate_link:
            raise ValueError("publishable deals require an official affiliate link")


@dataclass(frozen=True, slots=True)
class PriceHistory:
    product: Product
    price: Money
    payment_condition: PaymentCondition
    collected_at: datetime
    source: str
    freight: Money | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "collected_at", ensure_utc(self.collected_at))
        if self.freight is not None and self.freight.currency != self.price.currency:
            raise ValueError("price and freight must use the same currency")


@dataclass(frozen=True, slots=True)
class ProcessedItem:
    store: Store
    external_product_id: str
    deal_hash: str
    variation_key: str = ""
    last_sent_at: datetime | None = None
    last_price: Money | None = None
    last_coupon: str | None = None
    cooldown_until: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("last_sent_at", "cooldown_until"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, ensure_utc(value))
