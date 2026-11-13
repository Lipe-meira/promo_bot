"""S1 second re-audit: R-01 (incomplete immutability) and R-02 (legacy-runtime path aliases).

Synthetic data and temporary directories only; the real runtime is never touched.
"""

from __future__ import annotations

import copy
import json
import os
import pickle
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from promo_bot.config import AppConfig
from promo_bot.config import workspace as ws
from promo_bot.config.schema import ProviderConfig
from promo_bot.config.workspace import WorkspaceSpecError
from tests.unit.test_workspace_runtime_spec import APP_CONFIG, WS_A, WS_B, build

NT = os.name == "nt"
ROOT_INVALID = "WORKSPACE_RUNTIME_ROOT_INVALID"
UNCOMPARABLE = "WORKSPACE_LEGACY_RUNTIME_UNCOMPARABLE"
ENABLED = ProviderConfig(enabled=True, affiliate_mode="official_api")
DEST = {"chat_id": "-1001234567890", "kind": "private_channel"}

RAW = {
    **APP_CONFIG,
    "providers": {"shopee": {"enabled": False, "affiliate_mode": "manual"}},
    "search_intervals_minutes": {"shopee": 30},
    "provider_rate_limits_per_minute": {"shopee": 5},
    "telegram_shadow_delivery": {"allowed_destinations": {"main": DEST}},
}
FIELDS = ["providers", "search_intervals_minutes", "provider_rate_limits_per_minute", "dest"]


def target_of(config: AppConfig, field: str) -> Any:
    if field == "dest":
        return config.telegram_shadow_delivery.allowed_destinations
    return getattr(config, field)


def trusted_state(config: AppConfig) -> dict[str, object]:
    return copy.deepcopy(AppConfig.model_validate(config.model_dump()).model_dump())


# --- R-01: no dict method (inherited or otherwise) can change the trusted state ----------------

ATTACKS: dict[str, Any] = {
    "reinit": lambda m: m.__init__({"aliexpress": ENABLED}),
    "reinit_kw": lambda m: m.__init__(aliexpress=ENABLED),
    "dict.__init__": lambda m: dict.__init__(m, {"aliexpress": ENABLED}),
    "dict.__setitem__": lambda m: dict.__setitem__(m, "aliexpress", ENABLED),
    "dict.update": lambda m: dict.update(m, {"aliexpress": ENABLED}),
    "dict.__ior__": lambda m: dict.__ior__(m, {"aliexpress": ENABLED}),
    "dict.setdefault": lambda m: dict.setdefault(m, "aliexpress", ENABLED),
    "dict.pop": lambda m: dict.pop(m, "shopee"),
    "dict.popitem": lambda m: dict.popitem(m),
    "dict.clear": lambda m: dict.clear(m),
    "dict.__delitem__": lambda m: dict.__delitem__(m, "shopee"),
    "setitem": lambda m: m.__setitem__("aliexpress", ENABLED),
    "item_assignment": lambda m: m.__setitem__,  # attribute must not even exist
    "update": lambda m: m.update({"aliexpress": ENABLED}),
    "ior": lambda m: m.__ior__({"aliexpress": ENABLED}),
    "pop": lambda m: m.pop("shopee"),
    "clear": lambda m: m.clear(),
    "setdefault": lambda m: m.setdefault("aliexpress", ENABLED),
    "attribute": lambda m: setattr(m, "_view", {"aliexpress": ENABLED}),
    "attribute_new": lambda m: setattr(m, "extra", 1),
    "delattr": lambda m: delattr(m, "_view"),
    "class_swap": lambda m: setattr(m, "__class__", dict),
}
EXPECTED = (TypeError, AttributeError)  # the contract: a refusal, and the state is untouched


@pytest.mark.parametrize("attack", sorted(ATTACKS))
@pytest.mark.parametrize("field", FIELDS)
def test_no_mutation_path_can_change_the_workspace_state(
    tmp_path: Path, field: str, attack: str
) -> None:
    spec = build(tmp_path / "rt", app_config=RAW)
    before = trusted_state(spec.app_config)
    mapping = target_of(spec.app_config, field)

    try:
        ATTACKS[attack](mapping)
    except EXPECTED:
        pass

    assert trusted_state(spec.app_config) == before
    assert "aliexpress" not in spec.app_config.providers
    assert not any(p.enabled for p in spec.app_config.providers.values())


def test_the_reported_reinit_reproduction_no_longer_reopens_a_provider(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt")
    try:
        spec.app_config.providers.__init__({"aliexpress": ENABLED})  # type: ignore[misc]
    except EXPECTED:
        pass
    assert "aliexpress" not in spec.app_config.providers


@pytest.mark.parametrize("field", FIELDS)
def test_frozen_mapping_is_a_mapping_but_not_a_dict(tmp_path: Path, field: str) -> None:
    mapping = target_of(build(tmp_path / "rt", app_config=RAW).app_config, field)

    assert isinstance(mapping, Mapping)
    assert not isinstance(mapping, dict)
    assert len(mapping) == 1
    key = next(iter(mapping))
    assert key in mapping
    assert mapping.get(key) is mapping[key]
    assert mapping.get("missing") is None
    assert list(mapping.keys()) == [key]
    assert list(mapping.values()) == [mapping[key]]
    assert list(mapping.items()) == [(key, mapping[key])]
    assert mapping == dict(mapping)
    with pytest.raises(TypeError):
        mapping["k"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        del mapping[key]  # type: ignore[attr-defined]


def test_nested_models_are_protected(tmp_path: Path) -> None:
    config = build(tmp_path / "rt", app_config=RAW).app_config
    provider = config.providers["shopee"]
    destination = config.telegram_shadow_delivery.allowed_destinations["main"]

    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen-instance error
        provider.enabled = True  # type: ignore[misc]
    with pytest.raises(Exception):  # noqa: B017
        destination.chat_id = "-1009999999999"  # type: ignore[misc]
    with pytest.raises(Exception):  # noqa: B017
        config.telegram_shadow_delivery.allowed_destinations = {}  # type: ignore[misc]
    assert provider.enabled is False
    assert destination.chat_id == DEST["chat_id"]


def test_two_workspaces_built_from_one_input_share_nothing(tmp_path: Path) -> None:
    source = AppConfig.model_validate(RAW)
    a = build(tmp_path / "rt", WS_A, app_config=source)
    b = build(tmp_path / "rt", WS_B, app_config=source)

    for field in FIELDS:
        ma, mb = target_of(a.app_config, field), target_of(b.app_config, field)
        assert ma is not mb
        assert ma is not target_of(source, field)
        for key in ma:
            if isinstance(ma[key], BaseModel):  # small ints are interned; models must differ
                assert ma[key] is not mb[key]
    source.providers["aliexpress"] = ENABLED
    source.search_intervals_minutes["aliexpress"] = 1
    assert "aliexpress" not in a.app_config.providers
    assert "aliexpress" not in b.app_config.search_intervals_minutes


def test_input_mapping_mutated_after_build_is_not_observed(tmp_path: Path) -> None:
    raw: dict[str, Any] = copy.deepcopy(RAW)
    spec = build(tmp_path / "rt", app_config=raw)
    raw["providers"]["aliexpress"] = ENABLED
    raw["telegram_shadow_delivery"]["allowed_destinations"]["x"] = {**DEST, "chat_id": "-1002"}

    assert set(spec.app_config.providers) == {"shopee"}
    assert set(spec.app_config.telegram_shadow_delivery.allowed_destinations) == {"main"}


# --- R-01: copy, serialization and reconstruction contract --------------------------------------


def legacy_equivalent() -> AppConfig:
    return AppConfig.model_validate(RAW)


def test_model_dump_matches_the_legacy_shape_and_is_plain_and_detached(tmp_path: Path) -> None:
    config = build(tmp_path / "rt", app_config=RAW).app_config
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no pydantic serializer warning
        dumped = config.model_dump()
        dumped_json = config.model_dump_json()
        nested = config.telegram_shadow_delivery.model_dump()

    assert dumped == legacy_equivalent().model_dump()
    assert type(dumped["providers"]) is dict
    assert json.loads(dumped_json) == json.loads(legacy_equivalent().model_dump_json())
    assert type(nested["allowed_destinations"]) is dict
    dumped["providers"]["aliexpress"] = "tamper"  # the dump is a detached, mutable copy
    assert "aliexpress" not in config.providers


def test_dump_validates_back_to_an_equal_config(tmp_path: Path) -> None:
    config = build(tmp_path / "rt", app_config=RAW).app_config
    rebuilt = AppConfig.model_validate(config.model_dump())

    assert rebuilt == legacy_equivalent()
    assert build(tmp_path / "rt2", WS_B, app_config=config).app_config == config


def test_deepcopy_copy_and_pickle_keep_the_result_frozen_and_independent(tmp_path: Path) -> None:
    config = build(tmp_path / "rt", app_config=RAW).app_config
    clones = [
        copy.deepcopy(config),
        copy.copy(config),
        config.model_copy(deep=True),
        pickle.loads(pickle.dumps(config)),
    ]
    for clone in clones:
        assert clone == config
        for field in FIELDS:
            mapping = target_of(clone, field)
            assert isinstance(mapping, Mapping) and not isinstance(mapping, dict)
            with pytest.raises(TypeError):
                mapping["aliexpress"] = ENABLED
    assert target_of(clones[0], "providers") is not config.providers
    assert target_of(clones[2], "providers") is not config.providers


def test_runtime_style_consumption_still_works(tmp_path: Path) -> None:
    config = build(tmp_path / "rt", app_config=RAW).app_config

    provider = config.providers.get("shopee")
    assert provider is not None and provider.enabled is False
    assert config.providers.get("aliexpress") is None
    assert sum(item.enabled for item in config.providers.values()) == 0
    assert len(config.providers) == 1
    assert {name for name, _ in config.telegram_shadow_delivery.allowed_destinations.items()} == {
        "main"
    }
    relay = config.telegram_relay.model_copy(update={"processing_max_attempts": 1})
    assert relay.processing_max_attempts == 1
    updated = config.model_copy(update={"telegram_relay": relay})
    assert updated.telegram_relay.processing_max_attempts == 1
    assert target_of(updated, "providers") == config.providers


def test_repr_of_the_frozen_mapping_shows_no_chat_id(tmp_path: Path) -> None:
    config = build(tmp_path / "rt", app_config=RAW).app_config
    assert DEST["chat_id"] not in repr(config)
    assert DEST["chat_id"] not in repr(config.telegram_shadow_delivery.allowed_destinations)


# --- R-02: legacy runtime overlap, including extended-prefix aliases ----------------------------


def extended(path: Path) -> str:
    return "\\\\?\\" + str(path)


def refused(root: Path) -> WorkspaceSpecError:
    with pytest.raises(WorkspaceSpecError) as captured:
        build(root)
    return captured.value


@pytest.fixture
def legacy_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(legacy))
    return legacy


def test_conventional_legacy_runtime_relations(legacy_dir: Path, tmp_path: Path) -> None:
    for root in (
        legacy_dir,  # equal
        legacy_dir / "inner",  # descendant, absent
        legacy_dir / "a" / "b",  # deeper descendant, absent
        tmp_path,  # ancestor containing the legacy runtime
    ):
        assert refused(root).code == ROOT_INVALID
    (legacy_dir / "present").mkdir()
    assert refused(legacy_dir / "present").code == ROOT_INVALID  # existing descendant


def test_independent_root_is_still_accepted(legacy_dir: Path, tmp_path: Path) -> None:
    for name in ("other", "legacy2", "legacy-sibling"):  # prefix-sharing names are not overlaps
        spec = build(tmp_path / name)
        assert spec.runtime_dir == (tmp_path / name / str(WS_A)).resolve()


def test_nonexistent_legacy_runtime_is_still_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ghost = tmp_path / "ghost-legacy"
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(ghost))

    assert not ghost.exists()
    for root in (ghost, ghost / "inner", tmp_path):
        assert refused(root).code == ROOT_INVALID
    assert build(tmp_path / "independent").workspace_id == WS_A


@pytest.mark.skipif(not NT, reason="Windows extended-length prefix")
def test_extended_prefix_legacy_alias_is_the_same_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    alias = Path(extended(legacy))
    assert alias.samefile(legacy)  # physical identity: the reported reproduction
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(alias))

    for root in (legacy, legacy / "inner", tmp_path):
        assert refused(root).code == ROOT_INVALID
    (legacy / "present").mkdir()
    assert refused(legacy / "present").code == ROOT_INVALID
    assert build(tmp_path / "independent").workspace_id == WS_A


@pytest.mark.skipif(not NT, reason="Windows extended-length prefix")
def test_extended_prefix_legacy_alias_that_does_not_exist_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ghost = tmp_path / "ghost-legacy"
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", extended(ghost))

    for root in (ghost, ghost / "inner", tmp_path):
        assert refused(root).code == ROOT_INVALID
    assert build(tmp_path / "independent").workspace_id == WS_A


@pytest.mark.skipif(not NT, reason="Windows case-insensitive paths and prefixes")
def test_extended_prefix_and_case_differences_do_not_hide_the_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "Legacy"
    legacy.mkdir()
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", extended(Path(str(legacy).upper())))

    assert refused(Path(str(legacy).lower())).code == ROOT_INVALID
    assert refused(legacy / "x").code == ROOT_INVALID


@pytest.mark.skipif(not NT, reason="Windows namespaces")
@pytest.mark.parametrize(
    "value",
    ["\\\\?\\UNC\\srv\\share\\rt", "\\\\srv\\share\\rt", "\\\\.\\C:\\rt", "\\\\?\\Volume{x}\\rt"],
)
def test_uncomparable_legacy_runtime_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", value)

    error = refused(tmp_path / "rt")
    assert error.code == UNCOMPARABLE
    assert "srv" not in str(error) and "Volume" not in str(error)
    assert not (tmp_path / "rt").exists()


def test_unresolvable_legacy_runtime_fails_closed_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(tmp_path / "legacy-boom-detail"))
    original = Path.resolve

    def selective(self: Path, strict: bool = False) -> Path:
        if "legacy-boom" in str(self):
            raise OSError("secret-os-detail")
        return original(self, strict)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", selective)
        error = refused(tmp_path / "rt")

    assert error.code == UNCOMPARABLE
    assert "legacy-boom" not in str(error) and "secret-os-detail" not in str(error)
    assert error.__cause__ is None


def test_empty_or_unset_legacy_variable_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", "")
    assert build(tmp_path / "a").workspace_id == WS_A
    monkeypatch.delenv("PROMO_BOT_RUNTIME_DIR")
    assert build(tmp_path / "b").workspace_id == WS_A


def test_home_default_legacy_runtime_still_protected(tmp_path: Path) -> None:
    legacy = Path.home() / ".promo_bot"
    for root in (legacy, legacy / "inner", Path.home()):
        assert refused(root).code == ROOT_INVALID


def test_physical_identity_check_catches_what_string_comparison_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth: even if two spellings differ, the same directory is refused."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(legacy))
    # Pretend the textual comparison saw nothing: only samefile can object.
    monkeypatch.setattr(ws, "_overlaps", lambda first, second: False)

    assert refused(legacy).code == ROOT_INVALID
    assert refused(legacy / "inner").code == ROOT_INVALID  # inexistent: no identity, but...


@pytest.mark.skipif(not NT, reason="Windows namespaces")
def test_resolution_that_turns_the_legacy_runtime_into_unc_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mapped or substituted drive can resolve to UNC: it must not be compared as if drive."""
    legacy = tmp_path / "legacy-mapped"
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(legacy))
    original = Path.resolve

    def selective(self: Path, strict: bool = False) -> Path:
        if "legacy-mapped" in str(self):
            return Path("\\srv\share\legacy")
        return original(self, strict)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", selective)
        assert refused(tmp_path / "rt").code == UNCOMPARABLE


@pytest.mark.skipif(not NT, reason="Windows namespaces")
def test_resolution_that_keeps_an_extended_prefix_is_still_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "legacy-extended"
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(legacy))
    original = Path.resolve

    def selective(self: Path, strict: bool = False) -> Path:
        result = original(self, strict)
        if self.name == "legacy-extended":
            return Path("\\?\\" + str(result))  # what resolve() really does for such input
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", selective)
        assert refused(legacy).code == ROOT_INVALID
        assert refused(legacy / "inner").code == ROOT_INVALID
