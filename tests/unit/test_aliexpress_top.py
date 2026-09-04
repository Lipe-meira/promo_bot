from __future__ import annotations

import logging
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
RESERVED_PARAMETER_NAMES = {
    "access_token",
    "app_key",
    "debug",
    "format",
    "method",
    "partner_id",
    "session",
    "sign",
    "sign_method",
    "simplify",
    "timestamp",
    "v",
}


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


@pytest.mark.parametrize("reserved", sorted(RESERVED_PARAMETER_NAMES))
def test_reserved_business_parameter_collisions_are_rejected(reserved: str) -> None:
    sensitive_value = "never-echo-reserved-value"

    with pytest.raises(ValueError, match="reserved TOP parameter") as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            LINK_GENERATE,
            {reserved: sensitive_value},
            timestamp_ms=TIMESTAMP_MS,
        )

    assert sensitive_value not in str(captured.value)


def test_unsupported_dotted_operation_is_rejected_without_echoing_it() -> None:
    operation = "aliexpress.affiliate.order.list"

    with pytest.raises(ValueError) as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            operation, {}, timestamp_ms=TIMESTAMP_MS
        )

    assert operation not in str(captured.value)


def test_unicode_is_signed_and_form_encoded_as_utf8() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_QUERY,
        {"keywords": "café promoção", "tracking_id": "fixture"},
        timestamp_ms=TIMESTAMP_MS,
    )

    assert dict(request.query_pairs)["sign"] == (
        "B19E2FE531D7FACC5A1BEC2D0495DB6861469A6475D13DE5BA5B21CA16DD4157"
    )
    assert "keywords=caf%C3%A9+promo%C3%A7%C3%A3o" in request.encoded_form()


def test_fixed_timestamp_is_serialized_as_unix_milliseconds() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE, {}, timestamp_ms=TIMESTAMP_MS
    )

    assert ("timestamp", "1788498000123") in request.query_pairs


def test_signature_is_present_once_as_uppercase_hex() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE, {}, timestamp_ms=TIMESTAMP_MS
    )
    signatures = [value for name, value in request.query_pairs if name == "sign"]

    assert len(signatures) == 1
    assert len(signatures[0]) == 64
    assert signatures[0] == signatures[0].upper()
    assert set(signatures[0]) <= set("0123456789ABCDEF")
    assert "sign" not in {name for name, _ in request.form_pairs}


def test_blank_parameters_are_omitted_without_trimming_nonblank_values() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE,
        {"": "ignored", "   ": "ignored", "blank": "   ", "kept": " padded "},
        timestamp_ms=TIMESTAMP_MS,
        session="   ",
    )

    assert request.form_pairs == (("kept", " padded "),)
    assert "session" not in {name for name, _ in request.query_pairs}


@pytest.mark.parametrize("timestamp", [True, False, "1788498000123", 1.5])
def test_invalid_timestamps_fail_without_echoing_values(timestamp: object) -> None:
    with pytest.raises(TypeError, match="integer number of milliseconds") as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            LINK_GENERATE,
            {},
            timestamp_ms=timestamp,  # type: ignore[arg-type]
        )

    assert repr(timestamp) not in str(captured.value)


def test_repr_str_and_logs_redact_sensitive_values(caplog: pytest.LogCaptureFixture) -> None:
    app_key = "never-show-app-key"
    secret = "never-show-secret"
    tracking = "never-show-tracking"
    session = "never-show-session-token"
    body_value = "never-show-body-value"
    builder = AliExpressTopRequestBuilder(app_key, secret)
    request = builder.prepare(
        LINK_GENERATE,
        {"tracking_id": tracking, "source_values": body_value},
        timestamp_ms=TIMESTAMP_MS,
        session=session,
    )
    signature = dict(request.query_pairs)["sign"]

    logging.getLogger("test.aliexpress.top").warning("%r %r", builder, request)
    visible = " ".join((repr(builder), str(builder), repr(request), str(request), caplog.text))

    for sensitive in (app_key, secret, tracking, session, body_value, signature):
        assert sensitive not in visible


def test_prepared_repr_contains_no_literal_parameter_values() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE,
        {"tracking_id": "hidden-tracking", "source_values": "hidden-source"},
        timestamp_ms=TIMESTAMP_MS,
        session="hidden-token",
    )

    assert "query_pairs=<redacted:" in repr(request)
    assert "form_pairs=<redacted:" in repr(request)
    for _, value in (*request.query_pairs, *request.form_pairs):
        assert value not in repr(request)


def test_arbitrary_invalid_value_is_not_echoed_in_error() -> None:
    sensitive = "never-echo-invalid-body-value"

    with pytest.raises(TypeError, match="strings or null") as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            LINK_GENERATE,
            {"tracking_id": (sensitive, object())},  # type: ignore[dict-item]
            timestamp_ms=TIMESTAMP_MS,
        )

    assert sensitive not in str(captured.value)


def test_non_string_parameter_name_is_rejected_without_echoing_it() -> None:
    sensitive_name = ("never-echo-invalid-name",)

    with pytest.raises(TypeError, match="names must be strings") as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            LINK_GENERATE,
            {sensitive_name: "value"},  # type: ignore[dict-item]
            timestamp_ms=TIMESTAMP_MS,
        )

    assert sensitive_name[0] not in str(captured.value)
