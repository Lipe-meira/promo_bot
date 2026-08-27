from datetime import UTC, datetime
from decimal import Decimal

import pytest

from promo_bot.domain import (
    Deal,
    DealState,
    DiscoveryOrigin,
    Money,
    PaymentCondition,
    PaymentMethod,
    Product,
    Store,
)


def product() -> Product:
    return Product(
        store=Store.KABUM,
        external_id="123",
        title="SSD de teste",
        canonical_url="https://www.kabum.com.br/produto/123",
    )


def test_money_uses_decimal_and_normalizes_currency() -> None:
    value = Money(Decimal("1199.995"), "brl")

    assert value.amount == Decimal("1200.00")
    assert value.currency == "BRL"


@pytest.mark.parametrize("amount", [Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity")])
def test_money_rejects_invalid_values(amount: Decimal) -> None:
    with pytest.raises(ValueError):
        Money(amount)


def test_timestamp_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Deal(
            product=product(),
            current_price=Money(Decimal("100")),
            final_price=Money(Decimal("100")),
            payment_condition=PaymentCondition(PaymentMethod.PIX),
            source="fixture",
            discovery_origin=DiscoveryOrigin.MANUAL,
            discovered_at=datetime(2026, 1, 1),
        )


def test_ready_deal_requires_affiliate_link() -> None:
    with pytest.raises(ValueError, match="affiliate link"):
        Deal(
            product=product(),
            current_price=Money(Decimal("100")),
            final_price=Money(Decimal("90")),
            payment_condition=PaymentCondition(PaymentMethod.PIX),
            source="fixture",
            discovery_origin=DiscoveryOrigin.MANUAL,
            discovered_at=datetime.now(UTC),
            state=DealState.READY,
        )
