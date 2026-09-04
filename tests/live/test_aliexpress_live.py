"""Explicitly gated read-only AliExpress TOP check; never part of the default test run."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

from promo_bot.config import EnvironmentSettings
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import (
    LINK_GENERATE,
    PRODUCT_DETAIL,
    link_generate_payload,
    product_detail_payload,
)
from promo_bot.providers.aliexpress.parsing import parse_link_generate, parse_product_detail
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import (
    AliExpressHttpTransport,
    build_offline_safe_http_client,
)
from promo_bot.stores.urls import canonicalize_store_url

pytestmark = [pytest.mark.live, pytest.mark.enable_socket]


def require_live_authorization(gate_name: str) -> tuple[EnvironmentSettings, str]:
    if os.getenv(gate_name) != "1":
        pytest.skip(f"set {gate_name}=1 only after explicit authorization")

    settings = EnvironmentSettings()
    product_id = os.getenv("ALIEXPRESS_LIVE_TEST_PRODUCT_ID", "").strip()
    assert settings.aliexpress_live_api_enabled is True
    assert settings.dry_run is True
    assert settings.publish_real_deals is False
    assert settings.publish_without_affiliate is False
    assert settings.search_enabled is False
    assert settings.aliexpress_app_key is not None
    assert settings.aliexpress_app_secret is not None
    assert settings.aliexpress_tracking_id is not None
    assert product_id.isdigit()
    return settings, product_id


@pytest.mark.asyncio
async def test_one_known_product_detail_without_publication_or_database() -> None:
    settings, product_id = require_live_authorization("RUN_ALIEXPRESS_LIVE_TEST")
    app_key = settings.aliexpress_app_key
    app_secret = settings.aliexpress_app_secret
    tracking_id = settings.aliexpress_tracking_id
    assert app_key is not None
    assert app_secret is not None
    assert tracking_id is not None

    payload = product_detail_payload(
        product_ids=(product_id,),
        tracking_id=tracking_id.get_secret_value(),
        target_currency="BRL",
        target_language="PT",
        country="BR",
    )
    async with build_offline_safe_http_client() as http_client:
        transport = AliExpressHttpTransport(http_client)
        client = AliExpressAffiliateApiClient(
            transport,
            request_builder=AliExpressTopRequestBuilder(
                app_key.get_secret_value(),
                app_secret.get_secret_value(),
            ),
            live_enabled=True,
        )
        response = await client.execute(PRODUCT_DETAIL, payload)

    products = parse_product_detail(response)
    assert any(product.product_id == product_id for product in products)


@pytest.mark.asyncio
async def test_one_known_link_generate_without_publication_or_database() -> None:
    settings, product_id = require_live_authorization("RUN_ALIEXPRESS_LINK_GENERATE_LIVE_TEST")
    app_key = settings.aliexpress_app_key
    app_secret = settings.aliexpress_app_secret
    tracking_id = settings.aliexpress_tracking_id
    assert app_key is not None
    assert app_secret is not None
    assert tracking_id is not None

    source_url = f"https://www.aliexpress.com/item/{product_id}.html"
    payload = link_generate_payload(
        source_values=(source_url,),
        tracking_id=tracking_id.get_secret_value(),
        promotion_link_type=0,
        ship_to_country="BR",
    )
    async with build_offline_safe_http_client() as http_client:
        transport = AliExpressHttpTransport(http_client)
        client = AliExpressAffiliateApiClient(
            transport,
            request_builder=AliExpressTopRequestBuilder(
                app_key.get_secret_value(),
                app_secret.get_secret_value(),
            ),
            live_enabled=True,
        )
        response = await client.execute(LINK_GENERATE, payload)

    links = parse_link_generate(response, requested_source_values=(source_url,))
    if len(links) != 1:
        raise AssertionError("AliExpress link.generate must return exactly one result")
    source = canonicalize_store_url(links[0].source_value)
    if source.external_product_id != product_id:
        raise AssertionError("AliExpress source_value product identity mismatch")
    promotion_url = urlsplit(links[0].promotion_link)
    if promotion_url.scheme != "https" or promotion_url.hostname != "s.click.aliexpress.com":
        raise AssertionError("AliExpress promotion_link host or scheme is invalid")
