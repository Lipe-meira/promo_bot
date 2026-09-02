"""Documented business payloads for the offline AliExpress contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from promo_bot.providers.base import ProviderError

PRODUCT_DETAIL = "aliexpress.affiliate.productdetail.get"
PRODUCT_QUERY = "aliexpress.affiliate.product.query"
LINK_GENERATE = "aliexpress.affiliate.link.generate"
SKU_DETAIL = "aliexpress.affiliate.product.sku.detail.get"
PRODUCT_SHIPPING = "aliexpress.affiliate.product.shipping.get"
PROMOTION_INFO = "aliexpress.affiliate.promotion.info.get"

SUPPORTED_CURRENCIES = frozenset(
    {
        "USD",
        "GBP",
        "CAD",
        "EUR",
        "UAH",
        "MXN",
        "TRY",
        "RUB",
        "BRL",
        "AUD",
        "INR",
        "JPY",
        "IDR",
        "SEK",
        "KRW",
        "ILS",
        "THB",
        "CLP",
        "VND",
    }
)
SUPPORTED_LANGUAGES = frozenset(
    {
        "EN",
        "RU",
        "PT",
        "ES",
        "FR",
        "ID",
        "IT",
        "TH",
        "JA",
        "AR",
        "VI",
        "TR",
        "DE",
        "HE",
        "KO",
        "NL",
        "PL",
        "MX",
        "CL",
        "IN",
    }
)
PROMOTION_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "CH",
        "CZ",
        "HU",
        "SE",
        "PT",
        "DE",
        "FR",
        "NL",
        "ES",
        "IT",
        "PL",
        "UK",
        "JP",
        "US",
        "CL",
    }
)
PROMOTION_LANGUAGES = frozenset(
    {"EN", "FR", "TH", "HE", "JA", "PT", "RU", "KO", "AR", "UK", "NL", "ES", "DE", "IT", "TR", "PL"}
)


def product_detail_payload(
    *,
    product_ids: tuple[str, ...],
    tracking_id: str,
    target_currency: str = "BRL",
    target_language: str = "PT",
    country: str = "BR",
    fields: tuple[str, ...] = (),
) -> dict[str, str]:
    payload = {
        "product_ids": _csv_ids(product_ids, "product_ids"),
        "target_currency": _currency(target_currency),
        "target_language": _language(target_language),
        "tracking_id": _required(tracking_id, "tracking_id"),
        "country": _country(country),
    }
    if fields:
        payload["fields"] = _csv_text(fields, "fields")
    return payload


def product_query_payload(
    *,
    tracking_id: str,
    target_currency: str = "BRL",
    target_language: str = "PT",
    ship_to_country: str = "BR",
    category_ids: tuple[str, ...] = (),
    fields: tuple[str, ...] = (),
    keywords: str | None = None,
    page_no: int | None = None,
    page_size: int | None = None,
    platform_product_type: str | None = None,
    sort: str | None = None,
    promotion_name: str | None = None,
    delivery_days: str | None = None,
) -> dict[str, str]:
    payload = {
        "target_currency": _currency(target_currency),
        "target_language": _language(target_language),
        "tracking_id": _required(tracking_id, "tracking_id"),
        "ship_to_country": _country(ship_to_country),
    }
    optional = {
        "category_ids": _csv_ids(category_ids, "category_ids") if category_ids else None,
        "fields": _csv_text(fields, "fields") if fields else None,
        "keywords": _optional(keywords),
        "page_no": _bounded_int(page_no, "page_no", minimum=1) if page_no else None,
        "page_size": _bounded_int(page_size, "page_size", minimum=1, maximum=50)
        if page_size
        else None,
        "platform_product_type": _choice(
            platform_product_type, "platform_product_type", {"ALL", "PLAZA", "TMALL"}
        ),
        "sort": _choice(
            sort,
            "sort",
            {"SALE_PRICE_ASC", "SALE_PRICE_DESC", "LAST_VOLUME_ASC", "LAST_VOLUME_DESC"},
        ),
        "promotion_name": _optional(promotion_name),
        "delivery_days": _optional(delivery_days),
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


def link_generate_payload(
    *,
    source_values: tuple[str, ...],
    tracking_id: str,
    promotion_link_type: int = 0,
    ship_to_country: str = "BR",
) -> dict[str, str]:
    if not 1 <= len(source_values) <= 50:
        raise ValueError("source_values must contain between 1 and 50 links")
    if any(not value.strip() or "," in value for value in source_values):
        raise ValueError("source_values contains an invalid link value")
    if promotion_link_type not in {0, 2}:
        raise ValueError("promotion_link_type must be 0 or 2")
    return {
        "ship_to_country": _country(ship_to_country),
        "promotion_link_type": str(promotion_link_type),
        "source_values": ",".join(source_values),
        "tracking_id": _required(tracking_id, "tracking_id"),
    }


def sku_detail_payload(
    *,
    product_id: str,
    target_currency: str = "BRL",
    target_language: str = "PT",
    ship_to_country: str = "BR",
    need_deliver_info: bool = False,
    sku_ids: tuple[str, ...] = (),
) -> dict[str, str]:
    if len(sku_ids) > 20:
        raise ValueError("sku_ids accepts at most 20 values")
    payload = {
        "ship_to_country": _country(ship_to_country),
        "product_id": _identifier(product_id, "product_id"),
        "target_currency": _currency(target_currency),
        "target_language": _language(target_language),
        "need_deliver_info": "Yes" if need_deliver_info else "No",
    }
    if sku_ids:
        payload["sku_ids"] = _csv_ids(sku_ids, "sku_ids")
    return payload


@dataclass(frozen=True, slots=True)
class OfficialShippingInput:
    product_id: str
    sku_id: str
    target_sale_price: Decimal
    tax_rate: Decimal
    source_operation: str

    def __post_init__(self) -> None:
        _identifier(self.product_id, "product_id")
        _identifier(self.sku_id, "sku_id")
        if self.target_sale_price < 0 or self.tax_rate < 0:
            raise ValueError("official shipping inputs cannot be negative")
        if self.source_operation not in {PRODUCT_DETAIL, PRODUCT_QUERY, SKU_DETAIL}:
            raise ValueError("shipping inputs must originate in an official product operation")


def product_shipping_payload(
    source: OfficialShippingInput,
    *,
    target_currency: str = "BRL",
    target_language: str = "PT",
    ship_to_country: str = "BR",
) -> dict[str, str]:
    return {
        "product_id": source.product_id,
        "sku_id": source.sku_id,
        "ship_to_country": _country(ship_to_country),
        "target_currency": _currency(target_currency),
        "target_sale_price": _decimal_text(source.target_sale_price),
        "target_language": _language(target_language),
        "tax_rate": _decimal_text(source.tax_rate),
    }


def promotion_info_payload(
    *,
    product_ids: tuple[str, ...],
    currency: str,
    target_language: str,
    ship_to_country: str,
) -> dict[str, str]:
    country = _country(ship_to_country)
    if country == "BR" or country not in PROMOTION_COUNTRIES:
        raise ProviderError("ALIEXPRESS_PROMOTION_COUNTRY_UNSUPPORTED", retryable=False)
    language = target_language.strip().upper()
    if language not in PROMOTION_LANGUAGES:
        raise ValueError("target_language is not documented for promotion.info.get")
    if not 1 <= len(product_ids) <= 10:
        raise ValueError("promotion.info.get accepts between 1 and 10 product IDs")
    return {
        "currency": _currency(currency),
        "target_language": language,
        "product_id": _csv_ids(product_ids, "product_id"),
        "ship_to_country": country,
    }


def _required(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} cannot be empty")
    return cleaned


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _identifier(value: str, name: str) -> str:
    cleaned = _required(value, name)
    if not cleaned.isdigit():
        raise ValueError(f"{name} must contain only digits")
    return cleaned


def _csv_ids(values: tuple[str, ...], name: str) -> str:
    if not values:
        raise ValueError(f"{name} cannot be empty")
    return ",".join(_identifier(value, name) for value in values)


def _csv_text(values: tuple[str, ...], name: str) -> str:
    cleaned = tuple(_required(value, name) for value in values)
    if any("," in value for value in cleaned):
        raise ValueError(f"{name} values cannot contain commas")
    return ",".join(cleaned)


def _currency(value: str) -> str:
    currency = value.strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("currency is not documented for the AliExpress operation")
    return currency


def _language(value: str) -> str:
    language = value.strip().upper()
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError("language is not documented for the AliExpress operation")
    return language


def _country(value: str) -> str:
    country = value.strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise ValueError("country must be a two-letter code")
    return country


def _choice(value: str | None, name: str, choices: set[str]) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in choices:
        raise ValueError(f"{name} has an undocumented value")
    return normalized


def _bounded_int(value: int, name: str, *, minimum: int, maximum: int | None = None) -> str:
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside the documented range")
    return str(value)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal parameters must be finite")
    return format(value, "f")
