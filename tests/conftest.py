"""Offline suite never inherits operator live gates or the checkout's real .env."""

import pytest

from promo_bot.config.settings import EnvironmentSettings


@pytest.fixture(autouse=True)
def isolate_offline_environment(request, monkeypatch, tmp_path_factory):
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("browser"):
        return
    monkeypatch.setitem(EnvironmentSettings.model_config, "env_file", None)
    # Never let a test resolve the operator's real home, runtime directory or database. The
    # runtime directory equals the documented default inside the sandbox home, so tests that
    # assert the default layout keep working; tests that need other values set them explicitly.
    sandbox_home = tmp_path_factory.mktemp("sandbox-home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(sandbox_home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(sandbox_home / ".promo_bot"))
    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
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
