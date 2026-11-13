"""S1 — WorkspaceRuntimeSpec: explicit, isolated, fail-closed workspace configuration.

Everything here is synthetic: temporary directories, fake secrets, no network and no database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from promo_bot.config import AppConfig, EnvironmentSettings
from promo_bot.config.workspace import WorkspaceRuntimeSpec, WorkspaceSpecError

WS_A = UUID("11111111-1111-4111-8111-111111111111")
WS_B = UUID("22222222-2222-4222-8222-222222222222")
FAKE_HASH = "synthetic-api-hash-0001"
FAKE_TOKEN = "synthetic-bot-token-0002"
FAKE_KEY = "synthetic-ali-key-0003"
FAKE_SECRET = "synthetic-ali-secret-0004"
SECRETS = (FAKE_HASH, FAKE_TOKEN, FAKE_KEY, FAKE_SECRET)

APP_CONFIG = {"templates": ["{title}"], "affiliate_disclosure": "Link de afiliado"}


def managed(**extra: object) -> dict[str, object]:
    values: dict[str, object] = {
        "telegram_api_id": 12345,
        "telegram_api_hash": FAKE_HASH,
        "telegram_bot_token": FAKE_TOKEN,
        "aliexpress_app_key": FAKE_KEY,
        "aliexpress_app_secret": FAKE_SECRET,
    }
    values.update(extra)
    return values


def build(
    root: Path,
    workspace_id: object = WS_A,
    settings: dict[str, object] | None = None,
    app_config: object = None,
) -> WorkspaceRuntimeSpec:
    return WorkspaceRuntimeSpec.build(
        workspace_id,  # type: ignore[arg-type]
        runtime_root=root,
        settings=managed() if settings is None else settings,
        app_config=APP_CONFIG if app_config is None else app_config,  # type: ignore[arg-type]
    )


def test_builds_explicit_settings_and_app_config(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt")

    assert spec.workspace_id == WS_A
    assert isinstance(spec.settings, EnvironmentSettings)
    assert isinstance(spec.app_config, AppConfig)
    assert spec.settings.telegram_api_id == 12345
    assert spec.settings.telegram_bot_token is not None
    assert spec.settings.telegram_bot_token.get_secret_value() == FAKE_TOKEN


# --- isolation from the host environment ------------------------------------------------------


def test_polluted_environment_is_never_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_user = {
        "TELEGRAM_API_ID": "999",
        "TELEGRAM_API_HASH": "other-user-hash",
        "TELEGRAM_BOT_TOKEN": "other-user-token",
        "TELEGRAM_TARGET_CHAT_ID": "-1009999",
        "ALIEXPRESS_APP_KEY": "other-user-key",
        "ALIEXPRESS_APP_SECRET": "other-user-secret",
        "ALIEXPRESS_TRACKING_ID": "other-user-tracking",
        "SHOPEE_SECRET": "other-user-shopee",
        "AWIN_API_TOKEN": "other-user-awin",
        "PROMO_BOT_DATABASE_URL": "sqlite+aiosqlite:///C:/other/user.sqlite3",
        "PROMO_BOT_RUNTIME_DIR": str(tmp_path / "global-runtime"),
        "PROMO_BOT_LOG_LEVEL": "DEBUG",
        "MAX_PROMOTIONS_PER_HOUR": "777",
    }
    for name, value in other_user.items():
        monkeypatch.setenv(name, value)

    spec = build(tmp_path / "rt", settings={"telegram_api_id": 1})

    settings = spec.settings
    assert settings.telegram_api_hash is None
    assert settings.telegram_bot_token is None
    assert settings.telegram_target_chat_id is None
    assert settings.aliexpress_app_key is None
    assert settings.aliexpress_app_secret is None
    assert settings.aliexpress_tracking_id is None
    assert settings.shopee_secret is None
    assert settings.awin_api_token is None
    assert settings.log_level == "INFO"
    assert settings.max_promotions_per_hour == 10
    assert settings.resolved_database_url.startswith("sqlite+aiosqlite:///")
    assert "other" not in settings.resolved_database_url
    assert "global-runtime" not in str(settings.resolved_runtime_dir)


def test_dotenv_file_in_working_directory_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=dotenv-token\nPUBLISH_REAL_DEALS=true\nDRY_RUN=false\n"
        "ALIEXPRESS_LIVE_API_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(cwd)
    # Undo the suite-wide shield (env vars outrank .env): only ./.env may supply these values.
    for name in ("PUBLISH_REAL_DEALS", "DRY_RUN", "ALIEXPRESS_LIVE_API_ENABLED"):
        monkeypatch.delenv(name)
    monkeypatch.setitem(EnvironmentSettings.model_config, "env_file", ".env")
    assert EnvironmentSettings().publish_real_deals is True  # control: legacy path does read it

    spec = build(tmp_path / "rt", settings={})

    assert spec.settings.telegram_bot_token is None
    assert spec.settings.publish_real_deals is False
    assert spec.settings.dry_run is True
    assert spec.settings.aliexpress_live_api_enabled is False


def test_building_a_spec_does_not_change_the_legacy_settings_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_before = dict(EnvironmentSettings.model_config)
    build(tmp_path / "rt")
    assert dict(EnvironmentSettings.model_config) == config_before

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-token")
    legacy = EnvironmentSettings()
    assert legacy.telegram_bot_token is not None
    assert legacy.telegram_bot_token.get_secret_value() == "legacy-token"


def test_cli_does_not_depend_on_the_workspace_module() -> None:
    cli_source = (Path(__file__).resolve().parents[2] / "src/promo_bot/cli.py").read_text(
        encoding="utf-8"
    )
    assert "workspace" not in cli_source.replace("workspaces", "")


# --- isolation between workspaces -------------------------------------------------------------


def test_two_workspaces_get_disjoint_paths_and_credentials(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    a = build(root, WS_A, managed(telegram_api_hash="hash-of-a"))
    b = build(root, WS_B, managed(telegram_api_hash="hash-of-b"))

    assert a.runtime_dir != b.runtime_dir
    assert not a.runtime_dir.is_relative_to(b.runtime_dir)
    assert not b.runtime_dir.is_relative_to(a.runtime_dir)
    assert a.settings.resolved_database_url != b.settings.resolved_database_url
    assert a.settings.resolved_telegram_session_path != b.settings.resolved_telegram_session_path
    assert a.settings.resolved_mercadolivre_browser_profile_dir != (
        b.settings.resolved_mercadolivre_browser_profile_dir
    )
    assert a.settings.telegram_api_hash is not None and b.settings.telegram_api_hash is not None
    assert a.settings.telegram_api_hash.get_secret_value() == "hash-of-a"
    assert b.settings.telegram_api_hash.get_secret_value() == "hash-of-b"


def test_every_derived_path_stays_inside_the_workspace_directory(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt")
    settings = spec.settings

    assert spec.runtime_dir == (tmp_path / "rt" / str(WS_A)).resolve()
    inside = [
        settings.resolved_runtime_dir,
        settings.resolved_telegram_session_path,
        settings.resolved_mercadolivre_browser_profile_dir,
        Path(settings.resolved_database_url.removeprefix("sqlite+aiosqlite:///")),
    ]
    for path in inside:
        assert path.is_relative_to(spec.runtime_dir), path


def test_same_workspace_always_gets_the_same_directory(tmp_path: Path) -> None:
    assert build(tmp_path / "rt").runtime_dir == build(tmp_path / "rt").runtime_dir


# --- workspace identifier ---------------------------------------------------------------------


def test_accepts_uuid_object_and_canonical_string(tmp_path: Path) -> None:
    assert build(tmp_path / "rt", str(WS_A)).workspace_id == WS_A
    assert build(tmp_path / "rt", WS_A).workspace_id == WS_A


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "..",
        "../" + str(WS_A),
        str(WS_A) + "/..",
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",  # second spelling of aaaaaaaa-… would collide
        str(WS_A).replace("-", ""),
        "{" + str(WS_A) + "}",
        "urn:uuid:" + str(WS_A),
        " " + str(WS_A),
        str(WS_A) + "\n",
        "00000000-0000-0000-0000-000000000000",
        "ffffffff-ffff-ffff-ffff-ffffffffffff",
        12345,
        None,
        b"11111111-1111-4111-8111-111111111111",
        Path("workspace"),
    ],
)
def test_invalid_workspace_ids_are_refused_without_echo(tmp_path: Path, bad: object) -> None:
    root = tmp_path / "rt"
    with pytest.raises(WorkspaceSpecError) as captured:
        build(root, bad)

    assert captured.value.code == "WORKSPACE_ID_INVALID"
    if isinstance(bad, str) and bad.strip():
        assert bad.strip() not in str(captured.value)
    assert not root.exists()


# --- runtime root -----------------------------------------------------------------------------


def test_relative_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(Path("relative/root"))
    assert captured.value.code == "WORKSPACE_RUNTIME_ROOT_INVALID"


def test_root_with_parent_traversal_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt" / ".." / "elsewhere")
    assert captured.value.code == "WORKSPACE_RUNTIME_ROOT_INVALID"


def test_filesystem_root_is_refused() -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(Path(Path.cwd().anchor))
    assert captured.value.code == "WORKSPACE_RUNTIME_ROOT_INVALID"


def test_legacy_global_runtime_directory_is_never_reused(tmp_path: Path) -> None:
    legacy = Path.home() / ".promo_bot"
    for root in (legacy, legacy / "workspaces", Path.home()):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == "WORKSPACE_RUNTIME_ROOT_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="Windows drops trailing dots and spaces")
@pytest.mark.parametrize("name", ["rt.", "rt ", "ok/rt.", "ok/rt /sub"])
def test_ambiguous_windows_root_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(Path(str(tmp_path) + "\\" + name.replace("/", "\\")))
    assert captured.value.code == "WORKSPACE_RUNTIME_ROOT_INVALID"


def test_symlinked_workspace_directory_escaping_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / str(WS_A)).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(WorkspaceSpecError) as captured:
        build(root)
    assert captured.value.code == "WORKSPACE_RUNTIME_ROOT_INVALID"


def test_pure_construction_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    spec = build(root)

    assert not root.exists()
    assert not spec.runtime_dir.exists()
    assert list(tmp_path.iterdir()) == []


def test_construction_touches_no_transport_modules(tmp_path: Path) -> None:
    # Fresh interpreter: other tests may have imported the transports already. The suite's socket
    # guard would also fail a network attempt, but the configuration layer must not even import
    # the Telegram, database or provider code.
    code = """
import pathlib, sys, tempfile
from uuid import UUID
from promo_bot.config.workspace import WorkspaceRuntimeSpec
root = pathlib.Path(tempfile.gettempdir()) / 'promo-bot-s1-never-created'
WorkspaceRuntimeSpec.build(
    UUID('11111111-1111-4111-8111-111111111111'),
    runtime_root=root,
    settings={},
    app_config={'templates': ['x'], 'affiliate_disclosure': 'y'},
)
assert not root.exists()
banned = [m for m in ('telethon', 'alembic', 'sqlalchemy', 'httpx', 'aiohttp') if m in sys.modules]
assert not banned, banned
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# --- gates ------------------------------------------------------------------------------------

GATES = [
    "dry_run",
    "publish_real_deals",
    "search_enabled",
    "publish_without_affiliate",
    "coupon_browser_verification",
    "telegram_shadow_test_delivery_enabled",
    "aliexpress_live_api_enabled",
    "aliexpress_telegram_shadow_enabled",
    "aliexpress_telegram_shadow_listener_enabled",
    "aliexpress_telegram_shadow_auto_delivery_enabled",
    "aliexpress_coin_short_shadow_enabled",
    "aliexpress_discovery_shadow_enabled",
    "aliexpress_discovery_hotproduct_shadow_enabled",
    "aliexpress_sku_dimension_api_confirmed",
    "aliexpress_discovery_sku_shadow_enabled",
    "mercadolivre_browser_enabled",
    "mercadolivre_affiliate_mode",
]


def test_default_spec_has_every_dangerous_gate_closed(tmp_path: Path) -> None:
    settings = build(tmp_path / "rt").settings

    assert settings.dry_run is True
    for name in GATES:
        if name == "dry_run":
            continue
        assert getattr(settings, name) in (False, "disabled"), name
    assert settings.max_promotions_per_hour == 10


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("value", [True, False, "true"])
def test_workspace_data_cannot_set_any_gate(tmp_path: Path, gate: str, value: object) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", settings=managed(**{gate: value}))

    assert captured.value.code == "WORKSPACE_SETTINGS_INVALID"
    assert gate in str(captured.value)


@pytest.mark.parametrize(
    "field",
    ["runtime_dir", "PROMO_BOT_RUNTIME_DIR", "database_url", "PROMO_BOT_DATABASE_URL"],
)
def test_workspace_data_cannot_choose_its_own_paths(tmp_path: Path, field: str) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", settings=managed(**{field: str(tmp_path / "elsewhere")}))
    assert captured.value.code == "WORKSPACE_SETTINGS_INVALID"


def test_unknown_field_name_is_not_echoed(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", settings=managed(**{"my-private-sentinel-name": "x"}))
    assert "my-private-sentinel-name" not in str(captured.value)


def test_provider_activation_in_app_config_is_refused(tmp_path: Path) -> None:
    enabled = {
        **APP_CONFIG,
        "providers": {"aliexpress": {"enabled": True, "affiliate_mode": "official_api"}},
    }
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", app_config=enabled)
    assert captured.value.code == "WORKSPACE_APP_CONFIG_INVALID"


def test_disabled_provider_and_manual_mode_are_accepted(tmp_path: Path) -> None:
    config = {
        **APP_CONFIG,
        "providers": {
            "aliexpress": {"enabled": False, "affiliate_mode": "disabled"},
            "shopee": {"enabled": False, "affiliate_mode": "manual"},
        },
    }
    spec = build(tmp_path / "rt", app_config=config)
    assert not any(p.enabled for p in spec.app_config.providers.values())


def test_invalid_app_config_error_does_not_echo_input(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", app_config={"unexpected": "app-config-sentinel"})
    assert captured.value.code == "WORKSPACE_APP_CONFIG_INVALID"
    assert "app-config-sentinel" not in str(captured.value)


def test_gates_cannot_be_flipped_on_a_built_spec(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt")
    with pytest.raises(AttributeError):
        spec.workspace_id = WS_B  # type: ignore[misc]
    with pytest.raises(Exception):  # noqa: B017 - frozen model, exact type is pydantic's
        spec.settings.publish_real_deals = True


# --- secrets never leak -----------------------------------------------------------------------


def assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def test_repr_str_and_summary_do_not_expose_secrets(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt")

    for text in (
        repr(spec),
        str(spec),
        repr(spec.settings),
        str(spec.settings),
        repr(spec.app_config),
        repr(spec.safe_summary()),
    ):
        assert_no_secret(text)
    assert str(WS_A) in repr(spec)


def test_safe_summary_reports_state_but_no_values(tmp_path: Path) -> None:
    summary = build(tmp_path / "rt").safe_summary()

    assert summary["workspace_id"] == str(WS_A)
    assert summary["dry_run"] is True
    assert summary["publish_real_deals"] is False
    assert summary["telegram_user_credentials_configured"] is True
    assert summary["aliexpress_app_key_configured"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"telegram_api_id": "not-a-number-" + FAKE_SECRET},
        {"log_level": "LOUD-" + FAKE_TOKEN},
        {"max_promotions_per_hour": 0, "telegram_api_hash": FAKE_HASH},
        {"telegram_bot_token": ["wrong-type", FAKE_TOKEN]},
        {"publish_real_deals": FAKE_KEY},
    ],
)
def test_validation_errors_do_not_leak_values(tmp_path: Path, bad: dict[str, object]) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", settings=managed(**bad))

    assert_no_secret(str(captured.value))
    assert_no_secret(repr(captured.value))
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True


def test_error_raised_for_other_id_does_not_reveal_root_path(tmp_path: Path) -> None:
    secret_dir = tmp_path / "very-private-root-name"
    with pytest.raises(WorkspaceSpecError) as captured:
        build(secret_dir, "not-a-uuid")
    assert "very-private-root-name" not in str(captured.value)


def test_workspace_ids_from_uuid4_are_accepted(tmp_path: Path) -> None:
    wid = uuid4()
    assert build(tmp_path / "rt", wid).runtime_dir.name == str(wid)
