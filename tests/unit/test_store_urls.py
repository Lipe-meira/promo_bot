import pytest

from promo_bot.domain.enums import RelayLinkState, Store
from promo_bot.stores.urls import canonicalize_store_url, is_allowed_network_url, is_shortener_url


@pytest.mark.parametrize(
    ("url", "store", "external_id", "canonical"),
    [
        (
            "https://www.amazon.com.br/gp/product/B0ABCDEFGH?tag=old-20&utm_source=x",
            Store.AMAZON,
            "B0ABCDEFGH",
            "https://www.amazon.com.br/dp/B0ABCDEFGH",
        ),
        (
            "https://produto.mercadolivre.com.br/MLB-123456-produto?matt_tool=old",
            Store.MERCADOLIVRE,
            "MLB123456",
            "https://produto.mercadolivre.com.br/MLB-123456",
        ),
        (
            "https://shopee.com.br/nome-i.100.200?utm_campaign=x",
            Store.SHOPEE,
            "100:200",
            "https://shopee.com.br/product/100/200",
        ),
        (
            "https://pt.aliexpress.com/item/1005001234567890.html?aff_fcid=old",
            Store.ALIEXPRESS,
            "1005001234567890",
            "https://www.aliexpress.com/item/1005001234567890.html",
        ),
        (
            "https://www.kabum.com.br/produto/123456/nome?awc=old",
            Store.KABUM,
            "123456",
            "https://www.kabum.com.br/produto/123456",
        ),
    ],
)
def test_canonicalizes_supported_store_urls(
    url: str, store: Store, external_id: str, canonical: str
) -> None:
    result = canonicalize_store_url(url)

    assert result.state is RelayLinkState.PENDING_AFFILIATE
    assert result.store is store
    assert result.external_product_id == external_id
    assert result.canonical_url == canonical
    assert "utm_" not in canonical
    assert "aff_" not in canonical


def test_explicit_numeric_variation_is_preserved() -> None:
    result = canonicalize_store_url(
        "https://shopee.com.br/product/100/200?variationId=300&utm_source=old"
    )

    assert result.state is RelayLinkState.PENDING_AFFILIATE
    assert result.variation_key == "variationid:300"
    assert result.canonical_url == "https://shopee.com.br/product/100/200?variationid=300"


def test_shopee_identity_includes_shop_and_item_ids() -> None:
    first = canonicalize_store_url("https://shopee.com.br/product/100/200")
    second = canonicalize_store_url("https://shopee.com.br/product/999/200")

    assert first.external_product_id == "100:200"
    assert second.external_product_id == "999:200"
    assert first.external_product_id != second.external_product_id


def test_ambiguous_variation_requires_manual_review() -> None:
    result = canonicalize_store_url("https://www.amazon.com.br/dp/B0ABCDEFGH?color=azul")

    assert result.state is RelayLinkState.MANUAL_REVIEW
    assert result.reason_code == "VARIATION_AMBIGUOUS"
    assert result.external_product_id is None


def test_duplicate_explicit_variation_values_require_manual_review() -> None:
    result = canonicalize_store_url(
        "https://shopee.com.br/product/100/200?variationId=300&variationId=400"
    )

    assert result.state is RelayLinkState.MANUAL_REVIEW
    assert result.reason_code == "VARIATION_AMBIGUOUS"


def test_unknown_query_semantics_are_never_deduplicated_as_the_base_product() -> None:
    result = canonicalize_store_url(
        "https://www.amazon.com.br/dp/B0ABCDEFGH?possibleVariant=unknown"
    )

    assert result.state is RelayLinkState.MANUAL_REVIEW
    assert result.reason_code == "QUERY_SEMANTICS_UNKNOWN"
    assert result.external_product_id is None


def test_product_id_must_be_explicit() -> None:
    result = canonicalize_store_url("https://www.amazon.com.br/s?k=ssd")

    assert result.state is RelayLinkState.MANUAL_REVIEW
    assert result.reason_code == "PRODUCT_ID_NOT_FOUND"


def test_only_exact_domains_are_allowed() -> None:
    assert is_allowed_network_url("https://amzn.to/example")
    assert not is_allowed_network_url("https://amazon.com.br.attacker.example/dp/B0ABCDEFGH")


def test_mercado_livre_sec_path_is_expanded_as_a_short_link() -> None:
    assert is_shortener_url("https://mercadolivre.com.br/sec/abc123")
