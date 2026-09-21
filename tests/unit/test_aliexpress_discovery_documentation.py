from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_discovery_shadow_documentation_records_safety_contract() -> None:
    text = (ROOT / "docs" / "ALIEXPRESS_DISCOVERY_SHADOW.md").read_text(encoding="utf-8")

    for required in (
        "ALIEXPRESS_DISCOVERY_SHADOW_ENABLED=false",
        "aliexpress.affiliate.product.query",
        "max_attempts=1",
        "CONCURRENT_QUERY_IN_PROGRESS",
        "LIVE > CACHE",
        "60 minutos",
        "30 dias",
        "baseline",
        "Advanced API",
        "não comprova comissão",
        "não publica",
        "discovery-scan",
        "discovery-results",
        "canonical_product_url",
        "https://pt.aliexpress.com/item/<product_id>.html",
        "somente com `--include-products`",
        "ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED=false",
        "ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED=false",
        "aliexpress.affiliate.product.sku.detail.get",
        "discovery-sku-refine",
        "discovery-sku-results",
        "PRODUCT_MINIMUM_UNVERIFIED_BY_SKU",
        "POSSIBLE_SKU_TRUNCATION",
        "SKU_HISTORY_BACKED_PRICE_DROP",
        "não comprova estoque",
    ):
        assert required in text


def test_discovery_profile_example_is_strict_and_not_hardcoded_in_application_config() -> None:
    example = ROOT / "docs" / "examples" / "aliexpress-discovery-profiles.example.yaml"
    payload = yaml.safe_load(example.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["profiles"]["hardware-gamer-br"]["ship_to_country"] == "BR"
    assert payload["profiles"]["hardware-gamer-br"]["target_currency"] == "BRL"
    assert payload["profiles"]["hardware-gamer-br"]["target_language"] == "PT"
