"""Safe YAML loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from promo_bot.config.schema import AppConfig


class ConfigLoadError(ValueError):
    """A configuration file could not be read or validated."""


def load_app_config(path: Path) -> AppConfig:
    """Load a YAML mapping with SafeLoader and validate it without exposing secrets."""

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"could not read configuration file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError("configuration file contains invalid YAML") from exc

    if not isinstance(raw, dict):
        raise ConfigLoadError("configuration root must be a mapping")

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_url=False, include_input=False)
        )
        raise ConfigLoadError(f"configuration validation failed: {errors}") from exc
