"""Tolerant product-query contract for isolated discovery only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from promo_bot.providers.aliexpress.contracts import PRODUCT_QUERY, product_query_payload
from promo_bot.providers.base import ProviderError


@dataclass(frozen=True, slots=True)
class ObservedPrice:
    field: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class DiscoveryProduct:
    product_id: str
    title: str | None
    image_url: str | None
    first_category_id: str | None
    first_category_name: str | None
    second_category_id: str | None
    second_category_name: str | None
    shop_id: str | None
    shop_name: str | None
    target_brl_price: Decimal | None
    observed_prices: tuple[ObservedPrice, ...]
    declared_discount_percent: Decimal | None
    commission_rate: Decimal | None
    hot_product_commission_rate: Decimal | None
    volume: int | None
    completeness_score: int
    diagnostics: frozenset[str]


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    products: tuple[DiscoveryProduct, ...]
    current_record_count: int | None
    total_record_count: int | None
    rejected_product_count: int = 0


class ProductQueryClient(Protocol):
    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]: ...


class AliExpressProductQueryGateway:
    def __init__(self, client: ProductQueryClient, *, tracking_id: str) -> None:
        if not tracking_id:
            raise ValueError("ALIEXPRESS_TRACKING_ID_MISSING")
        self._client = client
        self._tracking_id = tracking_id

    async def query_page(
        self,
        *,
        keyword: str,
        category_ids: tuple[str, ...],
        ship_to_country: str,
        target_currency: str,
        target_language: str,
        page_no: int,
        page_size: int,
    ) -> DiscoveryPage:
        payload = product_query_payload(
            tracking_id=self._tracking_id,
            target_currency=target_currency,
            target_language=target_language,
            ship_to_country=ship_to_country,
            category_ids=category_ids,
            keywords=keyword,
            page_no=page_no,
            page_size=page_size,
            platform_product_type="ALL",
        )
        response = await self._client.execute(PRODUCT_QUERY, payload)
        return parse_discovery_product_query(response)


def parse_discovery_product_query(payload: Mapping[str, Any]) -> DiscoveryPage:
    body = payload.get("aliexpress_affiliate_product_query_response", payload)
    if not isinstance(body, Mapping):
        raise _incompatible()
    response = body.get("resp_result")
    if not isinstance(response, Mapping) or str(response.get("resp_code")) != "200":
        raise ProviderError("ALIEXPRESS_API_REJECTED", retryable=False)
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise _incompatible()
    raw_products = result.get("products")
    if not isinstance(raw_products, list):
        raise _incompatible()

    products: list[DiscoveryProduct] = []
    rejected = 0
    for raw in raw_products:
        if not isinstance(raw, Mapping):
            rejected += 1
            continue
        product_id = _identifier(raw.get("product_id"))
        if product_id is None:
            rejected += 1
            continue
        products.append(_parse_product(product_id, raw))

    return DiscoveryPage(
        products=tuple(products),
        current_record_count=_optional_nonnegative_int(result.get("current_record_count")),
        total_record_count=_optional_nonnegative_int(result.get("total_record_count")),
        rejected_product_count=rejected,
    )


def _parse_product(product_id: str, item: Mapping[str, Any]) -> DiscoveryProduct:
    diagnostics: set[str] = set()
    target_amount = _optional_decimal(
        item.get("target_sale_price"), "TARGET_SALE_PRICE_INVALID", diagnostics
    )
    target_currency = _optional_text(item.get("target_sale_price_currency"))
    target_brl_price = (
        target_amount
        if target_amount is not None and target_amount > 0 and target_currency == "BRL"
        else None
    )
    if item.get("target_sale_price") not in {None, ""} and target_amount is None:
        diagnostics.add("TARGET_SALE_PRICE_INVALID")

    observed: list[ObservedPrice] = []
    for field in ("original_price", "sale_price", "app_sale_price", "target_app_sale_price"):
        amount = _optional_decimal(item.get(field), f"{field.upper()}_INVALID", diagnostics)
        currency = _optional_text(item.get(f"{field}_currency"))
        if amount is not None and amount > 0 and currency is not None:
            observed.append(ObservedPrice(field, amount, currency))
    if (
        target_amount is not None
        and target_amount > 0
        and target_currency is not None
        and target_currency != "BRL"
    ):
        observed.append(ObservedPrice("target_sale_price", target_amount, target_currency))

    discount = _optional_percent(item.get("discount"), "DISCOUNT_INVALID", diagnostics)
    commission = _optional_percent(
        item.get("commission_rate"), "COMMISSION_RATE_INVALID", diagnostics
    )
    hot_commission = _optional_percent(
        item.get("hot_product_commission_rate"),
        "HOT_PRODUCT_COMMISSION_RATE_INVALID",
        diagnostics,
    )
    volume = _optional_nonnegative_int(item.get("lastest_volume"))
    if item.get("lastest_volume") not in {None, ""} and volume is None:
        diagnostics.add("VOLUME_INVALID")

    title = _optional_text(item.get("product_title"))
    image_url = _optional_text(item.get("product_main_image_url"))
    first_id = _optional_identifier(item.get("first_level_category_id"), diagnostics)
    first_name = _optional_text(item.get("first_level_category_name"))
    second_id = _optional_identifier(item.get("second_level_category_id"), diagnostics)
    second_name = _optional_text(item.get("second_level_category_name"))
    shop_id = _optional_identifier(item.get("shop_id"), diagnostics)
    shop_name = _optional_text(item.get("shop_name"))
    completeness = sum(
        (
            title is not None,
            image_url is not None,
            first_id is not None or first_name is not None,
            second_id is not None or second_name is not None,
            target_brl_price is not None,
            discount is not None,
            commission is not None,
            volume is not None,
            shop_id is not None or shop_name is not None,
            bool(observed),
        )
    )
    return DiscoveryProduct(
        product_id=product_id,
        title=title,
        image_url=image_url,
        first_category_id=first_id,
        first_category_name=first_name,
        second_category_id=second_id,
        second_category_name=second_name,
        shop_id=shop_id,
        shop_name=shop_name,
        target_brl_price=target_brl_price,
        observed_prices=tuple(observed),
        declared_discount_percent=discount,
        commission_rate=commission,
        hot_product_commission_rate=hot_commission,
        volume=volume,
        completeness_score=completeness,
        diagnostics=frozenset(diagnostics),
    )


def _identifier(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text if text and text.isascii() and text.isdecimal() and int(text) > 0 else None


def _optional_identifier(value: object, diagnostics: set[str]) -> str | None:
    if value in {None, ""}:
        return None
    parsed = _identifier(value)
    if parsed is None:
        diagnostics.add("OPTIONAL_IDENTIFIER_INVALID")
    return parsed


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_decimal(value: object, code: str, diagnostics: set[str]) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        diagnostics.add(code)
        return None
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        diagnostics.add(code)
        return None
    if not parsed.is_finite():
        diagnostics.add(code)
        return None
    return parsed


def _optional_percent(value: object, code: str, diagnostics: set[str]) -> Decimal | None:
    if isinstance(value, str):
        value = value.strip().removesuffix("%")
    parsed = _optional_decimal(value, code, diagnostics)
    if parsed is not None and not Decimal("0") <= parsed <= Decimal("100"):
        diagnostics.add(code)
        return None
    return parsed


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text or not text.isascii() or not text.isdecimal():
        return None
    return int(text)


def _incompatible() -> ProviderError:
    return ProviderError("ALIEXPRESS_RESPONSE_INCOMPATIBLE", retryable=False, manual_review=True)
