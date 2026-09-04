"""Offline AliExpress TOP signing and request preparation."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import quote_plus

from promo_bot.providers.aliexpress.contracts import (
    LINK_GENERATE,
    PRODUCT_DETAIL,
    PRODUCT_QUERY,
    PRODUCT_SHIPPING,
    PROMOTION_INFO,
    SKU_DETAIL,
)

type ParameterPair = tuple[str, str]
AUTHORIZED_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        PRODUCT_DETAIL,
        PRODUCT_QUERY,
        LINK_GENERATE,
        SKU_DETAIL,
        PRODUCT_SHIPPING,
        PROMOTION_INFO,
    }
)
CONTENT_TYPE: Final[str] = "application/x-www-form-urlencoded;charset=UTF-8"
PARTNER_ID: Final[str] = "iop-sdk-java-20181207"
RESERVED_PARAMETER_NAMES: Final[frozenset[str]] = frozenset(
    {
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
)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedAliExpressTopRequest:
    """Immutable wire representation without a gateway or transport."""

    method: Literal["POST"]
    path: Literal["/sync"]
    query_pairs: tuple[ParameterPair, ...]
    form_pairs: tuple[ParameterPair, ...]
    content_type: str

    def relative_url(self) -> str:
        return f"{self.path}?{_encode_pairs(self.query_pairs)}"

    def encoded_form(self) -> str:
        return _encode_pairs(self.form_pairs)

    def __repr__(self) -> str:
        return (
            "PreparedAliExpressTopRequest(method='POST', path='/sync', "
            f"query_pairs=<redacted:{len(self.query_pairs)}>, "
            f"form_pairs=<redacted:{len(self.form_pairs)}>)"
        )

    __str__ = __repr__


class AliExpressTopRequestBuilder:
    """Build a signed relative request without performing I/O."""

    __slots__ = ("_app_key", "_app_secret")

    def __init__(self, app_key: str, app_secret: str) -> None:
        self._app_key = _required_credential(app_key)
        self._app_secret = _required_credential(app_secret)

    def prepare(
        self,
        operation: str,
        business_parameters: Mapping[str, str | None],
        *,
        timestamp_ms: int | None = None,
        session: str | None = None,
        debug: bool = False,
    ) -> PreparedAliExpressTopRequest:
        if operation not in AUTHORIZED_OPERATIONS:
            raise ValueError("unsupported AliExpress Affiliate operation")

        _reject_reserved_collisions(business_parameters)
        business = _normalized_parameters(business_parameters)
        common = {
            "app_key": self._app_key,
            "format": "json",
            "method": operation,
            "partner_id": PARTNER_ID,
            "sign_method": "sha256",
            "simplify": "true",
            "timestamp": _timestamp_text(timestamp_ms),
            "v": "2.0",
        }
        normalized_session = _optional_session(session)
        if normalized_session is not None:
            common["session"] = normalized_session
        if debug:
            common["debug"] = "true"

        signed_parameters = {**common, **business}
        common["sign"] = _sign(signed_parameters, self._app_secret)

        return PreparedAliExpressTopRequest(
            method="POST",
            path="/sync",
            query_pairs=(("method", operation), *sorted(common.items())),
            form_pairs=tuple(sorted(business.items())),
            content_type=CONTENT_TYPE,
        )

    def __repr__(self) -> str:
        return "AliExpressTopRequestBuilder(app_key=<redacted>, app_secret=<redacted>)"

    __str__ = __repr__


def _required_credential(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("AliExpress credential must be a non-empty string")
    return value


def _reject_reserved_collisions(parameters: Mapping[str, str | None]) -> None:
    for name in parameters:
        if not isinstance(name, str):
            raise TypeError("business parameter names must be strings")
        if name in RESERVED_PARAMETER_NAMES:
            raise ValueError("business parameters contain a reserved TOP parameter")


def _normalized_parameters(parameters: Mapping[str, str | None]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, value in parameters.items():
        if not isinstance(name, str):
            raise TypeError("business parameter names must be strings")
        if value is not None and not isinstance(value, str):
            raise TypeError("business parameter values must be strings or null")
        if name.strip() and value is not None and value.strip():
            normalized[name] = value
    return normalized


def _optional_session(value: str | None) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError("session must be a string or null")
    if value is None or not value.strip():
        return None
    return value


def _timestamp_text(timestamp_ms: int | None) -> str:
    value = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("timestamp_ms must be an integer number of milliseconds")
    return str(value)


def _sign(parameters: Mapping[str, str], app_secret: str) -> str:
    canonical = "".join(
        name + parameters[name]
        for name in sorted(parameters)
        if name != "sign" and name.strip() and parameters[name].strip()
    )
    return (
        hmac.new(
            app_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        )
        .hexdigest()
        .upper()
    )


def _encode_pairs(pairs: tuple[ParameterPair, ...]) -> str:
    return "&".join(
        f"{name}={quote_plus(value, encoding='utf-8', errors='strict')}" for name, value in pairs
    )
