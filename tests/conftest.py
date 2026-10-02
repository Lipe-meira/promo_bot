"""Offline suite never inherits operator live gates or the checkout's real .env."""

import pytest

from promo_bot.config.settings import EnvironmentSettings


@pytest.fixture(autouse=True)
def isolate_offline_environment(request, monkeypatch):
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("browser"):
        return
    monkeypatch.setitem(EnvironmentSettings.model_config, "env_file", None)
    for name in (
        "ALIEXPRESS_LIVE_API_ENABLED",
        "ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED",
        "ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED",
        "ALIEXPRESS_TELEGRAM_SHADOW_ENABLED",
        "ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED",
        "TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED",
        "ALIEXPRESS_DISCOVERY_SHADOW_ENABLED",
        "ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_ENABLED",
        "ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED",
        "ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED",
        "PUBLISH_REAL_DEALS",
        "PUBLISH_WITHOUT_AFFILIATE",
        "SEARCH_ENABLED",
        "COUPON_BROWSER_VERIFICATION",
    ):
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("DRY_RUN", "true")
