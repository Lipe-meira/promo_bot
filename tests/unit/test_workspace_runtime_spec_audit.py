"""S1 audit regressions: H-01 (shared AppConfig), M-01 (path identity), L-01, L-02, gate guard.

Synthetic data and temporary directories only; no network, no database connection is opened.
"""

from __future__ import annotations

import copy
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import create_model
from sqlalchemy.engine import make_url

from promo_bot.config import AppConfig, EnvironmentSettings
from promo_bot.config import workspace as ws
from promo_bot.config.schema import ProviderConfig, ShadowDestination
from promo_bot.config.workspace import WorkspaceRuntimeSpec, WorkspaceSpecError
from tests.unit.test_workspace_runtime_spec import APP_CONFIG, GATES, WS_A, WS_B, build

NT = os.name == "nt"
ROOT_INVALID = "WORKSPACE_RUNTIME_ROOT_INVALID"
ENABLED = ProviderConfig(enabled=True, affiliate_mode="official_api")
DEST = {"chat_id": "-1001234567890", "kind": "private_channel"}


def source_config(**extra: Any) -> AppConfig:
    return AppConfig(**{**APP_CONFIG, **extra})


def make_link(link: Path, target: Path) -> None:
    """Directory junction on Windows (no privilege needed), symlink elsewhere."""
    try:
        if NT:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                check=True,
            )
        else:
            link.symlink_to(target, target_is_directory=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("cannot create a directory link here")


# --- H-01: AppConfig isolation -----------------------------------------------------------------


def test_appconfig_instance_is_never_shared_between_workspaces(tmp_path: Path) -> None:
    source = source_config()
    a = build(tmp_path / "rt", WS_A, app_config=source)
    b = build(tmp_path / "rt", WS_B, app_config=source)

    assert a.app_config is not source
    assert b.app_config is not source
    assert a.app_config is not b.app_config
    assert a.app_config.providers is not source.providers
    assert a.app_config.providers is not b.app_config.providers


def test_mutating_the_input_after_build_cannot_reopen_providers(tmp_path: Path) -> None:
    source = source_config()
    a = build(tmp_path / "rt", WS_A, app_config=source)
    b = build(tmp_path / "rt", WS_B, app_config=source)

    source.providers["aliexpress"] = ENABLED  # frozen model, but the dict is mutable

    assert a.app_config.providers == {}
    assert b.app_config.providers == {}


def test_mutating_input_mapping_template_list_and_nested_objects_after_build(
    tmp_path: Path,
) -> None:
    templates = ["{title}"]
    providers: dict[str, object] = {"shopee": ProviderConfig(enabled=False)}
    destinations: dict[str, object] = {"main": ShadowDestination(**DEST)}  # type: ignore[arg-type]
    raw = {
        **APP_CONFIG,
        "templates": templates,
        "providers": providers,
        "telegram_shadow_delivery": {"allowed_destinations": destinations},
    }
    spec = build(tmp_path / "rt", app_config=raw)

    templates.append("{price}")
    providers["aliexpress"] = ENABLED
    destinations["other"] = ShadowDestination(chat_id="-1009999999999", kind="private_channel")

    assert spec.app_config.templates == ("{title}",)
    assert set(spec.app_config.providers) == {"shopee"}
    assert set(spec.app_config.telegram_shadow_delivery.allowed_destinations) == {"main"}


def test_nested_model_instances_are_copied_not_referenced(tmp_path: Path) -> None:
    provider = ProviderConfig(enabled=False)
    destination = ShadowDestination(**DEST)  # type: ignore[arg-type]
    raw = {
        **APP_CONFIG,
        "providers": {"shopee": provider},
        "telegram_shadow_delivery": {"allowed_destinations": {"main": destination}},
    }
    a = build(tmp_path / "rt", WS_A, app_config=raw)
    b = build(tmp_path / "rt", WS_B, app_config=raw)

    assert a.app_config.providers["shopee"] is not provider
    assert a.app_config.providers["shopee"] is not b.app_config.providers["shopee"]
    dest_a = a.app_config.telegram_shadow_delivery.allowed_destinations["main"]
    dest_b = b.app_config.telegram_shadow_delivery.allowed_destinations["main"]
    assert dest_a is not destination
    assert dest_a is not dest_b


MUTATIONS: dict[str, Any] = {
    "setitem": lambda d: d.__setitem__("k", 1),
    "delitem": lambda d: d.__delitem__(next(iter(d))),
    "update": lambda d: d.update({"k": 1}),
    "pop": lambda d: d.pop(next(iter(d))),
    "popitem": lambda d: d.popitem(),
    "clear": lambda d: d.clear(),
    "setdefault": lambda d: d.setdefault("k", 1),
    "ior": lambda d: d.__ior__({"k": 1}),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
@pytest.mark.parametrize(
    "field", ["providers", "search_intervals_minutes", "provider_rate_limits_per_minute", "dest"]
)
def test_result_mappings_are_immutable(tmp_path: Path, field: str, mutation: str) -> None:
    raw = {
        **APP_CONFIG,
        "providers": {"shopee": {"enabled": False}},
        "search_intervals_minutes": {"shopee": 30},
        "provider_rate_limits_per_minute": {"shopee": 5},
        "telegram_shadow_delivery": {"allowed_destinations": {"main": DEST}},
    }
    config = build(tmp_path / "rt", app_config=raw).app_config
    target = (
        config.telegram_shadow_delivery.allowed_destinations
        if field == "dest"
        else getattr(config, field)
    )

    with pytest.raises((TypeError, AttributeError)):  # contract: a refusal, state untouched
        MUTATIONS[mutation](target)

    assert len(target) == 1


def test_result_attributes_cannot_be_reassigned(tmp_path: Path) -> None:
    config = build(tmp_path / "rt").app_config
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen-instance error
        config.providers = {"aliexpress": ENABLED}  # type: ignore[misc]


def test_frozen_result_stays_usable_by_the_runtime(tmp_path: Path) -> None:
    raw = {
        **APP_CONFIG,
        "providers": {"shopee": {"enabled": False, "affiliate_mode": "manual"}},
        "telegram_shadow_delivery": {"allowed_destinations": {"main": DEST}},
    }
    config = build(tmp_path / "rt", app_config=raw).app_config

    # Contract change (R-01): a read-only Mapping, deliberately not a dict.
    assert isinstance(config.providers, Mapping) and not isinstance(config.providers, dict)
    assert config.providers.get("shopee") is not None
    assert config.providers.get("aliexpress") is None
    assert [name for name, _ in config.providers.items()] == ["shopee"]
    dumped = config.model_dump()
    assert dumped["providers"]["shopee"]["affiliate_mode"] == "manual"
    assert AppConfig.model_validate(dumped) == config
    clone = copy.deepcopy(config)
    assert clone == config
    with pytest.raises(TypeError):
        clone.providers["aliexpress"] = ENABLED


def test_a_built_spec_can_be_used_as_the_next_input(tmp_path: Path) -> None:
    first = build(tmp_path / "rt", WS_A)
    second = build(tmp_path / "rt", WS_B, app_config=first.app_config)

    assert second.app_config is not first.app_config
    assert second.app_config == first.app_config


# --- M-01: path identity -----------------------------------------------------------------------


def runtime_database_path(url: str) -> Path:
    from promo_bot.database.runtime_migration import _database_path

    return _database_path(url)


def test_database_url_round_trips_to_the_validated_file(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt")
    url = spec.settings.resolved_database_url
    expected = spec.runtime_dir / "promo_bot.sqlite3"

    parsed = make_url(url)
    assert parsed.drivername == "sqlite+aiosqlite"
    assert not parsed.query
    assert Path(str(parsed.database)).resolve() == expected
    assert runtime_database_path(url) == expected  # what the operational runtime would open


def test_distinct_workspaces_never_share_a_sqlite_target(tmp_path: Path) -> None:
    a = build(tmp_path / "rt", WS_A)
    b = build(tmp_path / "rt", WS_B)

    url_a, url_b = a.settings.resolved_database_url, b.settings.resolved_database_url
    assert url_a != url_b
    assert make_url(url_a).database != make_url(url_b).database
    assert runtime_database_path(url_a) != runtime_database_path(url_b)


@pytest.mark.parametrize("char", ["?", "#", "%", "&", "=", "'", ";", "é", "~", "+", "@", "["])
def test_sqlite_url_always_represents_the_validated_path(tmp_path: Path, char: str) -> None:
    root = tmp_path / f"a{char}b"
    try:
        spec = build(root, WS_A)
    except WorkspaceSpecError as exc:
        assert exc.code == ROOT_INVALID
        return
    url = spec.settings.resolved_database_url
    parsed = make_url(url)
    assert not parsed.query
    assert runtime_database_path(url) == spec.runtime_dir / "promo_bot.sqlite3"
    assert parsed.database is not None and Path(parsed.database).resolve() == (
        spec.runtime_dir / "promo_bot.sqlite3"
    )


def test_question_mark_is_always_refused(tmp_path: Path) -> None:
    for root in (tmp_path / "a?b", tmp_path / "x" / "?" / "y", tmp_path / "rt?mode=ro"):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows path namespaces")
@pytest.mark.parametrize(
    "prefix", ["\\\\?\\", "\\\\.\\", "\\\\?\\UNC\\srv\\share\\", "\\\\srv\\share\\", "//srv/share/"]
)
def test_unsupported_windows_namespaces_are_refused(tmp_path: Path, prefix: str) -> None:
    if prefix.startswith(("\\\\?\\UNC", "\\\\srv", "//")):
        root = Path(prefix + "rt")
    else:
        root = Path(prefix + str(tmp_path).replace("/", "\\") + "\\rt")
    with pytest.raises(WorkspaceSpecError) as captured:
        build(root)
    assert captured.value.code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows path namespaces")
def test_extended_prefix_cannot_bypass_the_legacy_overlap_check() -> None:
    legacy = Path.home() / ".promo_bot"
    for root in (Path("\\\\?\\" + str(legacy)), Path("\\\\?\\" + str(legacy / "inner"))):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows reserved device names")
@pytest.mark.parametrize(
    "name", ["CON", "nul", "Com1", "LPT9", "aux.txt", "PRN.d.e", "com5.sqlite", "CONIN$"]
)
def test_reserved_windows_names_are_refused(tmp_path: Path, name: str) -> None:
    for root in (tmp_path / name, tmp_path / name / "inner", tmp_path / "ok" / name):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows invalid characters")
@pytest.mark.parametrize("char", ["<", ">", '"', "|", "*", ":", "\x01", "\x1f"])
def test_invalid_windows_characters_are_refused(tmp_path: Path, char: str) -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(Path(str(tmp_path) + "\\bad" + char + "name"))
    assert captured.value.code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows drive paths")
def test_drive_relative_paths_are_refused() -> None:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(Path("C:rt"))
    assert captured.value.code == ROOT_INVALID


def test_existing_regular_file_is_not_an_acceptable_root(tmp_path: Path) -> None:
    file_root = tmp_path / "rt"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(WorkspaceSpecError) as captured:
        build(file_root)
    assert captured.value.code == ROOT_INVALID

    with pytest.raises(WorkspaceSpecError) as captured:  # an ancestor that is a file
        build(file_root / "deeper")
    assert captured.value.code == ROOT_INVALID


def test_existing_file_at_the_workspace_directory_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    root.mkdir()
    (root / str(WS_A)).write_text("squatter", encoding="utf-8")

    with pytest.raises(WorkspaceSpecError) as captured:
        build(root, WS_A)
    assert captured.value.code == ROOT_INVALID
    assert build(root, WS_B).runtime_dir == (root / str(WS_B)).resolve()


def test_junction_or_symlink_inside_the_root_cannot_redirect_a_workspace(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    make_link(root / str(WS_A), outside)

    with pytest.raises(WorkspaceSpecError) as captured:
        build(root, WS_A)
    assert captured.value.code == ROOT_INVALID


def test_alias_of_the_legacy_runtime_directory_is_refused(tmp_path: Path) -> None:
    legacy = Path.home() / ".promo_bot"
    legacy.mkdir(parents=True)
    alias = tmp_path / "alias"
    make_link(alias, legacy)

    for root in (alias, alias / "sub"):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows paths are case-insensitive")
def test_legacy_overlap_is_case_insensitive_on_windows() -> None:
    legacy = str(Path.home() / ".promo_bot")
    for root in (Path(legacy.upper()), Path(legacy.lower()) / "x"):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == ROOT_INVALID


def test_configured_global_runtime_directory_is_also_never_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_dir = tmp_path / "global-runtime"
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(global_dir))

    for root in (global_dir, global_dir / "workspaces", tmp_path):
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)
        assert captured.value.code == ROOT_INVALID
    assert build(tmp_path / "sibling").runtime_dir.is_relative_to(tmp_path / "sibling")


# --- L-02: controlled path failures ------------------------------------------------------------


@pytest.mark.parametrize("junk", ["\x00", "a\x00b", "\x00/..", "x\x00"])
def test_nul_in_the_root_is_a_coded_refusal(tmp_path: Path, junk: str) -> None:
    root = Path(str(tmp_path / "private-dir-name") + os.sep + junk)
    with pytest.raises(WorkspaceSpecError) as captured:
        build(root)

    assert captured.value.code == ROOT_INVALID
    assert "private-dir-name" not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "failure",
    [
        OSError("secret-os-detail"),
        RuntimeError("symlink loop"),
        ValueError("embedded null byte secret-value"),
    ],
)
def test_synthetic_resolution_failures_are_coded_and_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    root = tmp_path / "private-dir-name"

    def boom(self: Path, strict: bool = False) -> Path:
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", boom)
        with pytest.raises(WorkspaceSpecError) as captured:
            build(root)

    assert captured.value.code == ROOT_INVALID
    text = str(captured.value) + repr(captured.value)
    for leaked in ("private-dir-name", "secret-os-detail", "symlink loop", "secret-value"):
        assert leaked not in text
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True


def test_stat_failure_while_checking_existing_paths_is_coded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Path) -> bool:
        raise PermissionError("secret-acl-detail")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_dir", boom)
        patch.setattr(Path, "exists", boom)
        with pytest.raises(WorkspaceSpecError) as captured:
            build(tmp_path / "rt")

    assert captured.value.code == ROOT_INVALID
    assert "secret-acl-detail" not in str(captured.value)


# --- L-01: explicit log_level is applied -------------------------------------------------------


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_explicit_log_level_is_applied(tmp_path: Path, level: str) -> None:
    spec = build(tmp_path / "rt", settings={"log_level": level})
    assert spec.settings.log_level == level
    assert spec.safe_summary()["log_level"] == level


def test_log_level_default_and_external_alias(tmp_path: Path) -> None:
    assert build(tmp_path / "rt", settings={}).settings.log_level == "INFO"
    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", settings={"PROMO_BOT_LOG_LEVEL": "ERROR"})
    assert captured.value.code == "WORKSPACE_SETTINGS_INVALID"


def test_a_managed_value_that_does_not_land_in_the_settings_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If an alias ever drifts again, the value must not be silently dropped."""
    drifted = create_model(
        "Drifted",
        __base__=ws._WorkspaceEnvironmentSettings,
        log_level=(str, __import__("pydantic").Field("INFO", validation_alias="SOMETHING_ELSE")),
    )
    monkeypatch.setattr(ws, "_WorkspaceEnvironmentSettings", drifted)

    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt", settings={"log_level": "ERROR"})
    assert captured.value.code == "WORKSPACE_SETTINGS_NOT_APPLIED"


# --- the final gate guard ----------------------------------------------------------------------

HOSTILE_VALUES: dict[str, object] = {
    **{name: True for name in GATES},
    "dry_run": False,
    "mercadolivre_affiliate_mode": "official_api",
}


def test_guard_table_matches_the_independent_gate_list() -> None:
    assert set(ws._CLOSED_GATES) == set(GATES)
    assert ws._CLOSED_GATES["dry_run"] is True
    assert ws._CLOSED_GATES["mercadolivre_affiliate_mode"] == "disabled"


def test_every_boolean_setting_is_classified_as_gate_or_known_harmless() -> None:
    boolean_fields = {
        name for name, info in EnvironmentSettings.model_fields.items() if info.annotation is bool
    }
    harmless = {"mercadolivre_browser_headless"}
    assert boolean_fields - harmless == set(GATES) - {"mercadolivre_affiliate_mode"}, (
        "a new boolean setting must be classified: add it to the guard or to the harmless set"
    )


@pytest.mark.parametrize("gate", GATES)
def test_guard_refuses_each_dangerous_value(gate: str) -> None:
    settings = EnvironmentSettings.model_construct(**{gate: HOSTILE_VALUES[gate]})
    # model_construct leaves every other field at its safe default.
    with pytest.raises(WorkspaceSpecError) as captured:
        ws._assert_gates_closed(settings)
    assert captured.value.code == "WORKSPACE_GATES_NOT_CLOSED"


def test_guard_accepts_the_safe_defaults() -> None:
    ws._assert_gates_closed(EnvironmentSettings.model_construct())


def test_guard_does_not_accept_truthy_lookalikes() -> None:
    for value in (1, "true", "yes"):
        settings = EnvironmentSettings.model_construct(publish_real_deals=value)
        with pytest.raises(WorkspaceSpecError):
            ws._assert_gates_closed(settings)
    with pytest.raises(WorkspaceSpecError):
        ws._assert_gates_closed(EnvironmentSettings.model_construct(dry_run=1))


@pytest.mark.parametrize("gate", GATES)
def test_a_future_unsafe_default_is_refused_by_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    """Simulates someone flipping a default in EnvironmentSettings: build must still refuse."""
    annotation = EnvironmentSettings.model_fields[gate].annotation
    hostile = create_model(
        "Hostile",
        __base__=ws._WorkspaceEnvironmentSettings,
        **{gate: (annotation, HOSTILE_VALUES[gate])},  # type: ignore[call-overload]
    )
    monkeypatch.setattr(ws, "_WorkspaceEnvironmentSettings", hostile)

    with pytest.raises(WorkspaceSpecError) as captured:
        build(tmp_path / "rt")
    assert captured.value.code == "WORKSPACE_GATES_NOT_CLOSED"
    assert not (tmp_path / "rt").exists()


def test_public_entry_point_is_still_the_only_way_to_build(tmp_path: Path) -> None:
    spec = WorkspaceRuntimeSpec.build(
        WS_A, runtime_root=tmp_path / "rt", settings={}, app_config=APP_CONFIG
    )
    assert spec.settings.dry_run is True


@pytest.mark.parametrize("name", ["a?b", "rt?mode=ro", "x?", "?"])
def test_database_url_layer_refuses_what_the_url_parser_would_reinterpret(
    tmp_path: Path, name: str
) -> None:
    # Independent of the path-text checks (which already reject `?` on Windows): the URL itself
    # must round-trip to exactly the validated file, or be refused.
    with pytest.raises(WorkspaceSpecError) as captured:
        ws._database_url(tmp_path / name / str(WS_A))
    assert captured.value.code == ROOT_INVALID


def test_database_url_layer_accepts_a_faithful_path(tmp_path: Path) -> None:
    url = ws._database_url(tmp_path / "rt" / str(WS_A))
    assert make_url(url).database == (tmp_path / "rt" / str(WS_A) / "promo_bot.sqlite3").as_posix()
