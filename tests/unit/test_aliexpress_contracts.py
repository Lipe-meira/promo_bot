from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from promo_bot.domain.models import Money
from promo_bot.providers.aliexpress.client import (
    SIGNING_CONTRACT_UNAVAILABLE,
    UnavailableAliExpressAffiliateClient,
)
from promo_bot.providers.aliexpress.contracts import (
    LINK_GENERATE,
    OfficialShippingInput,
    link_generate_payload,
    product_detail_payload,
    product_shipping_payload,
    promotion_info_payload,
    sku_detail_payload,
)
from promo_bot.providers.aliexpress.models import (
    AffiliateLinkProof,
    AliExpressProductReference,
    AliExpressProductSnapshot,
    EnrichedAffiliateOffer,
    PriceDisplayMode,
    PriceScope,
)
from promo_bot.providers.aliexpress.parsing import (
    parse_link_generate,
    parse_product_detail,
    parse_product_query,
    parse_promotion_info,
    parse_shipping,
    parse_sku_detail,
)
from promo_bot.providers.aliexpress.policy import select_publishable_price
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.providers.base import ProviderError
from promo_bot.stores.urls import canonicalize_store_url

FIXTURES = Path(__file__).parents[1] / "fixtures" / "aliexpress"
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def reference(*, sku_id: str | None = None) -> AliExpressProductReference:
    base = "https://www.aliexpress.com/item/1005000000000001.html"
    return AliExpressProductReference(
        external_product_id="1005000000000001",
        canonical_url=f"{base}?sku_id={sku_id}" if sku_id else base,
        requested_sku_id=sku_id,
    )


def snapshot(
    *,
    currency: str = "BRL",
    minimum: str = "119.90",
    maximum: str = "119.90",
    scope: PriceScope = PriceScope.PRODUCT,
    sku_id: str | None = None,
    selected_price: str | None = None,
) -> AliExpressProductSnapshot:
    return AliExpressProductSnapshot(
        reference=reference(sku_id=sku_id),
        title="Produto de fixture",
        price_min=Money(Decimal(minimum), currency),
        price_max=Money(Decimal(maximum), currency),
        price_scope=scope,
        queried_at=NOW,
        selected_sku_id=sku_id,
        selected_price=Money(Decimal(selected_price), currency) if selected_price else None,
        available=True,
    )


def proof(*, validated: bool = True) -> AffiliateLinkProof:
    ref = reference()
    return AffiliateLinkProof(
        operation=LINK_GENERATE,
        requested_at=NOW,
        responded_at=NOW + timedelta(seconds=1),
        source_external_product_id=ref.external_product_id,
        canonical_url=ref.canonical_url,
        short_link="https://s.click.aliexpress.com/e/fixture",
        official_endpoint_host="api-sg.aliexpress.com",
        credential_profile_id="default",
        contract_version="attachment-2026-09",
        official_response_validated=validated,
    )


def test_product_detail_parses_both_envelopes_and_string_numbers() -> None:
    streamlined = parse_product_detail(fixture("product_detail_streamlined.json"))
    non_refinement = parse_product_detail(fixture("product_detail_non_refinement.json"))

    assert streamlined[0].product_id == "1005000000000001"
    assert streamlined[0].target_sale_price == Money(Decimal("119.90"), "BRL")
    assert streamlined[0].commission_rate == Decimal("3.5")
    assert non_refinement[0].image_url is None
    assert non_refinement[0].seller is None


def test_product_query_and_empty_result_are_supported() -> None:
    products = parse_product_query(fixture("product_query_streamlined.json"))
    assert products[0].product_id == "1005000000000002"

    payload = fixture("product_query_streamlined.json")
    payload["resp_result"]["result"]["products"] = []
    assert parse_product_query(payload) == ()


def test_missing_nullable_fields_and_incompatible_numeric_values_fail_safely() -> None:
    payload = fixture("product_detail_streamlined.json")
    product = payload["resp_result"]["result"]["products"][0]
    product["target_sale_price"] = None
    product["commission_rate"] = "not-a-number"

    with pytest.raises(ProviderError) as captured:
        parse_product_detail(payload)
    assert captured.value.code == "ALIEXPRESS_COMMISSION_RATE_INVALID"


def test_link_generation_is_mapped_by_source_not_response_order() -> None:
    sources = (
        "https://www.aliexpress.com/item/1005000000000001.html",
        "https://www.aliexpress.com/item/1005000000000002.html",
    )
    links = parse_link_generate(
        fixture("link_generate_streamlined.json"), requested_source_values=sources
    )
    assert tuple(item.source_value for item in links) == sources
    assert all(item.promotion_link.startswith("https://s.click.aliexpress.com/") for item in links)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("extra", "ALIEXPRESS_PROMOTION_LINK_COUNT_MISMATCH"),
        ("unknown", "ALIEXPRESS_PROMOTION_LINK_SOURCE_UNKNOWN"),
        ("non_affiliate", "ALIEXPRESS_PROMOTION_LINK_INVALID"),
    ],
)
def test_link_generation_rejects_unexpected_or_unofficial_results(mutation: str, code: str) -> None:
    source = "https://www.aliexpress.com/item/1005000000000001.html"
    payload = fixture("link_generate_non_refinement.json")
    links = payload["aliexpress_affiliate_link_generate_response"]["resp_result"]["result"][
        "promotion_links"
    ]
    if mutation == "extra":
        links.append(dict(links[0]))
    elif mutation == "unknown":
        links[0]["source_value"] = "https://www.aliexpress.com/item/9999999999999.html"
    else:
        links[0]["promotion_link"] = "https://untrusted.example/fixture"

    with pytest.raises(ProviderError) as captured:
        parse_link_generate(payload, requested_source_values=(source,))
    assert captured.value.code == code


def test_sku_shipping_and_unsupported_br_promotion_contracts() -> None:
    skus = parse_sku_detail(fixture("sku_detail_streamlined.json"))
    shipping = parse_shipping(fixture("shipping_streamlined.json"), currency="BRL")

    assert skus[0].sku_id == "120000000000001"
    assert skus[0].sale_price_with_tax == Money(Decimal("119.90"), "BRL")
    assert shipping.fee == Money(Decimal("12.50"), "BRL")
    with pytest.raises(ProviderError) as captured:
        parse_promotion_info(
            fixture("promotion_streamlined.json"), currency="BRL", ship_to_country="BR"
        )
    assert captured.value.code == "ALIEXPRESS_PROMOTION_COUNTRY_UNSUPPORTED"


def test_missing_sku_is_represented_by_an_empty_official_result() -> None:
    payload = fixture("sku_detail_streamlined.json")
    payload["result"]["result"]["ae_item_sku_info"] = []
    assert parse_sku_detail(payload) == ()


def test_payloads_preserve_documented_values_and_shipping_provenance() -> None:
    assert (
        product_detail_payload(product_ids=("1005000000000001",), tracking_id="configured")[
            "target_currency"
        ]
        == "BRL"
    )
    assert sku_detail_payload(product_id="1005000000000001")["need_deliver_info"] == "No"
    shipping = OfficialShippingInput(
        product_id="1005000000000001",
        sku_id="120000000000001",
        target_sale_price=Decimal("119.90"),
        tax_rate=Decimal("0.1"),
        source_operation="aliexpress.affiliate.product.sku.detail.get",
    )
    assert product_shipping_payload(shipping)["target_sale_price"] == "119.90"
    assert (
        link_generate_payload(source_values=(reference().canonical_url,), tracking_id="configured")[
            "promotion_link_type"
        ]
        == "0"
    )
    with pytest.raises(ProviderError):
        promotion_info_payload(
            product_ids=(reference().external_product_id,),
            currency="BRL",
            target_language="PT",
            ship_to_country="BR",
        )


def test_canonicalization_preserves_only_confirmed_sku_identity() -> None:
    result = canonicalize_store_url(
        "https://de.aliexpress.com/item/1005000000000001.html?spm=old&skuId=120000000000001"
    )
    assert result.external_product_id == "1005000000000001"
    assert result.variation_key == "sku_id:120000000000001"
    assert result.canonical_url == (
        "https://www.aliexpress.com/item/1005000000000001.html?sku_id=120000000000001"
    )


def test_price_policy_never_promotes_one_cheap_sku_as_the_product_price() -> None:
    ranged = select_publishable_price(snapshot(minimum="90", maximum="140", scope=PriceScope.RANGE))
    assert ranged.mode is PriceDisplayMode.RANGE
    assert ranged.maximum.amount == Decimal("140")

    selected = select_publishable_price(
        snapshot(
            minimum="90",
            maximum="140",
            scope=PriceScope.SKU,
            sku_id="120000000000001",
            selected_price="90",
        )
    )
    assert selected.mode is PriceDisplayMode.EXACT
    assert selected.selected_sku_id == "120000000000001"


@pytest.mark.parametrize(("currency", "minimum"), [("USD", "10"), ("BRL", "0")])
def test_non_brl_and_non_positive_prices_cannot_become_ready(currency: str, minimum: str) -> None:
    with pytest.raises(ProviderError):
        select_publishable_price(snapshot(currency=currency, minimum=minimum, maximum=minimum))


def test_unconfirmed_availability_cannot_become_ready() -> None:
    product = snapshot()
    object.__setattr__(product, "available", None)
    with pytest.raises(ProviderError) as captured:
        select_publishable_price(product)
    assert captured.value.code == "ALIEXPRESS_AVAILABILITY_UNCONFIRMED"
    assert captured.value.manual_review


def test_offer_requires_official_link_generation_proof() -> None:
    with pytest.raises(ValueError, match="validated official response"):
        EnrichedAffiliateOffer(product=snapshot(), affiliate_proof=proof(validated=False))


class StaticSigner:
    def sign(self, operation: str, business_parameters: dict[str, str]) -> dict[str, str]:
        return {"method": operation, **business_parameters, "sign": "redacted"}


@pytest.mark.asyncio
async def test_http_transport_retries_429_and_caps_retry_after() -> None:
    attempts = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "999999"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AliExpressHttpTransport(
            client,
            endpoint="https://api-sg.aliexpress.com/rest",
            allowed_endpoint_host="api-sg.aliexpress.com",
            signer=StaticSigner(),
            retry_after_max_seconds=7,
            sleep=sleep,
        )
        assert await transport.execute(LINK_GENERATE, {"tracking_id": "configured"}) == {"ok": True}
    assert attempts == 2
    assert delays == [7]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_http_transport_does_not_retry_permanent_errors(status: int) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AliExpressHttpTransport(
            client,
            endpoint="https://api-sg.aliexpress.com/rest",
            allowed_endpoint_host="api-sg.aliexpress.com",
            signer=StaticSigner(),
        )
        with pytest.raises(ProviderError) as captured:
            await transport.execute(LINK_GENERATE, {})
    assert captured.value.code == "ALIEXPRESS_HTTP_PERMANENT"
    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "server_error"])
async def test_http_transport_limits_retries_for_transient_failures(failure: str) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(503, request=request)

    async def no_sleep(delay: float) -> None:
        assert delay >= 0

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = AliExpressHttpTransport(
            client,
            endpoint="https://api-sg.aliexpress.com/rest",
            allowed_endpoint_host="api-sg.aliexpress.com",
            signer=StaticSigner(),
            max_attempts=2,
            sleep=no_sleep,
        )
        with pytest.raises(ProviderError) as captured:
            await transport.execute(LINK_GENERATE, {})
    assert captured.value.code == "ALIEXPRESS_RETRY_EXHAUSTED"
    assert attempts == 2


@pytest.mark.asyncio
async def test_real_client_stays_behind_explicit_contract_gate() -> None:
    with pytest.raises(ProviderError) as captured:
        await UnavailableAliExpressAffiliateClient().enrich(reference())
    assert captured.value.code == SIGNING_CONTRACT_UNAVAILABLE
