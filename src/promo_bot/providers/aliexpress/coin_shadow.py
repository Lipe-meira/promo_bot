"""Strict contract for the isolated AliExpress coin-short experiment."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

from promo_bot.providers.base import ProviderError

_COIN_SHORT = re.compile(
    r"https://s\.click\.aliexpress\.com/e/_[A-Za-z0-9]{7,8}\Z",
    flags=re.ASCII,
)
_PROMOTION_LINK_HOSTS = frozenset({"s.click.aliexpress.com"})


class CoinShadowCorrelationMode(StrEnum):
    SOURCE_VALUE_EXACT = "SOURCE_VALUE_EXACT"
    POSITIONAL_SINGLETON = "POSITIONAL_SINGLETON"


@dataclass(frozen=True, slots=True, repr=False)
class CoinShadowLinkResult:
    source_value: str
    promotion_link: str
    tracking_confirmed: bool
    correlation_mode: CoinShadowCorrelationMode


def validate_coin_short(value: str) -> str:
    if not isinstance(value, str) or _COIN_SHORT.fullmatch(value) is None:
        raise ValueError("ALIEXPRESS_COIN_SHORT_INVALID")
    return value


def parse_coin_shadow_link_generate(
    payload: Mapping[str, Any],
    *,
    sent_source_value: str,
    expected_tracking_id: str,
) -> CoinShadowLinkResult:
    """Validate one response without relaxing the canonical product parser."""

    source = validate_coin_short(sent_source_value)
    if not expected_tracking_id:
        raise ValueError("ALIEXPRESS_COIN_TRACKING_REQUIRED")

    body_value = payload.get("aliexpress_affiliate_link_generate_response", payload)
    body = _mapping(body_value)
    if str(payload.get("code", "0")) != "0":
        _fail("API_REJECTED")
    response = _mapping(body.get("resp_result"))
    if str(response.get("resp_code")) != "200":
        _fail("API_REJECTED")
    result = _mapping(response.get("result"))
    raw_links = result.get("promotion_links")
    if not isinstance(raw_links, list) or len(raw_links) != 1:
        _fail("COUNT_MISMATCH")

    item = _mapping(raw_links[0])
    returned_source = item.get("source_value")
    returned_link = item.get("promotion_link")
    returned_tracking = result.get("tracking_id")

    correlation_mode: CoinShadowCorrelationMode
    if returned_source is None or (
        isinstance(returned_source, str) and not returned_source.strip()
    ):
        correlation_mode = CoinShadowCorrelationMode.POSITIONAL_SINGLETON
    elif not isinstance(returned_source, str) or returned_source != source:
        _fail("SOURCE_DIVERGENT")
    else:
        correlation_mode = CoinShadowCorrelationMode.SOURCE_VALUE_EXACT

    if not isinstance(returned_tracking, str) or returned_tracking != expected_tracking_id:
        _fail("TRACKING_MISMATCH")
    if not isinstance(returned_link, str) or not returned_link.strip():
        _fail("PROMOTION_LINK_INVALID")
    promotion_link = returned_link.strip()
    _validate_promotion_link(promotion_link)
    if promotion_link == source:
        _fail("PROMOTION_LINK_EQUALS_SOURCE")

    return CoinShadowLinkResult(
        source_value=source,
        promotion_link=promotion_link,
        tracking_confirmed=True,
        correlation_mode=correlation_mode,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("RESPONSE_INCOMPATIBLE")
    return cast(Mapping[str, Any], value)


def _validate_promotion_link(value: str) -> None:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        _fail("PROMOTION_LINK_INVALID")
    if (
        parts.scheme != "https"
        or parts.hostname not in _PROMOTION_LINK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or not parts.path
    ):
        _fail("PROMOTION_LINK_INVALID")


def _fail(suffix: str) -> NoReturn:
    raise ProviderError(f"ALIEXPRESS_COIN_{suffix}", retryable=False, manual_review=True)
