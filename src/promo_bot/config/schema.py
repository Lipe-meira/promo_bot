"""Schema for the non-secret YAML configuration."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StoreName = Literal["mercadolivre", "amazon", "shopee", "aliexpress", "kabum"]
AffiliateMode = Literal["official_api", "browser", "manual", "disabled"]
BrowserExecutionMode = Literal["collect_only", "manual", "scheduled", "immediate"]


class MercadoLivreBrowserConfig(BaseModel):
    """Non-secret, fail-closed controls for the gated browser integration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    headless: bool = False
    execution_mode: BrowserExecutionMode = "collect_only"
    max_generations_per_hour: int = Field(default=6, ge=1, le=30)
    min_interval_seconds: int = Field(default=60, ge=30, le=3_600)
    jitter_seconds: int = Field(default=15, ge=0, le=60)
    timeout_seconds: int = Field(default=90, ge=30, le=300)
    max_attempts: int = Field(default=3, ge=1, le=5)
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=10)
    circuit_breaker_cooldown_minutes: int = Field(default=60, ge=5, le=1_440)
    allowed_affiliate_hosts: tuple[str, ...] = ()
    registered_labels: tuple[str, ...] = ()

    @field_validator("allowed_affiliate_hosts")
    @classmethod
    def validate_hostnames(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            hostname = value.strip().rstrip(".").casefold()
            try:
                parsed = urlsplit(f"//{hostname}")
                parsed_hostname = parsed.hostname
                parsed_port = parsed.port
            except (UnicodeError, ValueError) as exc:
                raise ValueError("affiliate host entries must be sanitized hostnames") from exc
            if (
                not hostname
                or parsed_hostname != hostname
                or parsed_port is not None
                or any(character in value for character in "/?#@")
            ):
                raise ValueError("affiliate host entries must be sanitized hostnames")
            normalized.append(hostname.encode("idna").decode("ascii"))
        if len(set(normalized)) != len(normalized):
            raise ValueError("affiliate host entries must be unique")
        return tuple(normalized)

    @field_validator("registered_labels")
    @classmethod
    def validate_registered_labels(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value or len(value) > 120 for value in cleaned):
            raise ValueError("registered labels must contain 1 to 120 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("registered labels must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_browser_mode(self) -> MercadoLivreBrowserConfig:
        if self.headless and not self.enabled:
            raise ValueError("headless mode requires the browser to be enabled")
        return self


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    affiliate_mode: AffiliateMode = "disabled"
    browser: MercadoLivreBrowserConfig | None = None

    @model_validator(mode="after")
    def disabled_provider_has_safe_mode(self) -> ProviderConfig:
        if not self.enabled and self.affiliate_mode not in {"disabled", "manual"}:
            raise ValueError("a disabled provider cannot use an automatic affiliate mode")
        if self.browser is not None and self.browser.enabled:
            if not self.enabled or self.affiliate_mode != "browser":
                raise ValueError("browser execution requires an enabled browser-mode provider")
        return self


class TelegramRelayConfig(BaseModel):
    """Conservative, non-secret limits for the local Telegram relay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catch_up_on_start: bool = True
    catch_up_lookback_hours: int = Field(default=6, ge=1, le=168)
    catch_up_max_messages_per_channel: int = Field(default=100, ge=1, le=1_000)
    queue_max_size: int = Field(default=200, ge=1, le=10_000)
    recovery_batch_size: int = Field(default=50, ge=1, le=1_000)
    processing_max_attempts: int = Field(default=5, ge=1, le=20)
    processing_stale_after_minutes: int = Field(default=15, ge=1, le=1_440)
    retry_initial_seconds: int = Field(default=2, ge=1, le=300)
    retry_max_seconds: int = Field(default=300, ge=1, le=86_400)
    redirect_max_hops: int = Field(default=5, ge=0, le=10)
    http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @model_validator(mode="after")
    def validate_retry_window(self) -> TelegramRelayConfig:
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds must be >= retry_initial_seconds")
        return self


class AppConfig(BaseModel):
    """Validated, non-secret behavior configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_channels: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    blacklist: tuple[str, ...] = ()
    minimum_discount_percent: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    minimum_score: int = Field(default=50, ge=0, le=100)
    cooldown_hours: int = Field(default=24, ge=1, le=8_760)
    maximum_promotions_per_hour: int = Field(default=10, ge=1, le=1_000)
    search_intervals_minutes: dict[StoreName, int] = Field(default_factory=dict)
    providers: dict[StoreName, ProviderConfig] = Field(default_factory=dict)
    templates: tuple[str, ...] = ()
    affiliate_disclosure: str
    maximum_price: Decimal | None = Field(default=None, gt=0)
    allowed_sellers: tuple[str, ...] = ()
    blocked_sellers: tuple[str, ...] = ()
    presentation_timezone: str = "America/Sao_Paulo"
    provider_rate_limits_per_minute: dict[StoreName, int] = Field(default_factory=dict)
    telegram_relay: TelegramRelayConfig = Field(default_factory=TelegramRelayConfig)

    @field_validator("source_channels", mode="before")
    @classmethod
    def normalize_channels(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(item) for item in value]
        return value

    @field_validator("search_intervals_minutes", "provider_rate_limits_per_minute")
    @classmethod
    def validate_positive_mapping(cls, value: dict[StoreName, int]) -> dict[StoreName, int]:
        if any(item < 1 for item in value.values()):
            raise ValueError("all provider limits and intervals must be positive")
        return value

    @field_validator("presentation_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("presentation_timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("templates")
    @classmethod
    def validate_templates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one message template is required")
        if any(not template.strip() for template in value):
            raise ValueError("message templates cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_seller_lists(self) -> AppConfig:
        overlap = {seller.casefold() for seller in self.allowed_sellers} & {
            seller.casefold() for seller in self.blocked_sellers
        }
        if overlap:
            raise ValueError("a seller cannot be both allowed and blocked")
        for store, provider in self.providers.items():
            if store != "mercadolivre" and provider.browser is not None:
                raise ValueError("browser settings are supported only for Mercado Livre")
        return self
