"""Fail-closed SKU-detail contract for isolated discovery refinement."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from promo_bot.providers.aliexpress.contracts import SKU_DETAIL, sku_detail_payload
from promo_bot.providers.base import ProviderError


@dataclass(frozen=True, slots=True)
class DiscoverySkuAttribute:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class DiscoverySku:
    product_id: str
    sku_id: str
    currency: str
    price_with_tax: Decimal | None
    sale_price_with_tax: Decimal
    discount_percent: Decimal | None
    attributes: tuple[DiscoverySkuAttribute, ...]


@dataclass(frozen=True, slots=True)
class DiscoverySkuPage:
    product_id: str
    skus: tuple[DiscoverySku, ...]


class SkuDetailClient(Protocol):
    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]: ...


class AliExpressDiscoverySkuGateway:
    """One untracked SKU-detail query per requested discovery product."""

    def __init__(self, client: SkuDetailClient) -> None:
        self._client = client

    async def query_product_skus(
        self,
        *,
        product_id: str,
        ship_to_country: str,
        target_currency: str,
        target_language: str,
    ) -> DiscoverySkuPage:
        response = await self._client.execute(
            SKU_DETAIL,
            sku_detail_payload(
                product_id=product_id,
                ship_to_country=ship_to_country,
                target_currency=target_currency,
                target_language=target_language,
            ),
        )
        return parse_discovery_sku_detail(response, expected_product_id=product_id)


def parse_discovery_sku_detail(
    payload: Mapping[str, Any], *, expected_product_id: str | None = None
) -> DiscoverySkuPage:
    """Parse only SKU evidence needed by the discovery refinement shadow."""
    wrapped = payload.get("aliexpress_affiliate_product_sku_detail_get_response")
    if wrapped is not None:
        _require_success(payload.get("code"), optional=True)
    body = wrapped if wrapped is not None else payload
    if not isinstance(body, Mapping):
        raise _incompatible()
    _require_success(body.get("code"), optional=True)
    outer = body.get("result")
    if not isinstance(outer, Mapping):
        raise _incompatible()
    _require_success(outer.get("code"))
    result = outer.get("result")
    if not isinstance(result, Mapping):
        raise _incompatible()
    product = result.get("ae_item_info")
    if not isinstance(product, Mapping):
        raise _incompatible()
    product_id = _identifier(product.get("product_id"))
    if product_id is None:
        raise _incompatible()
    if expected_product_id is not None and _identifier(expected_product_id) != product_id:
        raise ProviderError("ALIEXPRESS_DISCOVERY_SKU_PRODUCT_MISMATCH", retryable=False)
    raw_skus = result.get("ae_item_sku_info")
    if not isinstance(raw_skus, list):
        raise _incompatible()
    if not raw_skus:
        raise _item_invalid()
    if len(raw_skus) == 20:
        raise ProviderError(
            "ALIEXPRESS_DISCOVERY_SKU_POSSIBLY_TRUNCATED",
            retryable=False,
            manual_review=True,
        )

    skus: list[DiscoverySku] = []
    seen_sku_ids: set[str] = set()
    for raw in raw_skus:
        if not isinstance(raw, Mapping):
            raise _item_invalid()
        item_product_id = raw.get("product_id")
        if item_product_id is not None and _identifier(item_product_id) != product_id:
            raise ProviderError("ALIEXPRESS_DISCOVERY_SKU_PRODUCT_MISMATCH", retryable=False)
        sku_id = _identifier(raw.get("sku_id"))
        if sku_id is None:
            raise _item_invalid()
        if sku_id in seen_sku_ids:
            raise ProviderError("ALIEXPRESS_DISCOVERY_SKU_DUPLICATE", retryable=False)
        seen_sku_ids.add(sku_id)
        currency = _text(raw.get("currency"))
        if currency != "BRL":
            raise ProviderError("ALIEXPRESS_DISCOVERY_SKU_CURRENCY_INVALID", retryable=False)
        sale_price = _item_decimal(raw.get("sale_price_with_tax"))
        if sale_price is None or sale_price <= 0:
            raise ProviderError("ALIEXPRESS_DISCOVERY_SKU_SALE_PRICE_INVALID", retryable=False)
        skus.append(
            DiscoverySku(
                product_id=product_id,
                sku_id=sku_id,
                currency=currency,
                price_with_tax=_item_decimal(raw.get("price_with_tax")),
                sale_price_with_tax=sale_price,
                discount_percent=_item_percent(raw.get("discount")),
                attributes=_attributes(
                    raw.get("sku_properties"), color=raw.get("color"), size=raw.get("size")
                ),
            )
        )
    return DiscoverySkuPage(product_id=product_id, skus=tuple(skus))


def _attributes(value: object, *, color: object, size: object) -> tuple[DiscoverySkuAttribute, ...]:
    if not isinstance(value, str):
        raise _item_invalid()
    try:
        decoded = json.loads(value, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _item_invalid() from exc
    pairs: list[tuple[object, object]]
    if isinstance(decoded, Mapping):
        pairs = list(decoded.items())
    elif isinstance(decoded, list):
        pairs = []
        for item in decoded:
            if isinstance(item, list) and len(item) == 2:
                pairs.append((item[0], item[1]))
            elif isinstance(item, Mapping) and set(item) == {"name", "value"}:
                pairs.append((item["name"], item["value"]))
            else:
                raise _item_invalid()
    else:
        raise _item_invalid()
    attributes = tuple(
        DiscoverySkuAttribute(name=name, value=attribute_value)
        for name, attribute_value in (_text_pair(pair) for pair in pairs)
    )
    if not attributes or len({attribute.name for attribute in attributes}) != len(attributes):
        raise _item_invalid()
    if any(candidate is not None and _text(candidate) is None for candidate in (color, size)):
        raise _item_invalid()
    top_level = tuple(
        DiscoverySkuAttribute(name=name, value=attribute_value)
        for name, candidate in (("color", color), ("size", size))
        if (attribute_value := _text(candidate)) is not None
    )
    return attributes + top_level


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for name, value in pairs:
        if name in decoded:
            raise ValueError("duplicate JSON object key")
        decoded[name] = value
    return decoded


def _text_pair(pair: tuple[object, object]) -> tuple[str, str]:
    name = _text(pair[0])
    value = _text(pair[1])
    if name is None or value is None:
        raise _item_invalid()
    return name, value


def _identifier(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return text if text and text.isascii() and text.isdecimal() and int(text) > 0 else None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise _incompatible()
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise _incompatible() from exc
    if not parsed.is_finite():
        raise _incompatible()
    return parsed


def _percent(value: object) -> Decimal | None:
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    parsed = _decimal(value)
    if parsed is not None and not Decimal("0") <= parsed <= Decimal("100"):
        raise _incompatible()
    return parsed


def _item_decimal(value: object) -> Decimal | None:
    try:
        return _decimal(value)
    except ProviderError as exc:
        raise _item_invalid() from exc


def _item_percent(value: object) -> Decimal | None:
    try:
        return _percent(value)
    except ProviderError as exc:
        raise _item_invalid() from exc


def _require_success(value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if str(value) != "0":
        raise ProviderError("ALIEXPRESS_API_REJECTED", retryable=False)


def _incompatible() -> ProviderError:
    return ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)


def _item_invalid() -> ProviderError:
    return ProviderError(
        "ALIEXPRESS_DISCOVERY_SKU_ITEM_INVALID", retryable=False, manual_review=True
    )
