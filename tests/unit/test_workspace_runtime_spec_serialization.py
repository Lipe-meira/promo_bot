"""S1 third re-audit: R-03 (ignored serialization filters) and R-04 (public-schema dump).

Every expectation is the behavior of the legacy `AppConfig` built from equivalent data, so the
workspace config can never drift from the public schema's serialization semantics.
"""

from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from promo_bot.config import AppConfig
from tests.unit.test_workspace_runtime_spec import WS_B, build
from tests.unit.test_workspace_runtime_spec_final import ENABLED, RAW


def legacy() -> AppConfig:
    return AppConfig.model_validate(RAW)


def workspace(tmp_path: Path) -> AppConfig:
    return build(tmp_path / "rt", app_config=RAW).app_config


FILTERS: dict[str, dict[str, Any]] = {
    "include_field": {"include": {"templates"}},
    "include_nested": {"include": {"telegram_shadow_delivery": {"allowed_destinations"}}},
    "include_provider_key": {"include": {"providers": {"shopee": {"enabled"}}}},
    "exclude_field": {"exclude": {"telegram_shadow_delivery"}},
    "exclude_nested": {"exclude": {"telegram_shadow_delivery": {"allowed_destinations"}}},
    "exclude_provider_key": {"exclude": {"providers": {"shopee"}}},
    "exclude_defaults": {"exclude_defaults": True},
    "exclude_unset": {"exclude_unset": True},
    "exclude_none": {"exclude_none": True},
    "by_alias": {"by_alias": True},
}


@pytest.mark.parametrize("name", sorted(FILTERS))
def test_model_dump_filters_match_the_legacy_schema(tmp_path: Path, name: str) -> None:
    kwargs = FILTERS[name]
    assert workspace(tmp_path).model_dump(**kwargs) == legacy().model_dump(**kwargs)


@pytest.mark.parametrize("name", sorted(FILTERS))
def test_model_dump_json_filters_match_the_legacy_schema(tmp_path: Path, name: str) -> None:
    kwargs = FILTERS[name]
    got = json.loads(workspace(tmp_path).model_dump_json(**kwargs))
    assert got == json.loads(legacy().model_dump_json(**kwargs))


def test_requested_exclusion_is_never_silently_ignored(tmp_path: Path) -> None:
    config = workspace(tmp_path)
    assert "telegram_shadow_delivery" not in config.model_dump(exclude={"telegram_shadow_delivery"})
    assert "telegram_shadow_delivery" not in json.loads(
        config.model_dump_json(exclude={"telegram_shadow_delivery"})
    )
    assert set(config.model_dump(include={"templates"})) == {"templates"}


def test_nested_model_filters_match_the_legacy_schema(tmp_path: Path) -> None:
    ours = workspace(tmp_path).telegram_shadow_delivery
    theirs = legacy().telegram_shadow_delivery
    for kwargs in ({"exclude_defaults": True}, {"include": {"allowed_destinations"}}):
        assert ours.model_dump(**kwargs) == theirs.model_dump(**kwargs)


# --- R-04: the public schema type must serialize the workspace config -------------------------


def test_type_adapter_of_the_public_schema_matches_the_legacy_dump(tmp_path: Path) -> None:
    adapter = TypeAdapter(AppConfig)
    config = workspace(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert adapter.dump_python(config) == adapter.dump_python(legacy())
        assert json.loads(adapter.dump_json(config)) == json.loads(adapter.dump_json(legacy()))
        assert adapter.dump_python(config, mode="json") == adapter.dump_python(
            legacy(), mode="json"
        )


@pytest.mark.parametrize("name", sorted(FILTERS))
def test_type_adapter_filters_match_the_legacy_schema(tmp_path: Path, name: str) -> None:
    adapter = TypeAdapter(AppConfig)
    kwargs = FILTERS[name]
    config = workspace(tmp_path)
    assert adapter.dump_python(config, **kwargs) == adapter.dump_python(legacy(), **kwargs)
    assert json.loads(adapter.dump_json(config, **kwargs)) == json.loads(
        adapter.dump_json(legacy(), **kwargs)
    )


class Wrapper(BaseModel):
    cfg: AppConfig
    label: str = "x"


def test_enclosing_model_serializes_like_with_the_legacy_config(tmp_path: Path) -> None:
    ours, theirs = Wrapper(cfg=workspace(tmp_path)), Wrapper(cfg=legacy())
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert ours.model_dump() == theirs.model_dump()
        assert json.loads(ours.model_dump_json()) == json.loads(theirs.model_dump_json())
    for kwargs in (
        {"exclude": {"cfg": {"telegram_shadow_delivery"}}},
        {"include": {"cfg": {"templates"}}},
        {"exclude": {"cfg"}},
    ):
        assert ours.model_dump(**kwargs) == theirs.model_dump(**kwargs)
        assert json.loads(ours.model_dump_json(**kwargs)) == json.loads(
            theirs.model_dump_json(**kwargs)
        )


# --- the structural requirement: no dump, copy or accessor reaches the trusted state ----------


def test_every_output_is_detached_from_the_trusted_snapshot(tmp_path: Path) -> None:
    spec = build(tmp_path / "rt", app_config=RAW)
    outputs = [
        spec.app_config.model_dump(),
        TypeAdapter(AppConfig).dump_python(spec.app_config),
        copy.deepcopy(spec.app_config).model_dump(),
    ]
    for dumped in outputs:
        dumped["providers"]["aliexpress"] = {"enabled": True}
        dumped["telegram_shadow_delivery"]["allowed_destinations"].clear()
    returned = spec.app_config
    returned.providers["aliexpress"] = ENABLED  # a plain, caller-owned dict now
    returned.providers.clear()

    assert set(spec.app_config.providers) == {"shopee"}
    assert set(spec.app_config.telegram_shadow_delivery.allowed_destinations) == {"main"}
    assert spec.app_config == legacy()


def test_returned_config_is_a_real_legacy_appconfig(tmp_path: Path) -> None:
    config = workspace(tmp_path)
    assert type(config) is AppConfig
    assert type(config.providers) is dict
    assert config == legacy()
    assert build(tmp_path / "rt2", WS_B, app_config=config).app_config == config
