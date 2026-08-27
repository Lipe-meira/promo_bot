"""Dependency-free domain model."""

from promo_bot.domain.enums import (
    CapabilityStatus,
    ConfidenceLevel,
    CouponStatus,
    DealState,
    DiscoveryOrigin,
    LinkSource,
    PaymentMethod,
    RelayLinkState,
    SourceMessageState,
    Store,
)
from promo_bot.domain.models import (
    Coupon,
    Deal,
    Money,
    PaymentCondition,
    PriceHistory,
    ProcessedItem,
    Product,
    SourceMessage,
)

__all__ = [
    "CapabilityStatus",
    "ConfidenceLevel",
    "Coupon",
    "CouponStatus",
    "Deal",
    "DealState",
    "DiscoveryOrigin",
    "LinkSource",
    "Money",
    "PaymentCondition",
    "PaymentMethod",
    "PriceHistory",
    "ProcessedItem",
    "Product",
    "RelayLinkState",
    "SourceMessage",
    "SourceMessageState",
    "Store",
]
