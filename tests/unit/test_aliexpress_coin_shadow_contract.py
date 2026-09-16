from __future__ import annotations

import ast
from pathlib import Path

import pytest

from promo_bot.providers.base import ProviderError

SOURCE = "https://s.click.aliexpress.com/e/_Ab12Cd3"
PROMOTION = "https://s.click.aliexpress.com/e/_Zy98Xw7"
TRACKING = "configured-tracking"


def payload(
    *,
    source_value: object = SOURCE,
    promotion_link: object = PROMOTION,
    tracking_id: object = TRACKING,
    links: list[object] | None = None,
    resp_code: object = "200",
) -> dict[str, object]:
    promotion_links = (
        [{"source_value": source_value, "promotion_link": promotion_link}]
        if links is None
        else links
    )
    return {
        "code": "0",
        "aliexpress_affiliate_link_generate_response": {
            "resp_result": {
                "resp_code": resp_code,
                "resp_msg": "success",
                "result": {
                    "promotion_links": promotion_links,
                    "tracking_id": tracking_id,
                },
            }
        },
    }


@pytest.mark.parametrize(
    "value",
    (
        "https://s.click.aliexpress.com/e/_Ab12Cd3",
        "https://s.click.aliexpress.com/e/_Ab12Cd34",
    ),
)
def test_coin_short_contract_accepts_only_proven_token_lengths(value: str) -> None:
    from promo_bot.providers.aliexpress.coin_shadow import validate_coin_short

    assert validate_coin_short(value) == value


@pytest.mark.parametrize(
    "value",
    (
        "https://s.click.aliexpress.com/e/_Ab12Cd",
        "https://s.click.aliexpress.com/e/_Ab12Cd345",
        "https://s.click.aliexpress.com/e/_Ab12-Cd",
        "https://s.click.aliexpress.com/e/_Áb12Cd3",
        "http://s.click.aliexpress.com/e/_Ab12Cd3",
        "https://S.CLICK.ALIEXPRESS.COM/e/_Ab12Cd3",
        "https://user@s.click.aliexpress.com/e/_Ab12Cd3",
        "https://s.click.aliexpress.com:444/e/_Ab12Cd3",
        "https://s.click.aliexpress.com/e/_Ab12Cd3?x=1",
        "https://s.click.aliexpress.com/e/_Ab12Cd3#fragment",
        "https://s.click.aliexpress.com/e/_Ab12Cd3/",
    ),
)
def test_coin_short_contract_rejects_every_unproven_shape(value: str) -> None:
    from promo_bot.providers.aliexpress.coin_shadow import validate_coin_short

    with pytest.raises(ValueError, match="ALIEXPRESS_COIN_SHORT_INVALID"):
        validate_coin_short(value)


def test_coin_shadow_parser_correlates_exact_echo() -> None:
    from promo_bot.providers.aliexpress.coin_shadow import (
        CoinShadowCorrelationMode,
        parse_coin_shadow_link_generate,
    )

    result = parse_coin_shadow_link_generate(
        payload(), sent_source_value=SOURCE, expected_tracking_id=TRACKING
    )

    assert result.source_value == SOURCE
    assert result.promotion_link == PROMOTION
    assert result.tracking_confirmed is True
    assert result.correlation_mode is CoinShadowCorrelationMode.SOURCE_VALUE_EXACT


@pytest.mark.parametrize("source_value", (None, "", "   "))
def test_coin_shadow_parser_allows_only_missing_source_as_positional_singleton(
    source_value: object,
) -> None:
    from promo_bot.providers.aliexpress.coin_shadow import (
        CoinShadowCorrelationMode,
        parse_coin_shadow_link_generate,
    )

    body = payload(source_value=source_value)
    if source_value is None:
        item = body["aliexpress_affiliate_link_generate_response"]["resp_result"]["result"][  # type: ignore[index]
            "promotion_links"
        ][0]
        assert isinstance(item, dict)
        item.pop("source_value")

    result = parse_coin_shadow_link_generate(
        body, sent_source_value=SOURCE, expected_tracking_id=TRACKING
    )

    assert result.source_value == SOURCE
    assert result.correlation_mode is CoinShadowCorrelationMode.POSITIONAL_SINGLETON


@pytest.mark.parametrize(
    ("body", "code"),
    (
        (payload(source_value="https://s.click.aliexpress.com/e/_Other12"), "SOURCE_DIVERGENT"),
        (payload(links=[]), "COUNT_MISMATCH"),
        (
            payload(
                links=[
                    {"source_value": SOURCE, "promotion_link": PROMOTION},
                    {"source_value": SOURCE, "promotion_link": PROMOTION},
                ]
            ),
            "COUNT_MISMATCH",
        ),
        (payload(tracking_id=None), "TRACKING_MISMATCH"),
        (payload(tracking_id="other"), "TRACKING_MISMATCH"),
        (payload(promotion_link=None), "PROMOTION_LINK_INVALID"),
        (payload(promotion_link=""), "PROMOTION_LINK_INVALID"),
        (payload(promotion_link=SOURCE), "PROMOTION_LINK_EQUALS_SOURCE"),
        (
            payload(promotion_link="http://s.click.aliexpress.com/e/_Zy98Xw7"),
            "PROMOTION_LINK_INVALID",
        ),
        (payload(promotion_link="https://example.com/e/_Zy98Xw7"), "PROMOTION_LINK_INVALID"),
        (payload(resp_code="500"), "API_REJECTED"),
    ),
)
def test_coin_shadow_parser_fails_closed(body: dict[str, object], code: str) -> None:
    from promo_bot.providers.aliexpress.coin_shadow import parse_coin_shadow_link_generate

    with pytest.raises(ProviderError, match=f"ALIEXPRESS_COIN_{code}"):
        parse_coin_shadow_link_generate(
            body, sent_source_value=SOURCE, expected_tracking_id=TRACKING
        )


def test_canonical_modules_cannot_import_coin_shadow_parser() -> None:
    root = Path(__file__).parents[2] / "src" / "promo_bot"
    canonical_modules = (
        root / "providers" / "aliexpress" / "parsing.py",
        root / "affiliate" / "aliexpress_conversion.py",
    )
    forbidden = "promo_bot.providers.aliexpress.coin_shadow"

    for module in canonical_modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert forbidden not in imports
