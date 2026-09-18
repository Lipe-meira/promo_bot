"""Strict configuration for manual AliExpress discovery profiles."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from promo_bot.config.loader import UniqueKeySafeLoader


class DiscoveryConfigError(ValueError):
    """A discovery profile file is unavailable or violates the MVP contract."""


class SkuRefinementRequirement(BaseModel):
    """A single SKU property and the literal values accepted for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    property_name: str
    accepted_values: tuple[str, ...]

    @field_validator("property_name")
    @classmethod
    def normalize_property_name(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("property_name cannot be empty")
        return normalized

    @field_validator("accepted_values")
    @classmethod
    def normalize_accepted_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().casefold() for value in values)
        if not 1 <= len(normalized) <= 20 or any(not value for value in normalized):
            raise ValueError("accepted_values must contain between 1 and 20 entries")
        if len(set(normalized)) != len(normalized):
            raise ValueError("accepted_values must be unique")
        return normalized


class SkuRefinementProfile(BaseModel):
    """Bounded, optional SKU evidence requirements for a discovery profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_refined_products: int = Field(ge=1, le=20)
    max_sku_api_calls: int = Field(ge=1, le=20)
    requirements: tuple[SkuRefinementRequirement, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_budgets_and_requirements(self) -> SkuRefinementProfile:
        if self.max_sku_api_calls > self.max_refined_products:
            raise ValueError("max_sku_api_calls cannot exceed max_refined_products")
        names = tuple(requirement.property_name for requirement in self.requirements)
        if len(set(names)) != len(names):
            raise ValueError("requirements must use unique property names")
        return self


class DiscoveryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keywords: tuple[str, ...]
    category_ids: tuple[str, ...] = ()
    ship_to_country: Literal["BR"]
    target_currency: Literal["BRL"]
    target_language: Literal["PT"]
    page_size: int = Field(ge=1, le=50)
    max_pages: int = Field(ge=1, le=5)
    max_results: int = Field(ge=1, le=250)
    max_api_calls: int = Field(ge=1, le=20)
    minimum_price_drop_percent: Decimal = Field(gt=0, le=100)
    sku_refinement: SkuRefinementProfile | None = None

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if not 1 <= len(cleaned) <= 20:
            raise ValueError("keywords must contain between 1 and 20 entries")
        if any(not value or len(value) > 100 for value in cleaned):
            raise ValueError("keywords must contain between 1 and 100 characters")
        folded = tuple(value.casefold() for value in cleaned)
        if len(set(folded)) != len(folded):
            raise ValueError("keywords must be unique")
        return cleaned

    @field_validator("category_ids")
    @classmethod
    def validate_category_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 20:
            raise ValueError("category_ids cannot contain more than 20 entries")
        if any(re.fullmatch(r"[0-9]+", value) is None for value in values):
            raise ValueError("category_ids must contain ASCII digits only")
        if len(set(values)) != len(values):
            raise ValueError("category_ids must be unique")
        return values


class DiscoveryProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    profiles: dict[str, DiscoveryProfile]

    @field_validator("profiles")
    @classmethod
    def validate_profile_names(
        cls, values: dict[str, DiscoveryProfile]
    ) -> dict[str, DiscoveryProfile]:
        if not values:
            raise ValueError("at least one discovery profile is required")
        if any(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name) is None for name in values):
            raise ValueError("discovery profile name is invalid")
        return values

    def get(self, name: str) -> DiscoveryProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise DiscoveryConfigError("ALIEXPRESS_DISCOVERY_PROFILE_NOT_FOUND") from exc


def load_discovery_profiles(path: Path) -> DiscoveryProfiles:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeySafeLoader)
    except OSError as exc:
        raise DiscoveryConfigError("ALIEXPRESS_DISCOVERY_PROFILE_FILE_UNAVAILABLE") from exc
    except yaml.YAMLError as exc:
        raise DiscoveryConfigError("ALIEXPRESS_DISCOVERY_PROFILE_YAML_INVALID") from exc
    try:
        return DiscoveryProfiles.model_validate(raw)
    except ValidationError as exc:
        raise DiscoveryConfigError("ALIEXPRESS_DISCOVERY_PROFILE_INVALID") from exc
