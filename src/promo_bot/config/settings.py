"""Environment-backed settings with safe defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentSettings(BaseSettings):
    """Secrets and operational switches loaded from the environment or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
    )

    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_target_chat_id: str | None = None

    shopee_app_id: str | None = None
    shopee_secret: SecretStr | None = None

    aliexpress_app_key: SecretStr | None = None
    aliexpress_app_secret: SecretStr | None = None
    aliexpress_tracking_id: SecretStr | None = None
    aliexpress_live_api_enabled: bool = False

    amazon_credential_id: str | None = None
    amazon_credential_secret: SecretStr | None = None
    amazon_associate_tag: str | None = None

    mercadolivre_affiliate_mode: Literal["official_api", "browser", "manual", "disabled"] = Field(
        default="disabled",
        validation_alias=AliasChoices(
            "MERCADO_LIVRE_AFFILIATE_MODE", "MERCADOLIVRE_AFFILIATE_MODE"
        ),
    )
    mercadolivre_browser_enabled: bool = Field(
        default=False, validation_alias="MERCADO_LIVRE_BROWSER_ENABLED"
    )
    mercadolivre_browser_headless: bool = Field(
        default=False, validation_alias="MERCADO_LIVRE_BROWSER_HEADLESS"
    )
    mercadolivre_browser_profile_dir: Path | None = Field(
        default=None, validation_alias="MERCADO_LIVRE_BROWSER_PROFILE_DIR"
    )

    awin_api_token: SecretStr | None = None
    awin_publisher_id: str | None = None
    awin_kabum_advertiser_id: str | None = None

    dry_run: bool = True
    publish_real_deals: bool = False
    search_enabled: bool = False
    publish_without_affiliate: bool = False
    coupon_browser_verification: bool = False
    max_promotions_per_hour: int = Field(default=10, ge=1, le=1_000)
    telegram_retry_after_max_seconds: int = Field(default=300, ge=1, le=86_400)

    runtime_dir: Path | None = Field(default=None, validation_alias="PROMO_BOT_RUNTIME_DIR")
    database_url: str | None = Field(default=None, validation_alias="PROMO_BOT_DATABASE_URL")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", validation_alias="PROMO_BOT_LOG_LEVEL"
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("sqlite+aiosqlite:///"):
            raise ValueError("only sqlite+aiosqlite database URLs are supported")
        return value

    @property
    def resolved_runtime_dir(self) -> Path:
        """Return the external runtime directory without creating it."""

        return (self.runtime_dir or Path.home() / ".promo_bot").expanduser().resolve()

    @property
    def resolved_database_url(self) -> str:
        """Return an async SQLite URL outside the repository by default."""

        if self.database_url:
            return self.database_url
        database_path = (self.resolved_runtime_dir / "promo_bot.sqlite3").as_posix()
        return f"sqlite+aiosqlite:///{database_path}"

    @property
    def resolved_telegram_session_path(self) -> Path:
        """Return the fixed external session path without creating it."""

        return self.resolved_runtime_dir / "telegram" / "monitor.session"

    @property
    def resolved_mercadolivre_browser_profile_dir(self) -> Path:
        """Return the external browser profile path without creating it."""

        profile = self.mercadolivre_browser_profile_dir
        default_profile = self.resolved_runtime_dir / "browser" / "mercadolivre" / "profile"
        return (profile or default_profile).expanduser().resolve()

    def safe_summary(self) -> dict[str, object]:
        """Expose only non-secret operational state for diagnostics."""

        return {
            "dry_run": self.dry_run,
            "publish_real_deals": self.publish_real_deals,
            "search_enabled": self.search_enabled,
            "publish_without_affiliate": self.publish_without_affiliate,
            "coupon_browser_verification": self.coupon_browser_verification,
            "max_promotions_per_hour": self.max_promotions_per_hour,
            "telegram_retry_after_max_seconds": self.telegram_retry_after_max_seconds,
            "runtime_dir": str(self.resolved_runtime_dir),
            "database_backend": "sqlite+aiosqlite",
            "telegram_user_credentials_configured": bool(
                self.telegram_api_id and self.telegram_api_hash
            ),
            "telegram_bot_credentials_configured": bool(
                self.telegram_bot_token and self.telegram_target_chat_id
            ),
            "telegram_session_path": str(self.resolved_telegram_session_path),
            "aliexpress_app_key_configured": self.aliexpress_app_key is not None,
            "aliexpress_app_secret_configured": self.aliexpress_app_secret is not None,
            "aliexpress_tracking_id_configured": self.aliexpress_tracking_id is not None,
            "aliexpress_live_api_enabled": self.aliexpress_live_api_enabled,
            "mercadolivre_affiliate_mode": self.mercadolivre_affiliate_mode,
            "mercadolivre_browser_enabled": self.mercadolivre_browser_enabled,
            "mercadolivre_browser_headless": self.mercadolivre_browser_headless,
            "mercadolivre_browser_profile_dir": str(self.resolved_mercadolivre_browser_profile_dir),
            "log_level": self.log_level,
        }
