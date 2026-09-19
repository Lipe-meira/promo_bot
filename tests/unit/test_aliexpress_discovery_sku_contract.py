from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from promo_bot.discovery.config import DiscoveryConfigError, load_discovery_profiles
from promo_bot.providers.aliexpress.contracts import SKU_DETAIL
from promo_bot.providers.aliexpress.discovery import parse_discovery_product_query
from promo_bot.providers.aliexpress.discovery_sku import (
    AliExpressDiscoverySkuGateway,
    parse_discovery_sku_detail,
)
from promo_bot.providers.base import ProviderError


class RecordingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def execute(self, operation: str, payload: dict[str, str]) -> dict[str, object]:
        self.calls.append((operation, payload))
        return self.response


@pytest.mark.asyncio
async def test_query_product_skus_uses_untracked_documented_payload_and_preserves_attributes() -> (
    None
):
    client = RecordingClient(
        {
            "code": "0",
            "result": {
                "code": "0",
                "result": {
                    "ae_item_info": {"product_id": "1005000000000001"},
                    "ae_item_sku_info": [
                        {
                            "sku_id": "120000000000001",
                            "currency": "BRL",
                            "price_with_tax": "129.90",
                            "sale_price_with_tax": "119.90",
                            "discount": "8%",
                            "sku_properties": (
                                '[{"name": "Color", "value": "Preto"}, '
                                '{"name": "Size", "value": "M"}]'
                            ),
                            "product_detail_url": "https://untrusted.example/product",
                            "shipping_fees": "12.50",
                            "delivery_days": "12",
                            "stock": 7,
                            "title": "ignored",
                        }
                    ],
                },
            },
        }
    )

    page = await AliExpressDiscoverySkuGateway(client).query_product_skus(
        product_id="1005000000000001"
    )

    assert page.product_id == "1005000000000001"
    assert page.skus[0].sku_id == "120000000000001"
    assert page.skus[0].price_with_tax == Decimal("129.90")
    assert page.skus[0].sale_price_with_tax == Decimal("119.90")
    assert page.skus[0].currency == "BRL"
    assert page.skus[0].discount_percent == Decimal("8")
    assert [(attribute.name, attribute.value) for attribute in page.skus[0].attributes] == [
        ("Color", "Preto"),
        ("Size", "M"),
    ]
    assert not hasattr(page.skus[0], "title")
    assert not hasattr(page.skus[0], "delivery_days")
    assert not hasattr(page.skus[0], "stock")
    assert client.calls == [
        (
            SKU_DETAIL,
            {
                "ship_to_country": "BR",
                "product_id": "1005000000000001",
                "target_currency": "BRL",
                "target_language": "PT",
                "need_deliver_info": "No",
            },
        )
    ]


def test_parser_marks_twenty_skus_as_possibly_truncated() -> None:
    payload = {
        "result": {
            "code": "0",
            "result": {
                "ae_item_info": {"product_id": "1005000000000001"},
                "ae_item_sku_info": [
                    {
                        "sku_id": str(120000000000001 + index),
                        "currency": "BRL",
                        "sale_price_with_tax": "119.90",
                        "sku_properties": '{"Color": "Preto"}',
                    }
                    for index in range(20)
                ],
            },
        }
    }

    with pytest.raises(ProviderError) as captured:
        parse_discovery_sku_detail(payload)

    assert captured.value.code == "ALIEXPRESS_DISCOVERY_SKU_POSSIBLY_TRUNCATED"
    assert captured.value.manual_review is True


def test_optional_sku_refinement_profile_normalizes_requirements_without_unit_conversion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """
version: 1
profiles:
  hardware-gamer-br:
    keywords: [ssd]
    ship_to_country: BR
    target_currency: BRL
    target_language: PT
    page_size: 5
    max_pages: 2
    max_results: 25
    max_api_calls: 4
    minimum_price_drop_percent: 5
    sku_refinement:
      max_refined_products: 4
      max_sku_api_calls: 3
      requirements:
        - dimension: " storage-capacity "
          property_names: [" Color ", ROM]
          accepted_values: [" Preto ", "1 TB"]
""",
        encoding="utf-8",
    )

    refinement = load_discovery_profiles(path).get("hardware-gamer-br").sku_refinement

    assert refinement is not None
    assert refinement.max_refined_products == 4
    assert refinement.max_sku_api_calls == 3
    assert refinement.requirements[0].dimension == "storage-capacity"
    assert refinement.requirements[0].property_names == ("color", "rom")
    assert refinement.requirements[0].accepted_values == ("preto", "1 tb")


def test_parser_accepts_the_non_refinement_envelope_and_json_object_attributes() -> None:
    page = parse_discovery_sku_detail(
        {
            "code": "0",
            "aliexpress_affiliate_product_sku_detail_get_response": {
                "code": "0",
                "result": {
                    "code": "0",
                    "result": {
                        "ae_item_info": {"product_id": "1005000000000001"},
                        "ae_item_sku_info": [
                            {
                                "sku_id": "120000000000001",
                                "currency": "BRL",
                                "price_with_tax": "129.90",
                                "sale_price_with_tax": "119.90",
                                "sku_properties": '{"Color": "Preto", "Size": "M"}',
                            }
                        ],
                    },
                },
            },
        },
        expected_product_id="1005000000000001",
    )

    assert [(item.name, item.value) for item in page.skus[0].attributes] == [
        ("Color", "Preto"),
        ("Size", "M"),
    ]


def test_parser_merges_top_level_color_and_size_with_distinct_json_properties() -> None:
    page = parse_discovery_sku_detail(
        {
            "result": {
                "code": "0",
                "result": {
                    "ae_item_info": {"product_id": "1005000000000001"},
                    "ae_item_sku_info": [
                        {
                            "sku_id": "120000000000001",
                            "currency": "BRL",
                            "sale_price_with_tax": "119.90",
                            "sku_properties": '{"Material": "Algodao"}',
                            "color": "Preto",
                            "size": "M",
                        }
                    ],
                },
            }
        }
    )

    assert [(attribute.name, attribute.value) for attribute in page.skus[0].attributes] == [
        ("Material", "Algodao"),
        ("color", "Preto"),
        ("size", "M"),
    ]


def test_parser_rejects_non_refinement_envelope_with_nonzero_root_status() -> None:
    payload = {
        "code": "13",
        "aliexpress_affiliate_product_sku_detail_get_response": {
            "code": "0",
            "result": {
                "code": "0",
                "result": {
                    "ae_item_info": {"product_id": "1005000000000001"},
                    "ae_item_sku_info": [
                        {
                            "sku_id": "120000000000001",
                            "currency": "BRL",
                            "sale_price_with_tax": "119.90",
                            "sku_properties": '{"Color": "Preto"}',
                        }
                    ],
                },
            },
        },
    }

    with pytest.raises(ProviderError) as captured:
        parse_discovery_sku_detail(payload)

    assert captured.value.code == "ALIEXPRESS_API_REJECTED"


@pytest.mark.parametrize(
    "invalid_id", [" 120000000000001", "120000000000001 ", chr(0xFF11), "+12", 0, True]
)
def test_parser_rejects_nonliteral_sku_ids(invalid_id: object) -> None:
    payload = {
        "result": {
            "code": "0",
            "result": {
                "ae_item_info": {"product_id": "1005000000000001"},
                "ae_item_sku_info": [
                    {
                        "sku_id": invalid_id,
                        "currency": "BRL",
                        "sale_price_with_tax": "119.90",
                        "sku_properties": '{"ROM": "1 TB"}',
                    }
                ],
            },
        }
    }
    with pytest.raises(ProviderError):
        parse_discovery_sku_detail(payload)


def test_parser_rejects_nontextual_top_level_property() -> None:
    payload = {
        "result": {
            "code": "0",
            "result": {
                "ae_item_info": {"product_id": "1005000000000001"},
                "ae_item_sku_info": [
                    {
                        "sku_id": "120000000000001",
                        "currency": "BRL",
                        "sale_price_with_tax": "119.90",
                        "sku_properties": '{"ROM": "1 TB"}',
                        "color": ["Preto"],
                    }
                ],
            },
        }
    }
    with pytest.raises(ProviderError):
        parse_discovery_sku_detail(payload)


@pytest.mark.parametrize("value", ["", "a" * 101, "x" + chr(10) + "y", "x" + chr(127) + "y"])
def test_sku_requirement_rejects_unusable_dimension(value: str) -> None:
    from pydantic import ValidationError

    from promo_bot.discovery.config import SkuRefinementRequirement

    with pytest.raises(ValidationError):
        SkuRefinementRequirement.model_validate(
            {
                "dimension": value,
                "property_names": ["ROM"],
                "accepted_values": ["1 TB"],
            }
        )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("empty", "ALIEXPRESS_RESPONSE_INCOMPATIBLE"),
        ("malformed_attributes", "ALIEXPRESS_RESPONSE_INCOMPATIBLE"),
        ("duplicate_sku", "ALIEXPRESS_DISCOVERY_SKU_DUPLICATE"),
        ("mismatched_product", "ALIEXPRESS_DISCOVERY_SKU_PRODUCT_MISMATCH"),
        ("non_brl", "ALIEXPRESS_DISCOVERY_SKU_CURRENCY_INVALID"),
        ("zero_sale_price", "ALIEXPRESS_DISCOVERY_SKU_SALE_PRICE_INVALID"),
    ],
)
def test_parser_rejects_unusable_or_uncorrelated_sku_evidence(mutation: str, code: str) -> None:
    sku = {
        "sku_id": "120000000000001",
        "currency": "BRL",
        "price_with_tax": "129.90",
        "sale_price_with_tax": "119.90",
        "sku_properties": '{"Color": "Preto"}',
    }
    skus: list[dict[str, object]] = [sku]
    if mutation == "empty":
        skus = []
    elif mutation == "malformed_attributes":
        sku["sku_properties"] = "not-json"
    elif mutation == "duplicate_sku":
        skus.append({**sku, "sale_price_with_tax": "109.90"})
    elif mutation == "mismatched_product":
        sku["product_id"] = "1005000000000002"
    elif mutation == "non_brl":
        sku["currency"] = "USD"
    elif mutation == "zero_sale_price":
        sku["sale_price_with_tax"] = "0"
    payload = {
        "result": {
            "code": "0",
            "result": {
                "ae_item_info": {"product_id": "1005000000000001"},
                "ae_item_sku_info": skus,
            },
        }
    }

    with pytest.raises(ProviderError) as captured:
        parse_discovery_sku_detail(payload, expected_product_id="1005000000000001")

    assert captured.value.code == code


@pytest.mark.parametrize(
    "refinement",
    [
        "max_refined_products: 0\n      max_sku_api_calls: 1",
        "max_refined_products: 21\n      max_sku_api_calls: 1",
        "max_refined_products: 1\n      max_sku_api_calls: 2",
        "max_refined_products: 1\n      max_sku_api_calls: 1\n      requirements: []",
    ],
)
def test_sku_refinement_profile_fails_closed_for_invalid_budgets_or_requirements(
    tmp_path: Path, refinement: str
) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(
        f"""
version: 1
profiles:
  hardware-gamer-br:
    keywords: [ssd]
    ship_to_country: BR
    target_currency: BRL
    target_language: PT
    page_size: 5
    max_pages: 2
    max_results: 25
    max_api_calls: 4
    minimum_price_drop_percent: 5
    sku_refinement:
      {refinement}
      requirements:
        - dimension: storage-capacity
          property_names: [Color]
          accepted_values: [Preto]
""",
        encoding="utf-8",
    )

    with pytest.raises(DiscoveryConfigError):
        load_discovery_profiles(path)


def test_product_query_still_discards_sku_id() -> None:
    page = parse_discovery_product_query(
        {
            "resp_result": {
                "resp_code": "200",
                "result": {
                    "products": [
                        {
                            "product_id": "1005000000000001",
                            "sku_id": "120000000000001",
                        }
                    ]
                },
            }
        }
    )

    assert page.products[0].product_id == "1005000000000001"
    assert not hasattr(page.products[0], "sku_id")
