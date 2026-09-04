"""Fail-closed parsers for documented AliExpress response envelopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from promo_bot.domain.enums import Store
from promo_bot.domain.models import Money
from promo_bot.providers.aliexpress.models import (
    AliExpressProduct,
    AliExpressPromotion,
    AliExpressShipping,
    AliExpressSku,
    PromotionLinkMapping,
)
from promo_bot.providers.base import ProviderError
from promo_bot.stores.urls import canonicalize_store_url

PROMOTION_LINK_HOSTS = frozenset({"s.click.aliexpress.com"})


def parse_product_detail(payload: Mapping[str, Any]) -> tuple[AliExpressProduct, ...]:
    result = _resp_result(
        payload,
        wrapper="aliexpress_affiliate_productdetail_get_response",
    )
    return _parse_products(result)


def parse_product_query(payload: Mapping[str, Any]) -> tuple[AliExpressProduct, ...]:
    result = _resp_result(payload, wrapper="aliexpress_affiliate_product_query_response")
    return _parse_products(result)


def parse_link_generate(
    payload: Mapping[str, Any], *, requested_source_values: Sequence[str]
) -> tuple[PromotionLinkMapping, ...]:
    requested = tuple(requested_source_values)
    requested_product_ids = tuple(_aliexpress_product_id(source) for source in requested)
    if (
        not requested
        or any(product_id is None for product_id in requested_product_ids)
        or len(set(requested_product_ids)) != len(requested_product_ids)
    ):
        raise ValueError("requested source values must be non-empty and unique")
    result = _resp_result(payload, wrapper="aliexpress_affiliate_link_generate_response")
    raw_links = _list(result.get("promotion_links"), "promotion_links")
    if len(raw_links) != len(requested):
        raise ProviderError("ALIEXPRESS_PROMOTION_LINK_COUNT_MISMATCH", retryable=False)

    by_product_id: dict[str, PromotionLinkMapping] = {}
    for raw in raw_links:
        item = _mapping(raw, "promotion_link")
        source = _required_text(item.get("source_value"), "source_value")
        link = _required_text(item.get("promotion_link"), "promotion_link")
        product_id = _aliexpress_product_id(source)
        if product_id is not None and product_id in by_product_id:
            raise ProviderError("ALIEXPRESS_PROMOTION_LINK_SOURCE_DUPLICATED", retryable=False)
        if product_id is None or product_id not in requested_product_ids:
            raise ProviderError("ALIEXPRESS_PROMOTION_LINK_SOURCE_UNKNOWN", retryable=False)
        _validate_promotion_link(link)
        by_product_id[product_id] = PromotionLinkMapping(
            source_value=source,
            promotion_link=link,
        )

    if set(by_product_id) != set(requested_product_ids):
        raise ProviderError("ALIEXPRESS_PROMOTION_LINK_SOURCE_MISSING", retryable=False)
    return tuple(by_product_id[product_id] for product_id in requested_product_ids if product_id)


def _aliexpress_product_id(value: str) -> str | None:
    canonical = canonicalize_store_url(value)
    if canonical.store is not Store.ALIEXPRESS:
        return None
    return canonical.external_product_id


def parse_sku_detail(payload: Mapping[str, Any]) -> tuple[AliExpressSku, ...]:
    body = _body(payload, "aliexpress_affiliate_product_sku_detail_get_response")
    outer = _mapping(body.get("result"), "result")
    _require_success_code(outer.get("code"), field="result.code", expected="0")
    inner = _mapping(outer.get("result"), "result.result")
    product = _mapping(inner.get("ae_item_info"), "ae_item_info")
    product_id = _identifier(product.get("product_id"), "product_id")
    raw_skus = _list(inner.get("ae_item_sku_info"), "ae_item_sku_info")
    parsed: list[AliExpressSku] = []
    for raw in raw_skus:
        item = _mapping(raw, "sku")
        currency = _optional_text(item.get("currency"))
        parsed.append(
            AliExpressSku(
                product_id=product_id,
                sku_id=_identifier(item.get("sku_id"), "sku_id"),
                price_with_tax=_money(item.get("price_with_tax"), currency, "price_with_tax"),
                sale_price_with_tax=_money(
                    item.get("sale_price_with_tax"), currency, "sale_price_with_tax"
                ),
                shipping_fee=_money(item.get("shipping_fees"), currency, "shipping_fees"),
                tax_rate=_decimal(item.get("tax_rate"), "tax_rate"),
                color=_optional_text(item.get("color")),
                size=_optional_text(item.get("size")),
                properties=_optional_text(item.get("sku_properties")),
                image_url=_optional_text(item.get("sku_image_link")),
                ship_from_country=_optional_text(item.get("ship_from_country")),
                delivery_days=_optional_text(item.get("delivery_days")),
                min_delivery_days=_optional_text(item.get("min_delivery_days")),
                max_delivery_days=_optional_text(item.get("max_delivery_days")),
            )
        )
    return tuple(parsed)


def parse_shipping(payload: Mapping[str, Any], *, currency: str) -> AliExpressShipping:
    result = _resp_result(
        payload,
        wrapper="aliexpress_affiliate_product_shipping_get_response",
    )
    return AliExpressShipping(
        fee=_money(result.get("shipping_fee"), currency, "shipping_fee"),
        delivery_days=_optional_text(result.get("delivery_days")),
        min_delivery_days=_optional_text(result.get("min_delivery_days")),
        max_delivery_days=_optional_text(result.get("max_delivery_days")),
        ship_from_country=_optional_text(result.get("ship_from_country")),
    )


def parse_promotion_info(
    payload: Mapping[str, Any], *, currency: str, ship_to_country: str
) -> tuple[AliExpressPromotion, ...]:
    if ship_to_country.strip().upper() == "BR":
        raise ProviderError("ALIEXPRESS_PROMOTION_COUNTRY_UNSUPPORTED", retryable=False)
    body = _body(payload, "aliexpress_affiliate_promotion_info_get_response")
    _require_success_code(body.get("code"), field="code", expected="0")
    result = _mapping(body.get("result"), "result")
    parsed: list[AliExpressPromotion] = []
    for raw_product in _list(result.get("promotion_results"), "promotion_results"):
        product = _mapping(raw_product, "promotion_result")
        product_id = _identifier(product.get("product_id"), "product_id")
        for raw_promotion in _list(product.get("promotions"), "promotions"):
            promotion = _mapping(raw_promotion, "promotion")
            parsed.append(
                AliExpressPromotion(
                    product_id=product_id,
                    redemption_channel=_optional_text(promotion.get("redemption_channel")),
                    minimum_purchase=_money_with_unit(
                        promotion.get("minimum_purchase_amount"),
                        currency,
                        "minimum_purchase_amount",
                    ),
                    money_off=_money_with_unit(
                        promotion.get("money_off_amount"), currency, "money_off_amount"
                    ),
                    generic_code=_optional_text(promotion.get("generic_redemption_code")),
                    product_applicability=_optional_text(promotion.get("product_applicability")),
                    value_type=_optional_text(promotion.get("coupon_value_type")),
                    offer_type=_optional_text(promotion.get("offer_type")),
                    title=_optional_text(promotion.get("long_title")),
                    effective_dates=_optional_text(promotion.get("promotion_effective_dates")),
                )
            )
    return tuple(parsed)


def _parse_products(result: Mapping[str, Any]) -> tuple[AliExpressProduct, ...]:
    products = _list(result.get("products"), "products")
    parsed: list[AliExpressProduct] = []
    for raw in products:
        item = _mapping(raw, "product")
        parsed.append(
            AliExpressProduct(
                product_id=_identifier(item.get("product_id"), "product_id"),
                title=_optional_text(item.get("product_title")),
                detail_url=_optional_text(item.get("product_detail_url")),
                original_price=_money(
                    item.get("original_price"),
                    _optional_text(item.get("original_price_currency")),
                    "original_price",
                ),
                sale_price=_money(
                    item.get("sale_price"),
                    _optional_text(item.get("sale_price_currency")),
                    "sale_price",
                ),
                target_sale_price=_money(
                    item.get("target_sale_price"),
                    _optional_text(item.get("target_sale_price_currency")),
                    "target_sale_price",
                ),
                app_sale_price=_money(
                    item.get("app_sale_price"),
                    _optional_text(item.get("app_sale_price_currency")),
                    "app_sale_price",
                ),
                target_app_sale_price=_money(
                    item.get("target_app_sale_price"),
                    _optional_text(item.get("target_app_sale_price_currency")),
                    "target_app_sale_price",
                ),
                image_url=_optional_text(item.get("product_main_image_url")),
                seller=_optional_text(item.get("shop_name")),
                shop_id=_optional_identifier(item.get("shop_id"), "shop_id"),
                sku_id=_optional_identifier(item.get("sku_id"), "sku_id"),
                commission_rate=_percent(item.get("commission_rate"), "commission_rate"),
                hot_product_commission_rate=_percent(
                    item.get("hot_product_commission_rate"),
                    "hot_product_commission_rate",
                ),
                relevant_market_commission_rate=_percent(
                    item.get("relevant_market_commission_rate"),
                    "relevant_market_commission_rate",
                ),
                tax_rate=_decimal(item.get("tax_rate"), "tax_rate"),
                queried_at=datetime.now(UTC),
            )
        )
    return tuple(parsed)


def _resp_result(payload: Mapping[str, Any], *, wrapper: str) -> Mapping[str, Any]:
    body = _body(payload, wrapper)
    _require_success_code(payload.get("code"), field="code", expected="0", optional=True)
    response = _mapping(body.get("resp_result"), "resp_result")
    _require_success_code(response.get("resp_code"), field="resp_code", expected="200")
    return _mapping(response.get("result"), "resp_result.result")


def _body(payload: Mapping[str, Any], wrapper: str) -> Mapping[str, Any]:
    wrapped = payload.get(wrapper)
    if wrapped is None:
        return payload
    return _mapping(wrapped, wrapper)


def _require_success_code(
    value: object, *, field: str, expected: str, optional: bool = False
) -> None:
    if value is None and optional:
        return
    if str(value) != expected:
        raise ProviderError("ALIEXPRESS_API_REJECTED", retryable=False)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
    return value


def _list(value: object, field: str) -> list[object]:
    del field
    if not isinstance(value, list):
        raise ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
    return value


def _required_text(value: object, field: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_MISSING", retryable=False)
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
    stripped = value.strip()
    return stripped or None


def _identifier(value: object, field: str) -> str:
    parsed = _optional_identifier(value, field)
    if parsed is None:
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_MISSING", retryable=False)
    return parsed


def _optional_identifier(value: object, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
    parsed = str(value).strip()
    if not parsed.isdigit():
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_INVALID", retryable=False)
    return parsed


def _decimal(value: object, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_INVALID", retryable=False) from exc
    if not parsed.is_finite():
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_INVALID", retryable=False)
    return parsed


def _percent(value: object, field: str) -> Decimal | None:
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    parsed = _decimal(value, field)
    if parsed is not None and not Decimal("0") <= parsed <= Decimal("100"):
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_INVALID", retryable=False)
    return parsed


def _money(value: object, currency: str | None, field: str) -> Money | None:
    amount = _decimal(value, field)
    if amount is None:
        return None
    if currency is None:
        raise ProviderError(f"ALIEXPRESS_{field.upper()}_CURRENCY_MISSING", retryable=False)
    return Money(amount, currency)


def _money_with_unit(value: object, currency: str, field: str) -> Money | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
    number = value.strip().split(maxsplit=1)[0]
    return _money(number, currency, field)


def _validate_promotion_link(url: str) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ProviderError("ALIEXPRESS_PROMOTION_LINK_INVALID", retryable=False) from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.hostname.casefold() not in PROMOTION_LINK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise ProviderError("ALIEXPRESS_PROMOTION_LINK_INVALID", retryable=False)
