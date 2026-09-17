from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from promo_bot.discovery.config import DiscoveryConfigError, load_discovery_profiles
from promo_bot.providers.aliexpress.contracts import PRODUCT_QUERY
from promo_bot.providers.aliexpress.discovery import (
    AliExpressProductQueryGateway,
    parse_discovery_product_query,
)


class RecordingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, operation: str, payload: dict[str, str]) -> dict[str, object]:
        self.calls.append((operation, payload))
        return self.response


def write_profiles(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_profile_contract_accepts_only_bounded_brazilian_product_queries(tmp_path: Path) -> None:
    profiles = load_discovery_profiles(
        write_profiles(
            tmp_path / "profiles.yaml",
            """
version: 1
profiles:
  hardware-gamer-br:
    keywords: [ssd, teclado mecanico]
    category_ids: ['7', '21']
    ship_to_country: BR
    target_currency: BRL
    target_language: PT
    page_size: 5
    max_pages: 2
    max_results: 25
    max_api_calls: 4
    minimum_price_drop_percent: 5
""",
        )
    )

    profile = profiles.get("hardware-gamer-br")
    assert profile.keywords == ("ssd", "teclado mecanico")
    assert profile.category_ids == ("7", "21")
    assert profile.ship_to_country == "BR"
    assert profile.target_currency == "BRL"
    assert profile.target_language == "PT"
    assert profile.page_size == 5
    assert profile.max_pages == 2
    assert profile.max_results == 25
    assert profile.max_api_calls == 4
    assert profile.minimum_price_drop_percent == Decimal("5")


@pytest.mark.parametrize(
    "override",
    [
        "target_currency: USD",
        "target_language: EN",
        "ship_to_country: US",
        "category_ids: ['١٢٣']",
        "page_size: 51",
        "max_pages: 6",
        "max_results: 251",
        "max_api_calls: 21",
        "unknown_option: true",
    ],
)
def test_profile_contract_fails_closed_for_unsupported_values(
    tmp_path: Path, override: str
) -> None:
    base = {
        "target_currency": "target_currency: BRL",
        "target_language": "target_language: PT",
        "ship_to_country": "ship_to_country: BR",
        "category_ids": "category_ids: []",
        "page_size": "page_size: 5",
        "max_pages": "max_pages: 2",
        "max_results": "max_results: 25",
        "max_api_calls": "max_api_calls: 4",
    }
    key = override.split(":", 1)[0]
    if key in base:
        base[key] = override
        extra = ""
    else:
        extra = f"    {override}\n"
    body = (
        "version: 1\nprofiles:\n  hardware-gamer-br:\n"
        "    keywords: [ssd]\n"
        + "".join(f"    {value}\n" for value in base.values())
        + "    minimum_price_drop_percent: 5\n"
        + extra
    )

    with pytest.raises(DiscoveryConfigError):
        load_discovery_profiles(write_profiles(tmp_path / "invalid.yaml", body))


@pytest.mark.asyncio
async def test_gateway_uses_the_proven_payload_without_fields() -> None:
    response = {
        "resp_result": {
            "resp_code": 200,
            "result": {"products": [], "current_record_count": 0},
        }
    }
    client = RecordingClient(response)
    gateway = AliExpressProductQueryGateway(client, tracking_id="configured-tracking")

    page = await gateway.query_page(
        keyword="ssd",
        category_ids=("7", "21"),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=5,
    )

    assert page.products == ()
    assert client.calls == [
        (
            PRODUCT_QUERY,
            {
                "target_currency": "BRL",
                "target_language": "PT",
                "tracking_id": "configured-tracking",
                "ship_to_country": "BR",
                "category_ids": "7,21",
                "keywords": "ssd",
                "page_no": "1",
                "page_size": "5",
                "platform_product_type": "ALL",
            },
        )
    ]


def test_parser_normalizes_real_types_and_never_exposes_affiliate_urls() -> None:
    payload = {
        "resp_result": {
            "resp_code": 200,
            "result": {
                "current_record_count": 2,
                "total_record_count": 137796,
                "total_page_no": 999,
                "products": [
                    {
                        "product_id": 1005009843319638,
                        "product_title": "SSD de teste",
                        "product_main_image_url": "https://example.invalid/image.jpg",
                        "first_level_category_id": 7,
                        "first_level_category_name": "Hardware",
                        "second_level_category_id": "21",
                        "second_level_category_name": "SSD",
                        "shop_id": 42,
                        "shop_name": "Loja",
                        "target_sale_price": "79.90",
                        "target_sale_price_currency": "BRL",
                        "sale_price": "16.00",
                        "sale_price_currency": "USD",
                        "original_price": "100.00",
                        "original_price_currency": "CNY",
                        "discount": "20%",
                        "commission_rate": "4.25%",
                        "hot_product_commission_rate": "6%",
                        "lastest_volume": 0,
                        "promotion_link": "https://s.click.aliexpress.com/e/secret",
                        "product_detail_url": "https://www.aliexpress.com/item/secret.html",
                        "shop_url": "https://shop.example.invalid/secret",
                    },
                    {
                        "product_id": "1005000000000002",
                        "target_sale_price": "invalid",
                        "target_sale_price_currency": "BRL",
                        "discount": {"unexpected": True},
                        "commission_rate": "invalid",
                        "lastest_volume": "invalid",
                    },
                ],
            },
        }
    }

    page = parse_discovery_product_query(payload)

    assert page.current_record_count == 2
    assert page.total_record_count == 137796
    assert len(page.products) == 2
    first, second = page.products
    assert first.product_id == "1005009843319638"
    assert first.target_brl_price == Decimal("79.90")
    assert [(p.field, p.amount, p.currency) for p in first.observed_prices] == [
        ("original_price", Decimal("100.00"), "CNY"),
        ("sale_price", Decimal("16.00"), "USD"),
    ]
    assert first.declared_discount_percent == Decimal("20")
    assert first.commission_rate == Decimal("4.25")
    assert first.volume == 0
    assert not hasattr(first, "promotion_link")
    assert not hasattr(first, "detail_url")
    assert not hasattr(first, "shop_url")
    assert second.product_id == "1005000000000002"
    assert second.target_brl_price is None
    assert {
        "TARGET_SALE_PRICE_INVALID",
        "DISCOUNT_INVALID",
        "COMMISSION_RATE_INVALID",
        "VOLUME_INVALID",
    }.issubset(second.diagnostics)


@pytest.mark.parametrize(
    "invalid_id",
    [True, 1.5, "1e3", "+123", "١٢٣", "12x"],
)
def test_parser_rejects_ambiguous_product_identity_without_failing_other_items(
    invalid_id: object,
) -> None:
    payload = {
        "resp_result": {
            "resp_code": "200",
            "result": {
                "products": [
                    {"product_id": invalid_id},
                    {"product_id": 1005000000000001},
                ]
            },
        }
    }

    page = parse_discovery_product_query(payload)

    assert [product.product_id for product in page.products] == ["1005000000000001"]
    assert page.rejected_product_count == 1
