"""Dependency-free domain model."""

from promo_bot.domain.enums import (
    CapabilityStatus,
    ConfidenceLevel,
    CouponStatus,
    DealState,
    DiscoveryOrigin,
    PaymentMethod,
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
    "Money",
    "PaymentCondition",
    "PaymentMethod",
    "PriceHistory",
    "ProcessedItem",
    "Product",
    "SourceMessage",
    "Store",
]
