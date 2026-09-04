from __future__ import annotations

from dataclasses import FrozenInstanceError
from urllib.parse import parse_qsl, urlsplit

import pytest

from promo_bot.providers.aliexpress.contracts import (
    LINK_GENERATE,
    PRODUCT_DETAIL,
    PRODUCT_QUERY,
    PRODUCT_SHIPPING,
    PROMOTION_INFO,
    SKU_DETAIL,
)
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder

APP_KEY = "fixture-app-key"
APP_SECRET = "fixture-secret"
TIMESTAMP_MS = 1_788_498_000_123


def test_prepared_request_preserves_java_method_compatibility_quirk() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE,
        {
            "tracking_id": "fixture-tracking",
            "source_values": "https://example.test/item/1",
        },
        timestamp_ms=TIMESTAMP_MS,
    )

    assert request.method == "POST"
    assert request.path == "/sync"
    assert [pair for pair in request.query_pairs if pair[0] == "method"] == [
        ("method", LINK_GENERATE),
        ("method", LINK_GENERATE),
    ]
    parsed_query = parse_qsl(urlsplit(request.relative_url()).query)
    assert parsed_query.count(("method", LINK_GENERATE)) == 2
    assert sum(name == "method" for name, _ in parsed_query) == 2


def test_signature_uses_one_method_and_excludes_sign_during_calculation() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE,
        {"tracking_id": "fixture-tracking", "source_values": "item"},
        timestamp_ms=TIMESTAMP_MS,
    )

    signatures = [value for key, value in request.query_pairs if key == "sign"]
    assert signatures == ["0F88C79BFC1E97740FFF89710D8F02BFF4F6D34253D63C42B55C5F4D14F9A5FF"]


def test_signature_sorting_is_independent_of_business_input_order() -> None:
    first = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_QUERY,
        {"zeta": "ultimo", "alpha": "primeiro"},
        timestamp_ms=TIMESTAMP_MS,
    )
    second = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_QUERY,
        {"alpha": "primeiro", "zeta": "ultimo"},
        timestamp_ms=TIMESTAMP_MS,
    )

    assert dict(first.query_pairs)["sign"] == dict(second.query_pairs)["sign"]


def test_common_query_and_business_form_are_separate_and_deterministic() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_DETAIL,
        {"tracking_id": "fixture", "country": "BR", "empty": "", "null": None},
        timestamp_ms=TIMESTAMP_MS,
    )

    assert request.query_pairs[0] == ("method", PRODUCT_DETAIL)
    assert request.query_pairs[1:] == tuple(sorted(request.query_pairs[1:]))
    assert request.form_pairs == (("country", "BR"), ("tracking_id", "fixture"))
    assert "tracking_id" not in {name for name, _ in request.query_pairs}
    assert "app_key" not in {name for name, _ in request.form_pairs}
    assert request.content_type == "application/x-www-form-urlencoded;charset=UTF-8"
    assert request.encoded_form() == "country=BR&tracking_id=fixture"


def test_prepared_request_is_immutable() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE, {}, timestamp_ms=TIMESTAMP_MS
    )

    with pytest.raises(FrozenInstanceError):
        request.path = "/other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "operation",
    [
        PRODUCT_DETAIL,
        PRODUCT_QUERY,
        LINK_GENERATE,
        SKU_DETAIL,
        PRODUCT_SHIPPING,
        PROMOTION_INFO,
    ],
)
def test_six_authorized_affiliate_operations_are_supported(operation: str) -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        operation, {}, timestamp_ms=TIMESTAMP_MS
    )

    assert request.query_pairs.count(("method", operation)) == 2
