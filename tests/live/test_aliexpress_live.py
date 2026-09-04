"""Explicitly gated read-only AliExpress TOP check; never part of the default test run."""

from __future__ import annotations

import os

import pytest

from promo_bot.config import EnvironmentSettings
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import PRODUCT_DETAIL, product_detail_payload
from promo_bot.providers.aliexpress.parsing import parse_product_detail
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import (
    AliExpressHttpTransport,
    build_offline_safe_http_client,
)

pytestmark = [pytest.mark.live, pytest.mark.enable_socket]


def require_live_authorization() -> tuple[EnvironmentSettings, str]:
    if os.getenv("RUN_ALIEXPRESS_LIVE_TEST") != "1":
        pytest.skip("set RUN_ALIEXPRESS_LIVE_TEST=1 only after explicit authorization")

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
    settings, product_id = require_live_authorization()
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
