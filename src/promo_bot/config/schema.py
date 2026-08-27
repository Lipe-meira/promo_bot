"""Schema for the non-secret YAML configuration."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StoreName = Literal["mercadolivre", "amazon", "shopee", "aliexpress", "kabum"]
AffiliateMode = Literal["official_api", "browser", "manual", "disabled"]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    affiliate_mode: AffiliateMode = "disabled"

    @model_validator(mode="after")
    def disabled_provider_has_safe_mode(self) -> ProviderConfig:
        if not self.enabled and self.affiliate_mode not in {"disabled", "manual"}:
            raise ValueError("a disabled provider cannot use an automatic affiliate mode")
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
        return self
