"""Strict configuration for manual AliExpress discovery profiles."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from promo_bot.config.loader import UniqueKeySafeLoader


class DiscoveryConfigError(ValueError):
    """A discovery profile file is unavailable or violates the MVP contract."""


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
