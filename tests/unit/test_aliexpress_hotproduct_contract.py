from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from promo_bot.providers.aliexpress import contracts


class RecordingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, operation: str, payload: dict[str, str]) -> dict[str, object]:
        self.calls.append((operation, payload))
        return self.response


def _response(products: list[object]) -> dict[str, object]:
    return {
        "aliexpress_affiliate_hotproduct_query_response": {
            "resp_result": {
                "resp_code": "200",
                "result": {"products": products, "current_record_count": str(len(products))},
            }
        }
    }


def test_hotproduct_payload_uses_documented_br_fields_without_urls_or_fields() -> None:
    assert contracts.hotproduct_query_payload(
        tracking_id="configured-tracking",
        keywords="ssd",
        category_ids=("7", "21"),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=5,
    ) == {
        "tracking_id": "configured-tracking",
        "keywords": "ssd",
        "category_ids": "7,21",
        "ship_to_country": "BR",
        "target_currency": "BRL",
        "target_language": "PT",
        "page_no": "1",
        "page_size": "5",
        "platform_product_type": "ALL",
    }


@pytest.mark.asyncio
async def test_hotproduct_gateway_sends_only_hot_operation_once() -> None:
    hot = importlib.import_module("promo_bot.providers.aliexpress.discovery_hot")
    client = RecordingClient(_response([]))
    page = await hot.AliExpressHotProductGateway(client, tracking_id="own-tracking").query_page(
        keyword="ssd",
        category_ids=(),
        ship_to_country="BR",
        target_currency="BRL",
        target_language="PT",
        page_no=1,
        page_size=5,
    )
    assert page.products == ()
    assert len(client.calls) == 1
    assert client.calls[0][0] == contracts.HOTPRODUCT_QUERY
    assert client.calls[0][1]["tracking_id"] == "own-tracking"


def test_hotproduct_parser_normalizes_product_level_price_and_discards_links_and_sku() -> None:
    hot = importlib.import_module("promo_bot.providers.aliexpress.discovery_hot")
    page = hot.parse_discovery_hotproduct_query(
        _response(
            [
                {
                    "product_id": "123",
                    "product_title": "SSD",
                    "product_main_image_url": "https://example.invalid/image",
                    "first_level_category_id": "7",
                    "first_level_category_name": "Hardware",
                    "second_level_category_id": "21",
                    "second_level_category_name": "SSD",
                    "shop_id": "42",
                    "shop_name": "Loja",
                    "target_sale_price": "89.90",
                    "target_sale_price_currency": "BRL",
                    "original_price": "100",
                    "original_price_currency": "USD",
                    "discount": "10%",
                    "commission_rate": "3",
                    "hot_product_commission_rate": "4",
                    "lastest_volume": "52",
                    "promotion_link": "https://example.invalid/affiliate",
                    "product_detail_url": "https://example.invalid/product",
                    "shop_url": "https://example.invalid/shop",
                    "sku_id": "999",
                }
            ]
        )
    )
    assert len(page.products) == 1
    product = page.products[0]
    assert product.product_id == "123"
    assert product.target_brl_price == Decimal("89.90")
    assert product.declared_discount_percent == Decimal("10")
    assert product.hot_product_commission_rate == Decimal("4")
    assert not hasattr(product, "sku_id")
    assert not hasattr(product, "promotion_link")


def test_hotproduct_parser_accepts_empty_and_rejects_bad_identity_but_keeps_optional_errors() -> (
    None
):
    hot = importlib.import_module("promo_bot.providers.aliexpress.discovery_hot")
    assert hot.parse_discovery_hotproduct_query(_response([])).products == ()
    page = hot.parse_discovery_hotproduct_query(
        _response(
            [
                {
                    "product_id": "١٢٣",
                    "target_sale_price": "12",
                    "target_sale_price_currency": "BRL",
                },
                {
                    "product_id": "123",
                    "target_sale_price": "bad",
                    "target_sale_price_currency": "BRL",
                },
            ]
        )
    )
    assert page.rejected_product_count == 1
    assert len(page.products) == 1
    assert page.products[0].target_brl_price is None
    assert "TARGET_SALE_PRICE_INVALID" in page.products[0].diagnostics
