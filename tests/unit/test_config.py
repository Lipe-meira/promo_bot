from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from promo_bot.config import ConfigLoadError, EnvironmentSettings, load_app_config
from promo_bot.config.schema import TelegramRelayConfig

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config.example.yaml"


def test_example_yaml_is_valid() -> None:
    config = load_app_config(EXAMPLE_CONFIG)

    assert config.cooldown_hours == 24
    assert config.presentation_timezone == "America/Sao_Paulo"
    assert not any(provider.enabled for provider in config.providers.values())
    assert config.telegram_relay.catch_up_on_start is True
    assert config.telegram_relay.catch_up_lookback_hours == 6
    assert config.telegram_relay.catch_up_max_messages_per_channel == 100


def test_invalid_yaml_root_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="root must be a mapping"):
        load_app_config(path)


def test_unknown_yaml_keys_are_rejected_without_dumping_input(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("unexpected_secret: should-not-be-echoed\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError) as captured:
        load_app_config(path)

    assert "should-not-be-echoed" not in str(captured.value)


def test_environment_defaults_are_fail_closed() -> None:
    settings = EnvironmentSettings(_env_file=None)

    assert settings.dry_run is True
    assert settings.publish_real_deals is False
    assert settings.search_enabled is False
    assert settings.publish_without_affiliate is False
    assert settings.coupon_browser_verification is False


def test_secret_values_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-only-token-value"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)

    settings = EnvironmentSettings(_env_file=None)

    assert secret not in repr(settings)
    assert secret not in str(settings.safe_summary())


def test_empty_example_values_are_ignored(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_API_ID=\nPROMO_BOT_RUNTIME_DIR=\n", encoding="utf-8")

    settings = EnvironmentSettings(_env_file=env_path)

    assert settings.telegram_api_id is None


def test_relay_retry_window_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        TelegramRelayConfig(retry_initial_seconds=10, retry_max_seconds=5)
