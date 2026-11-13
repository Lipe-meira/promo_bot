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

The resulting `AppConfig` is a private snapshot: it is rebuilt from plain data (so no object of
the caller is retained) and its mappings are frozen, because `frozen=True` on the schema does not
make a `dict` field immutable. Neither a later change to the input nor one workspace can reach
another through shared state.

Path limits (documented, not silently accepted): on Windows only drive-letter paths (`C:\\...`)
are supported; UNC (`\\\\server\\share`), `\\\\?\\` and `\\\\.\\` namespaces, drive-relative paths
and names that Windows reinterprets are refused rather than normalised. Long-path (>260)
support is not verified. The SQLite URL must round-trip through SQLAlchemy's URL parser (no
connection is opened) to exactly the validated file.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import make_url

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings

#: The default global runtime directory of the legacy single-installation layout.
_LEGACY_RUNTIME_DIRNAME: Final = ".promo_bot"
_SAFE_FIELD_NAMES: Final = frozenset(EnvironmentSettings.model_fields)
_ROOT_INVALID: Final = "WORKSPACE_RUNTIME_ROOT_INVALID"
_APP_CONFIG_INVALID: Final = "WORKSPACE_APP_CONFIG_INVALID"
#: Managed field name -> the alias `EnvironmentSettings` really reads. Internal only: the public
#: allow-list never accepts an alias.
_SETTINGS_ALIASES: Final = {"log_level": "PROMO_BOT_LOG_LEVEL"}

_WINDOWS_DRIVE_PATH: Final = re.compile(r"[A-Za-z]:[\\/]")
_WINDOWS_INVALID_CHARS: Final = frozenset('<>:"|?*')
_WINDOWS_RESERVED: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{device}{suffix}" for device in ("COM", "LPT") for suffix in "123456789\u00b9\u00b2\u00b3"}
)

#: Every dangerous gate and the only value it may have in a workspace. A new boolean setting must
#: be classified here (a test enforces it) so that no default can silently open an operation.
_CLOSED_GATES: Final[dict[str, object]] = {
    "dry_run": True,
    "publish_real_deals": False,
    "search_enabled": False,
    "publish_without_affiliate": False,
    "coupon_browser_verification": False,
    "telegram_shadow_test_delivery_enabled": False,
    "aliexpress_live_api_enabled": False,
    "aliexpress_telegram_shadow_enabled": False,
    "aliexpress_telegram_shadow_listener_enabled": False,
    "aliexpress_telegram_shadow_auto_delivery_enabled": False,
    "aliexpress_coin_short_shadow_enabled": False,
    "aliexpress_discovery_shadow_enabled": False,
    "aliexpress_discovery_hotproduct_shadow_enabled": False,
    "aliexpress_sku_dimension_api_confirmed": False,
    "aliexpress_discovery_sku_shadow_enabled": False,
    "mercadolivre_browser_enabled": False,
    "mercadolivre_affiliate_mode": "disabled",
}


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


def _check_path_text(text: str) -> None:
    """Refuse, on the literal spelling, anything whose identity is ambiguous or not portable.

    Applied both to the caller's text and to the resolved result, because `resolve()` may turn a
    mapped drive into a UNC or extended-prefix path. Messages never include the text.
    """

    if "?" in text or any(ord(char) < 32 for char in text):  # NUL, controls, URL query mark
        raise WorkspaceSpecError(_ROOT_INVALID)
    parts = text.replace("\\", "/").split("/")
    if ".." in parts:
        raise WorkspaceSpecError(_ROOT_INVALID)
    if os.name != "nt":
        return
    # Only `X:\...` is a supported namespace: no UNC, extended/device prefixes or drive-relative.
    if not _WINDOWS_DRIVE_PATH.match(text):
        raise WorkspaceSpecError(_ROOT_INVALID)
    for component in parts[1:]:
        if component in ("", "."):
            continue
        if component.endswith((".", " ")) or _WINDOWS_INVALID_CHARS & set(component):
            raise WorkspaceSpecError(_ROOT_INVALID)
        if component.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED:
            raise WorkspaceSpecError(_ROOT_INVALID)


def _overlaps(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def _legacy_runtime_directories() -> list[Path]:
    """Global runtime directories a workspace must never overlap (resolved, so aliases count)."""

    legacy = [(Path.home() / _LEGACY_RUNTIME_DIRNAME).resolve()]
    configured = os.environ.get("PROMO_BOT_RUNTIME_DIR")
    if configured:
        try:  # only compared, never used as a value; an unusable one cannot overlap
            legacy.append(Path(configured).expanduser().resolve())
        except (OSError, ValueError, RuntimeError):
            pass
    return legacy


def _derive_workspace_directory(runtime_root: object, workspace_id: UUID) -> Path:
    if not isinstance(runtime_root, Path) or not runtime_root.is_absolute():
        raise WorkspaceSpecError(_ROOT_INVALID)
    _check_path_text(str(runtime_root))
    root = runtime_root.resolve()
    _check_path_text(str(root))
    if root == Path(root.anchor):
        raise WorkspaceSpecError(_ROOT_INVALID)
    if any(_overlaps(root, legacy) for legacy in _legacy_runtime_directories()):
        # Not the global runtime, inside it, or an ancestor containing it (home included).
        raise WorkspaceSpecError(_ROOT_INVALID)
    directory = root / str(workspace_id)
    # A link inside the root must not redirect the workspace elsewhere.
    if directory.resolve() != directory:
        raise WorkspaceSpecError(_ROOT_INVALID)
    # The nearest existing path must be a real directory (never a file squatting a name).
    for candidate in (directory, root, *root.parents):
        if candidate.exists():
            if not candidate.is_dir():
                raise WorkspaceSpecError(_ROOT_INVALID)
            break
    return directory


def _workspace_directory(runtime_root: object, workspace_id: UUID) -> Path:
    try:
        return _derive_workspace_directory(runtime_root, workspace_id)
    except WorkspaceSpecError:
        raise
    except (OSError, ValueError, RuntimeError):  # NUL, symlink loops, ACL errors, ...
        raise WorkspaceSpecError(_ROOT_INVALID) from None


def _database_url(directory: Path) -> str:
    """A SQLite URL that provably denotes the validated file (parser round-trip, no connection)."""

    database = (directory / "promo_bot.sqlite3").as_posix()
    url = f"sqlite+aiosqlite:///{database}"
    try:
        parsed = make_url(url)
        faithful = (
            parsed.drivername == "sqlite+aiosqlite"
            and not parsed.query
            and parsed.database == database
        )
    except Exception:
        faithful = False
    if not faithful:
        raise WorkspaceSpecError(_ROOT_INVALID)
    return url


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


class _FrozenDict(dict[Any, Any]):
    """A `dict` (the runtime and pydantic expect one) that refuses every mutation."""

    def _refuse(self, *args: object, **kwargs: object) -> Any:
        raise TypeError("workspace configuration is immutable")

    __setitem__ = __delitem__ = __ior__ = _refuse
    update = pop = popitem = clear = setdefault = _refuse

    def __reduce__(self) -> tuple[Any, ...]:  # copy/deepcopy/pickle must not call __setitem__
        return (_FrozenDict, (dict(self),))


def _freeze(value: Any) -> Any:
    """Freeze, in place, the mappings of a model tree that we exclusively own (a fresh copy)."""

    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            # The schema models are frozen=True, which blocks setattr but not a dict's contents.
            object.__setattr__(value, name, _freeze(getattr(value, name)))
        return value
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _snapshot_app_config(raw: object) -> AppConfig:
    """Validate a private copy of the input and freeze it; nothing of the caller is retained."""

    try:
        if isinstance(raw, AppConfig):
            data: object = raw.model_dump()  # plain data: no shared model or dict instance
        elif isinstance(raw, Mapping):
            data = copy.deepcopy(dict(raw))  # nested model instances are copied, not referenced
        else:
            raise WorkspaceSpecError(_APP_CONFIG_INVALID)
        config = AppConfig.model_validate(data)
    except WorkspaceSpecError:
        raise
    except Exception:  # ValidationError, or a hostile mapping that cannot be copied
        raise WorkspaceSpecError(_APP_CONFIG_INVALID) from None
    # Enabling a provider is a deliberate later step (S7); S1 refuses it instead of ignoring it.
    for provider in config.providers.values():
        if provider.enabled or provider.affiliate_mode not in {"disabled", "manual"}:
            raise WorkspaceSpecError(
                _APP_CONFIG_INVALID, "providers cannot be activated in a workspace spec"
            )
        if provider.browser is not None and provider.browser.enabled:
            raise WorkspaceSpecError(
                _APP_CONFIG_INVALID, "browser execution is not available to workspaces"
            )
    frozen: AppConfig = _freeze(config)
    return frozen


def _assert_gates_closed(settings: EnvironmentSettings) -> None:
    """Final guard, independent of defaults: every dangerous gate must have its closed value."""

    for name, closed in _CLOSED_GATES.items():
        value = getattr(settings, name)
        if type(value) is not type(closed) or value != closed:
            raise WorkspaceSpecError("WORKSPACE_GATES_NOT_CLOSED")


def _assert_managed_values_applied(settings: EnvironmentSettings, managed: BaseModel) -> None:
    """A managed value that did not land (e.g. an alias drift) must not be dropped silently."""

    for name in WorkspaceManagedSettings.model_fields:
        expected = getattr(managed, name)
        actual = getattr(settings, name)
        if isinstance(expected, SecretStr):
            expected = expected.get_secret_value()
            actual = actual.get_secret_value() if isinstance(actual, SecretStr) else actual
        if actual != expected:
            raise WorkspaceSpecError("WORKSPACE_SETTINGS_NOT_APPLIED")


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
        database_url = _database_url(directory)
        managed = _validate_settings(settings)
        config = _snapshot_app_config(app_config)
        values: dict[str, Any] = {
            _SETTINGS_ALIASES.get(name, name): getattr(managed, name)
            for name in WorkspaceManagedSettings.model_fields
        }
        values["PROMO_BOT_RUNTIME_DIR"] = directory
        values["PROMO_BOT_DATABASE_URL"] = database_url
        resolved = _WorkspaceEnvironmentSettings(**values)
        _assert_managed_values_applied(resolved, managed)
        _assert_gates_closed(resolved)  # defense in depth: a changed default must not open a gate
        return cls(
            workspace_id=parsed_id, runtime_dir=directory, settings=resolved, app_config=config
        )

    def safe_summary(self) -> dict[str, object]:
        """Non-secret operational state, for diagnostics and logs."""

        return {"workspace_id": str(self.workspace_id), **self.settings.safe_summary()}
