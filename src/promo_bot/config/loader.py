"""Safe YAML loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from promo_bot.config.schema import AppConfig


class ConfigLoadError(ValueError):
    """A configuration file could not be read or validated."""


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Prevent duplicate YAML aliases from silently replacing an authorized destination."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        keys: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in keys:
                    raise yaml.YAMLError("DUPLICATE_CONFIG_KEY")
                keys.add(key)
            except TypeError:
                raise yaml.YAMLError("INVALID_CONFIG_KEY") from None
        return super().construct_mapping(node, deep=deep)


def load_app_config(path: Path) -> AppConfig:
    """Load a YAML mapping with SafeLoader and validate it without exposing secrets."""

    try:
        raw: Any = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeySafeLoader)
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
