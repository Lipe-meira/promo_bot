from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from promo_bot.domain.models import Money
from promo_bot.providers.base import ProviderError
from promo_bot.providers.shopee.client import UnavailableShopeeAffiliateClient
from promo_bot.providers.shopee.models import (
    AffiliateLinkProof,
    EnrichedAffiliateOffer,
    ProductSnapshot,
    ProviderProductReference,
    VariationSnapshot,
)
from promo_bot.providers.shopee.policy import (
    PriceDisplayMode,
    select_publishable_price,
    validated_official_image_url,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def reference(*, variation_id: str | None = None) -> ProviderProductReference:
    return ProviderProductReference(
        store="shopee",
        external_product_id="10:20",
        canonical_url="https://shopee.com.br/product/10/20",
        shop_id="10",
        item_id="20",
        requested_variation_id=variation_id,
    )


def snapshot(
    *,
    currency: str = "BRL",
    minimum: str = "90.00",
    maximum: str = "90.00",
    range_confirmed: bool = False,
    variation_id: str | None = None,
    variation_price: str | None = None,
    variation_available: bool | None = None,
) -> ProductSnapshot:
    return ProductSnapshot(
        reference=reference(variation_id=variation_id),
        title="Produto de teste",
        price_min=Money(Decimal(minimum), currency),
        price_max=Money(Decimal(maximum), currency),
        available=True,
        queried_at=NOW,
        selected_variation_id=variation_id,
        selected_variation_price=(
            Money(Decimal(variation_price), currency) if variation_price else None
        ),
        selected_variation_available=variation_available,
        range_semantics_confirmed=range_confirmed,
    )


def proof(*, validated: bool = True) -> AffiliateLinkProof:
    return AffiliateLinkProof(
        provider="shopee_official",
        operation="syntheticOperation",
        requested_at=NOW,
        responded_at=NOW + timedelta(seconds=1),
        source_external_product_id="10:20",
        canonical_url="https://shopee.com.br/product/10/20",
        short_link="https://short.example.test/fixture",
        official_endpoint_host="official.example.test",
        credential_profile_id="default",
        contract_version="fixture-v1",
        sub_ids=("test",),
        official_response_validated=validated,
    )


def test_exact_price_is_publishable() -> None:
    result = select_publishable_price(snapshot())

    assert result.price == Money(Decimal("90.00"))
    assert result.mode is PriceDisplayMode.EXACT


def test_reference_rejects_item_id_as_the_entire_identity() -> None:
    with pytest.raises(ValueError, match="shop_id:item_id"):
        ProviderProductReference(
            store="shopee",
            external_product_id="20",
            canonical_url="https://shopee.com.br/product/10/20",
            shop_id="10",
            item_id="20",
        )


@pytest.mark.parametrize(("minimum", "maximum"), [("0", "0"), ("0", "90")])
def test_non_positive_base_price_is_not_publishable(minimum: str, maximum: str) -> None:
    with pytest.raises(ProviderError) as captured:
        select_publishable_price(
            snapshot(minimum=minimum, maximum=maximum, range_confirmed=minimum != maximum)
        )

    assert captured.value.code == "SHOPEE_NON_POSITIVE_PRICE"
    assert not captured.value.retryable


def test_non_positive_selected_variation_price_is_not_publishable() -> None:
    with pytest.raises(ProviderError) as captured:
        select_publishable_price(
            snapshot(
                minimum="90",
                maximum="120",
                variation_id="model-7",
                variation_price="0",
                variation_available=True,
            )
        )

    assert captured.value.code == "SHOPEE_NON_POSITIVE_PRICE"


def test_price_range_never_becomes_a_single_price() -> None:
    with pytest.raises(ProviderError) as captured:
        select_publishable_price(snapshot(minimum="90", maximum="120"))

    assert captured.value.code == "SHOPEE_PRICE_RANGE_REQUIRES_REVIEW"
    assert captured.value.manual_review


def test_confirmed_price_range_uses_starting_at_mode() -> None:
    result = select_publishable_price(snapshot(minimum="90", maximum="120", range_confirmed=True))

    assert result.mode is PriceDisplayMode.STARTING_AT
    assert result.price.amount == Decimal("90.00")


def test_requested_variation_requires_matching_price_and_availability() -> None:
    result = select_publishable_price(
        snapshot(
            minimum="90",
            maximum="120",
            variation_id="model-7",
            variation_price="110",
            variation_available=True,
        )
    )

    assert result.mode is PriceDisplayMode.EXACT
    assert result.variation_id == "model-7"
    assert result.price.amount == Decimal("110.00")


def test_selected_variation_cannot_mix_price_availability_or_image() -> None:
    with pytest.raises(ValueError, match="inconsistent snapshots"):
        ProductSnapshot(
            reference=reference(variation_id="model-7"),
            title="Produto de teste",
            price_min=Money(Decimal("90")),
            price_max=Money(Decimal("120")),
            available=True,
            queried_at=NOW,
            selected_variation_id="model-7",
            selected_variation_price=Money(Decimal("110")),
            selected_variation_available=True,
            selected_variation_image_url="https://cdn.example.test/wrong.jpg",
            variations=(
                VariationSnapshot(
                    variation_id="model-7",
                    price=Money(Decimal("110")),
                    available=True,
                    image_url="https://cdn.example.test/right.jpg",
                ),
            ),
        )


def test_non_brl_currency_is_not_publishable() -> None:
    with pytest.raises(ProviderError) as captured:
        select_publishable_price(snapshot(currency="USD"))

    assert captured.value.code == "SHOPEE_CURRENCY_NOT_SUPPORTED"


@pytest.mark.parametrize(
    ("url", "allowed", "expected"),
    [
        ("https://cdn.example.test/image.jpg", frozenset(), None),
        ("http://cdn.example.test/image.jpg", frozenset({"cdn.example.test"}), None),
        ("https://unknown.test/image.jpg", frozenset({"cdn.example.test"}), None),
        (
            "https://cdn.example.test/image.jpg",
            frozenset({"cdn.example.test"}),
            "https://cdn.example.test/image.jpg",
        ),
        ("https://cdn.example.test:invalid/image.jpg", frozenset({"cdn.example.test"}), None),
    ],
)
def test_image_policy_fails_closed(url: str, allowed: frozenset[str], expected: str | None) -> None:
    assert validated_official_image_url(url, allowed_hosts=allowed) == expected


def test_host_alone_does_not_prove_affiliation() -> None:
    with pytest.raises(ValueError, match="validated official response"):
        EnrichedAffiliateOffer(product=snapshot(), affiliate_proof=proof(validated=False))


def test_affiliate_proof_rejects_non_https_short_link() -> None:
    with pytest.raises(ValueError, match="safe HTTPS"):
        AffiliateLinkProof(
            provider="shopee_official",
            operation="syntheticOperation",
            requested_at=NOW,
            responded_at=NOW + timedelta(seconds=1),
            source_external_product_id="10:20",
            canonical_url="https://shopee.com.br/product/10/20",
            short_link="http://short.example.test/fixture",
            official_endpoint_host="official.example.test",
            credential_profile_id="default",
            contract_version="fixture-v1",
            official_response_validated=True,
        )


@pytest.mark.asyncio
async def test_real_client_is_explicitly_blocked_at_documentation_gate() -> None:
    with pytest.raises(ProviderError) as captured:
        await UnavailableShopeeAffiliateClient().enrich(reference())

    assert captured.value.code == "SHOPEE_OFFICIAL_CONTRACT_UNAVAILABLE"
    assert not captured.value.retryable
