"""Typed configuration loading."""

from promo_bot.config.loader import ConfigLoadError, load_app_config
from promo_bot.config.schema import AppConfig, TelegramRelayConfig
from promo_bot.config.settings import EnvironmentSettings

__all__ = [
    "AppConfig",
    "ConfigLoadError",
    "EnvironmentSettings",
    "TelegramRelayConfig",
    "load_app_config",
]
