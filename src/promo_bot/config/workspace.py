"""Explicit, isolated, fail-closed configuration for one hosted workspace (SaaS S1).

The legacy runtime reads `EnvironmentSettings` from the process environment and `.env` and keeps
everything under one global directory. A hosted worker serving several workspaces must never do
that: a field missing from a workspace would silently fall back to the server's (or another
user's) value. `WorkspaceRuntimeSpec` therefore builds the *same* `EnvironmentSettings` and
`AppConfig` the runtime already consumes, but

* from explicit data only (a settings subclass whose single source is its constructor);
* accepting just an allow-list of credentials and limits; gates and paths cannot be supplied;
* with every path derived from a validated UUID below a controlled root.

Building a spec is pure: no file or directory is created, and no Telegram, AliExpress or database
code runs. Errors carry a stable code and never echo the rejected input.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings

#: The default global runtime directory of the legacy single-installation layout.
_LEGACY_RUNTIME_DIRNAME: Final = ".promo_bot"
_SAFE_FIELD_NAMES: Final = frozenset(EnvironmentSettings.model_fields)


class WorkspaceSpecError(ValueError):
    """A workspace specification was refused. `code` is stable; the message never echoes input."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class WorkspaceManagedSettings(BaseModel):
    """The only settings a workspace may provide. Gates and paths are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    telegram_api_id: int | None = Field(default=None, strict=True, ge=1)
    telegram_api_hash: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_target_chat_id: str | None = None
    aliexpress_app_key: SecretStr | None = None
    aliexpress_app_secret: SecretStr | None = None
    aliexpress_tracking_id: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    max_promotions_per_hour: int = Field(default=10, ge=1, le=1_000)
    telegram_retry_after_max_seconds: int = Field(default=300, ge=1, le=86_400)


class _WorkspaceEnvironmentSettings(EnvironmentSettings):
    """`EnvironmentSettings` whose only source is its constructor: no env vars, no `.env`."""

    model_config = SettingsConfigDict(env_file=None, frozen=True)  # merged with the parent config

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _parse_workspace_id(value: object) -> UUID:
    """One canonical spelling per workspace, so two spellings can never share or split a path."""

    if isinstance(value, UUID):
        candidate = value
    elif isinstance(value, str):
        try:
            candidate = UUID(value)
        except ValueError:
            raise WorkspaceSpecError("WORKSPACE_ID_INVALID") from None
        if str(candidate) != value:  # rejects uppercase, braces, urn:, no dashes, whitespace
            raise WorkspaceSpecError("WORKSPACE_ID_INVALID")
    else:
        raise WorkspaceSpecError("WORKSPACE_ID_INVALID")
    if candidate.int in (0, (1 << 128) - 1):
        raise WorkspaceSpecError("WORKSPACE_ID_INVALID")
    return candidate


def _reject_ambiguous_windows_name(text: str) -> None:
    # Windows drops trailing dots and spaces on creation, so the literal spelling would differ
    # from the effective one. Same rule as the runtime database path (T5A / N-01).
    if os.name != "nt":
        return
    for component in text.replace("\\", "/").split("/"):
        if component in (".", "..") or not component:
            continue
        if component.endswith((".", " ")):
            raise WorkspaceSpecError("WORKSPACE_RUNTIME_ROOT_INVALID")


def _overlaps(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def _workspace_directory(runtime_root: object, workspace_id: UUID) -> Path:
    if not isinstance(runtime_root, Path) or not runtime_root.is_absolute():
        raise WorkspaceSpecError("WORKSPACE_RUNTIME_ROOT_INVALID")
    literal = str(runtime_root)
    _reject_ambiguous_windows_name(literal)
    if ".." in literal.replace("\\", "/").split("/"):
        raise WorkspaceSpecError("WORKSPACE_RUNTIME_ROOT_INVALID")
    root = runtime_root.resolve()
    if root == Path(root.anchor):
        raise WorkspaceSpecError("WORKSPACE_RUNTIME_ROOT_INVALID")
    legacy = (Path.home() / _LEGACY_RUNTIME_DIRNAME).resolve()
    if _overlaps(root, legacy):
        # The root may not be, contain (the home directory or any ancestor) or live inside the
        # legacy global runtime directory.
        raise WorkspaceSpecError("WORKSPACE_RUNTIME_ROOT_INVALID")
    directory = root / str(workspace_id)
    # An existing link inside the root must not redirect the workspace elsewhere.
    if directory.resolve() != directory:
        raise WorkspaceSpecError("WORKSPACE_RUNTIME_ROOT_INVALID")
    return directory


def _validate_settings(raw: object) -> WorkspaceManagedSettings:
    if not isinstance(raw, Mapping):
        raise WorkspaceSpecError("WORKSPACE_SETTINGS_INVALID", "settings must be a mapping")
    try:
        return WorkspaceManagedSettings.model_validate(dict(raw))
    except ValidationError as exc:
        # Only field *names* known to the real settings are reported; never values or unknown keys.
        names = sorted(
            {
                str(item["loc"][0])
                for item in exc.errors(include_input=False, include_url=False)
                if item["loc"] and str(item["loc"][0]) in _SAFE_FIELD_NAMES
            }
        )
        detail = f"invalid or forbidden fields: {', '.join(names)}" if names else "invalid values"
        raise WorkspaceSpecError("WORKSPACE_SETTINGS_INVALID", detail) from None


def _validate_app_config(raw: object) -> AppConfig:
    try:
        config = raw if isinstance(raw, AppConfig) else AppConfig.model_validate(raw)
    except ValidationError:
        raise WorkspaceSpecError("WORKSPACE_APP_CONFIG_INVALID") from None
    # Enabling a provider is a deliberate later step (S7); S1 refuses it instead of ignoring it.
    for provider in config.providers.values():
        if provider.enabled or provider.affiliate_mode not in {"disabled", "manual"}:
            raise WorkspaceSpecError(
                "WORKSPACE_APP_CONFIG_INVALID", "providers cannot be activated in a workspace spec"
            )
        if provider.browser is not None and provider.browser.enabled:
            raise WorkspaceSpecError(
                "WORKSPACE_APP_CONFIG_INVALID", "browser execution is not available to workspaces"
            )
    return config


def _closed_gates(settings: EnvironmentSettings) -> bool:
    return (
        settings.dry_run is True
        and not settings.publish_real_deals
        and not settings.search_enabled
        and not settings.publish_without_affiliate
        and not settings.coupon_browser_verification
        and not settings.telegram_shadow_test_delivery_enabled
        and not settings.aliexpress_live_api_enabled
        and not settings.aliexpress_telegram_shadow_enabled
        and not settings.aliexpress_telegram_shadow_listener_enabled
        and not settings.aliexpress_telegram_shadow_auto_delivery_enabled
        and not settings.aliexpress_coin_short_shadow_enabled
        and not settings.aliexpress_discovery_shadow_enabled
        and not settings.aliexpress_discovery_hotproduct_shadow_enabled
        and not settings.aliexpress_sku_dimension_api_confirmed
        and not settings.aliexpress_discovery_sku_shadow_enabled
        and not settings.mercadolivre_browser_enabled
        and settings.mercadolivre_affiliate_mode == "disabled"
    )


@dataclass(frozen=True, slots=True)
class WorkspaceRuntimeSpec:
    """Everything the existing runtime needs for one workspace, with nothing inherited."""

    workspace_id: UUID
    runtime_dir: Path
    settings: EnvironmentSettings = field(repr=False)
    app_config: AppConfig = field(repr=False)

    @classmethod
    def build(
        cls,
        workspace_id: UUID | str,
        *,
        runtime_root: Path,
        settings: Mapping[str, Any],
        app_config: AppConfig | Mapping[str, Any],
    ) -> WorkspaceRuntimeSpec:
        parsed_id = _parse_workspace_id(workspace_id)
        directory = _workspace_directory(runtime_root, parsed_id)
        managed = _validate_settings(settings)
        config = _validate_app_config(app_config)
        database_path = (directory / "promo_bot.sqlite3").as_posix()
        values: dict[str, Any] = {
            name: getattr(managed, name) for name in WorkspaceManagedSettings.model_fields
        }
        values["PROMO_BOT_RUNTIME_DIR"] = directory
        values["PROMO_BOT_DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
        resolved = _WorkspaceEnvironmentSettings(**values)
        if not _closed_gates(resolved):  # defense in depth: a future default change must not leak
            raise WorkspaceSpecError("WORKSPACE_GATES_NOT_CLOSED")
        return cls(
            workspace_id=parsed_id, runtime_dir=directory, settings=resolved, app_config=config
        )

    def safe_summary(self) -> dict[str, object]:
        """Non-secret operational state, for diagnostics and logs."""

        return {"workspace_id": str(self.workspace_id), **self.settings.safe_summary()}
