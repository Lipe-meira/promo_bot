"""S1 fourth re-audit: R-05 (values silently lost from the `AppConfig` snapshot).

The snapshot used to be the `exclude_unset` dump, so *which fields were set* decided *which values
were kept*: a default container changed after construction is not in `model_fields_set`, and its
content vanished before validation — including a forbidden provider.

Every expectation is the behavior of the legacy `AppConfig` holding the same state. Synthetic data
and temporary directories only.
"""

from __future__ import annotations

import copy
import json
import pickle
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from promo_bot.config import AppConfig
from promo_bot.config.schema import (
    MercadoLivreBrowserConfig,
    ProviderConfig,
    ShadowDestination,
    TelegramRelayConfig,
    TelegramShadowDeliveryConfig,
)
from promo_bot.config.workspace import WorkspaceRuntimeSpec, WorkspaceSpecError
from tests.unit.test_workspace_runtime_spec import WS_A, WS_B, build
from tests.unit.test_workspace_runtime_spec_final import ENABLED, RAW
from tests.unit.test_workspace_runtime_spec_serialization import FILTERS

INVALID = "WORKSPACE_APP_CONFIG_INVALID"
BASE: dict[str, Any] = {"templates": ("{title}",), "affiliate_disclosure": "synthetic"}
CHAT_ID = "-1001234567890"
MANUAL = ProviderConfig(affiliate_mode="manual")


def destination() -> ShadowDestination:
    return ShadowDestination(chat_id=CHAT_ID, kind="private_channel")


def base_model() -> AppConfig:
    return AppConfig(**BASE)


# --- sources: every supported input shape, with state the old snapshot dropped ----------------


def model_default_intervals() -> AppConfig:
    source = base_model()
    source.search_intervals_minutes["shopee"] = 7
    return source


def model_default_rate_limits() -> AppConfig:
    source = base_model()
    source.provider_rate_limits_per_minute["shopee"] = 7
    return source


def model_default_providers() -> AppConfig:
    source = base_model()
    source.providers["shopee"] = MANUAL
    return source


def model_default_shadow_destination() -> AppConfig:
    source = base_model()
    source.telegram_shadow_delivery.allowed_destinations["main"] = destination()
    return source


def model_set_nested_then_mutated() -> AppConfig:
    source = AppConfig(**BASE, telegram_shadow_delivery=TelegramShadowDeliveryConfig())
    source.telegram_shadow_delivery.allowed_destinations["main"] = destination()
    return source


def model_partially_set_nested() -> AppConfig:
    return AppConfig(**BASE, telegram_relay=TelegramRelayConfig(queue_max_size=5))


def model_constructed_with_narrow_fields_set() -> AppConfig:
    values = dict(base_model().__dict__)
    values["search_intervals_minutes"] = {"shopee": 7}
    values["minimum_score"] = 80
    return AppConfig.model_construct(_fields_set={"templates"}, **values)


def mapping_plain() -> dict[str, Any]:
    return copy.deepcopy(RAW)


def mapping_with_mutated_nested_model() -> dict[str, Any]:
    shadow = TelegramShadowDeliveryConfig()
    shadow.allowed_destinations["main"] = destination()
    return {**BASE, "telegram_shadow_delivery": shadow}


def mapping_with_models_inside_mappings() -> dict[str, Any]:
    browser = MercadoLivreBrowserConfig(max_attempts=2)
    return {
        **BASE,
        "providers": {
            "shopee": ProviderConfig(),
            "mercadolivre": ProviderConfig(affiliate_mode="manual", browser=browser),
            "amazon": {"affiliate_mode": "manual"},
        },
        "telegram_shadow_delivery": {"allowed_destinations": {"main": destination()}},
    }


def mapping_reusing_a_mutated_default_container() -> dict[str, Any]:
    donor = base_model()
    donor.providers["shopee"] = MANUAL
    return {**BASE, "providers": donor.providers}


SOURCES: dict[str, Callable[[], Any]] = {
    "model_default_intervals": model_default_intervals,
    "model_default_rate_limits": model_default_rate_limits,
    "model_default_providers": model_default_providers,
    "model_default_shadow_destination": model_default_shadow_destination,
    "model_set_nested_then_mutated": model_set_nested_then_mutated,
    "model_partially_set_nested": model_partially_set_nested,
    "model_constructed_with_narrow_fields_set": model_constructed_with_narrow_fields_set,
    "mapping_plain": mapping_plain,
    "mapping_with_mutated_nested_model": mapping_with_mutated_nested_model,
    "mapping_with_models_inside_mappings": mapping_with_models_inside_mappings,
    "mapping_reusing_a_mutated_default_container": mapping_reusing_a_mutated_default_container,
}


def legacy_of(source: Any) -> AppConfig:
    """What the legacy runtime would hold for this input (models are taken as they are)."""

    return source if isinstance(source, AppConfig) else AppConfig.model_validate(source)


def fields_set_tree(value: Any) -> Any:
    """`model_fields_set` of every model reachable through fields, mappings and sequences."""

    if isinstance(value, BaseModel):
        children = {
            name: fields_set_tree(getattr(value, name)) for name in type(value).model_fields
        }
        return (
            type(value).__name__,
            frozenset(value.model_fields_set),
            {name: child for name, child in children.items() if child is not None},
        )
    if isinstance(value, dict):
        nested = {key: fields_set_tree(item) for key, item in value.items()}
        return {key: item for key, item in nested.items() if item is not None} or None
    if isinstance(value, (tuple, list)):
        items = [fields_set_tree(item) for item in value]
        return items if any(item is not None for item in items) else None
    return None


def built(tmp_path: Path, name: str) -> tuple[AppConfig, AppConfig]:
    """(workspace config, legacy config) for one source; the legacy side is built separately."""

    return build(tmp_path / "rt", app_config=SOURCES[name]()).app_config, legacy_of(SOURCES[name]())


# --- R-05: complete effective values --------------------------------------------------------


def test_the_reported_reproduction_keeps_the_mutated_default_mapping(tmp_path: Path) -> None:
    source = AppConfig(templates=("{title}",), affiliate_disclosure="synthetic")
    source.search_intervals_minutes["shopee"] = 7

    spec = WorkspaceRuntimeSpec.build(
        WS_A, runtime_root=tmp_path / "rt", settings={}, app_config=source
    )

    assert spec.app_config.search_intervals_minutes == {"shopee": 7}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_every_effective_value_reaches_the_workspace(tmp_path: Path, name: str) -> None:
    ours, theirs = built(tmp_path, name)

    assert type(ours) is AppConfig
    assert ours == theirs
    assert ours.model_dump() == theirs.model_dump()
    assert json.loads(ours.model_dump_json()) == json.loads(theirs.model_dump_json())


# --- R-05: explicitly-set information, kept apart from the values ---------------------------


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_fields_set_matches_the_legacy_model_at_every_depth(tmp_path: Path, name: str) -> None:
    ours, theirs = built(tmp_path, name)

    assert fields_set_tree(ours) == fields_set_tree(theirs)


@pytest.mark.parametrize("filter_name", sorted(FILTERS))
@pytest.mark.parametrize("name", sorted(SOURCES))
def test_dump_filters_match_the_legacy_model(tmp_path: Path, name: str, filter_name: str) -> None:
    ours, theirs = built(tmp_path, name)
    kwargs = FILTERS[filter_name]
    adapter = TypeAdapter(AppConfig)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ours.model_dump(**kwargs) == theirs.model_dump(**kwargs)
        assert json.loads(ours.model_dump_json(**kwargs)) == json.loads(
            theirs.model_dump_json(**kwargs)
        )
        assert adapter.dump_python(ours, **kwargs) == adapter.dump_python(theirs, **kwargs)
        assert json.loads(adapter.dump_json(ours, **kwargs)) == json.loads(
            adapter.dump_json(theirs, **kwargs)
        )


class Wrapper(BaseModel):
    cfg: AppConfig
    label: str = "x"


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_enclosing_models_and_copies_match_the_legacy_model(tmp_path: Path, name: str) -> None:
    ours, theirs = built(tmp_path, name)

    for kwargs in ({}, {"exclude_unset": True}, {"exclude_defaults": True}):
        assert Wrapper(cfg=ours).model_dump(**kwargs) == Wrapper(cfg=theirs).model_dump(**kwargs)
    for clone in (copy.copy(ours), copy.deepcopy(ours), ours.model_copy(deep=True)):
        assert clone == theirs
        assert fields_set_tree(clone) == fields_set_tree(theirs)
        assert clone.model_dump(exclude_unset=True) == theirs.model_dump(exclude_unset=True)


def test_exclude_unset_still_omits_a_mutated_default_like_the_legacy_model(tmp_path: Path) -> None:
    """Values and "was it set" are independent: the value is kept, the field stays unset."""

    config = build(tmp_path / "rt", app_config=model_default_intervals()).app_config

    assert config.search_intervals_minutes == {"shopee": 7}
    assert "search_intervals_minutes" not in config.model_fields_set
    assert "search_intervals_minutes" not in config.model_dump(exclude_unset=True)
    assert config.model_dump(exclude_defaults=True)["search_intervals_minutes"] == {"shopee": 7}


# --- R-05: security validation sees the complete received state -----------------------------

BROWSER_ON = ProviderConfig(
    enabled=True, affiliate_mode="browser", browser=MercadoLivreBrowserConfig(enabled=True)
)


def forbidden_in_default_map(store: str, provider: ProviderConfig) -> Callable[[], Any]:
    def make() -> AppConfig:
        source = base_model()
        source.providers[store] = provider
        return source

    return make


def forbidden_hidden_by_model_construct() -> AppConfig:
    values = dict(base_model().__dict__)
    values["providers"] = {"aliexpress": ENABLED}
    return AppConfig.model_construct(_fields_set={"templates", "affiliate_disclosure"}, **values)


def forbidden_in_a_reused_default_container() -> dict[str, Any]:
    donor = base_model()
    donor.providers["aliexpress"] = ENABLED
    return {**BASE, "providers": donor.providers}


def forbidden_unset_fields_inside_a_provider_model() -> dict[str, Any]:
    provider = ProviderConfig.model_construct(
        _fields_set=set(), enabled=True, affiliate_mode="official_api", browser=None
    )
    return {**BASE, "providers": {"aliexpress": provider}}


def forbidden_browser_hidden_in_a_provider_model() -> dict[str, Any]:
    provider = ProviderConfig.model_construct(
        _fields_set=set(),
        enabled=True,
        affiliate_mode="browser",
        browser=MercadoLivreBrowserConfig.model_construct(
            _fields_set=set(), **{**MercadoLivreBrowserConfig().__dict__, "enabled": True}
        ),
    )
    return {**BASE, "providers": {"mercadolivre": provider}}


def invalid_interval_in_default_map() -> AppConfig:
    source = base_model()
    source.search_intervals_minutes["shopee"] = 0
    return source


def invalid_rate_limit_in_default_map() -> AppConfig:
    source = base_model()
    source.provider_rate_limits_per_minute["shopee"] = -1
    return source


def unknown_store_in_default_map() -> AppConfig:
    source = base_model()
    source.providers["unknown-store"] = ProviderConfig()  # type: ignore[index]
    return source


def invalid_alias_in_default_nested_map() -> AppConfig:
    source = base_model()
    source.telegram_shadow_delivery.allowed_destinations["NOT AN ALIAS"] = destination()
    return source


def duplicate_destination_in_default_nested_map() -> AppConfig:
    source = base_model()
    source.telegram_shadow_delivery.allowed_destinations["one"] = destination()
    source.telegram_shadow_delivery.allowed_destinations["two"] = destination()
    return source


class ExtendedConfig(AppConfig):
    extra_flag: bool = False


REFUSED: dict[str, Callable[[], Any]] = {
    "enabled_provider_in_default_map": forbidden_in_default_map("aliexpress", ENABLED),
    "browser_provider_in_default_map": forbidden_in_default_map("mercadolivre", BROWSER_ON),
    "enabled_provider_hidden_by_model_construct": forbidden_hidden_by_model_construct,
    "enabled_provider_in_reused_default_container": forbidden_in_a_reused_default_container,
    "enabled_provider_in_unset_model_fields": forbidden_unset_fields_inside_a_provider_model,
    "browser_hidden_in_unset_model_fields": forbidden_browser_hidden_in_a_provider_model,
    "invalid_interval_in_default_map": invalid_interval_in_default_map,
    "invalid_rate_limit_in_default_map": invalid_rate_limit_in_default_map,
    "unknown_store_in_default_map": unknown_store_in_default_map,
    "invalid_alias_in_default_nested_map": invalid_alias_in_default_nested_map,
    "duplicate_destination_in_default_nested_map": duplicate_destination_in_default_nested_map,
    "subclass_with_an_unknown_field": lambda: ExtendedConfig(**BASE),
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_state_outside_fields_set_cannot_bypass_validation(tmp_path: Path, name: str) -> None:
    with pytest.raises(WorkspaceSpecError) as caught:
        build(tmp_path / "rt", app_config=REFUSED[name]())

    assert caught.value.code == INVALID
    assert CHAT_ID not in str(caught.value)
    assert "unknown-store" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_subclass_without_new_state_yields_the_public_schema_type(tmp_path: Path) -> None:
    class Renamed(AppConfig):
        pass

    config = build(tmp_path / "rt", app_config=Renamed(**BASE)).app_config

    assert type(config) is AppConfig
    assert config == base_model()


# --- isolation still holds with the new snapshot --------------------------------------------


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_later_changes_to_the_source_never_reach_the_snapshot(tmp_path: Path, name: str) -> None:
    source = SOURCES[name]()
    spec = build(tmp_path / "rt", app_config=source)
    before = spec.app_config
    before_sets = fields_set_tree(before)
    model = legacy_of(source)

    model.providers["aliexpress"] = ENABLED
    model.search_intervals_minutes["kabum"] = 999
    model.provider_rate_limits_per_minute.clear()
    model.telegram_shadow_delivery.allowed_destinations.clear()
    model.model_fields_set.add("providers")
    model.telegram_relay.model_fields_set.add("queue_max_size")
    if isinstance(source, dict):
        source["providers"] = {"aliexpress": ENABLED}
        source["minimum_score"] = 99

    assert spec.app_config == before
    assert fields_set_tree(spec.app_config) == before_sets
    assert "aliexpress" not in spec.app_config.providers


def test_each_access_returns_independent_values_and_fields_set(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt", app_config=model_partially_set_nested())
    first = spec.app_config
    reference = fields_set_tree(spec.app_config)

    first.providers["aliexpress"] = ENABLED
    first.model_fields_set.clear()
    first.telegram_relay.model_fields_set.add("retry_max_seconds")
    second = spec.app_config

    assert second is not first
    assert second.telegram_relay is not first.telegram_relay
    assert second.providers is not first.providers
    assert "aliexpress" not in second.providers
    assert fields_set_tree(second) == reference


def test_two_workspaces_built_from_one_source_share_nothing(tmp_path: Path) -> None:
    source = model_default_shadow_destination()
    first = build(tmp_path / "rt", WS_A, app_config=source)
    second = build(tmp_path / "rt", WS_B, app_config=source)

    leaked = first.app_config
    leaked.telegram_shadow_delivery.allowed_destinations.clear()
    leaked.providers["aliexpress"] = ENABLED

    assert second.app_config == legacy_of(model_default_shadow_destination())
    assert first.app_config == second.app_config
    assert first._app_config_snapshot is not second._app_config_snapshot


def test_the_fields_set_record_is_immutable_and_survives_copies(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt", app_config=mapping_with_models_inside_mappings())
    record = spec._app_config_fields_set
    reference = fields_set_tree(spec.app_config)

    with pytest.raises((TypeError, AttributeError)):
        record.names = frozenset()  # type: ignore[misc]
    with pytest.raises((TypeError, AttributeError)):
        record.children["providers"] = None  # type: ignore[index]
    with pytest.raises(AttributeError):
        record.names.add("providers")  # type: ignore[attr-defined]
    for clone in (copy.copy(record), copy.deepcopy(record), pickle.loads(pickle.dumps(record))):
        assert clone == record

    assert fields_set_tree(spec.app_config) == reference


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_the_trusted_snapshot_holds_every_field_not_a_filtered_dump(
    tmp_path: Path, name: str
) -> None:
    spec = build(tmp_path / "rt", app_config=SOURCES[name]())
    snapshot = spec._app_config_snapshot

    assert set(snapshot) == set(AppConfig.model_fields)
    assert set(snapshot["telegram_relay"]) == set(TelegramRelayConfig.model_fields)
    for provider in snapshot["providers"].values():
        assert set(provider) == set(ProviderConfig.model_fields)


def test_copies_of_the_spec_keep_values_and_fields_set(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt", app_config=mapping_with_models_inside_mappings())
    reference = fields_set_tree(spec.app_config)

    for clone in (copy.copy(spec), copy.deepcopy(spec), pickle.loads(pickle.dumps(spec))):
        assert clone.app_config == spec.app_config
        assert fields_set_tree(clone.app_config) == reference
    again = build(tmp_path / "rt2", WS_B, app_config=spec._app_config_snapshot).app_config
    assert again == spec.app_config


def test_round_trip_through_a_second_workspace_is_lossless(tmp_path: Path) -> None:
    for index, name in enumerate(sorted(SOURCES)):
        first = build(tmp_path / f"a{index}", WS_A, app_config=SOURCES[name]()).app_config
        second = build(tmp_path / f"b{index}", WS_B, app_config=first).app_config

        assert second == first, name
        assert fields_set_tree(second) == fields_set_tree(first), name
