"""Contract for policy fields that AppConfig accepts (T4 / F-03).

Every ``AppConfig`` field is classified in ``CONFIG_POLICY_STATUS``. The classification is checked
against the real code: reserved fields must not be read by production code (otherwise they are not
reserved), applied fields must be. Fields that look like safety filters but cannot be enforced yet
are rejected instead of silently ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from promo_bot.config import ConfigLoadError, load_app_config
from promo_bot.config.policy import (
    CONFIG_POLICY_STATUS,
    UNENFORCED_BLOCKING_FIELDS,
    PolicyStatus,
)
from promo_bot.config.schema import AppConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "promo_bot"
BASE: dict[str, object] = {
    "templates": ["{link_afiliado}"],
    "affiliate_disclosure": "fixture",
}


def _production_config_reads(field: str) -> list[str]:
    pattern = re.compile(rf"config\.{re.escape(field)}\b")
    hits: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative in {"config/schema.py", "config/policy.py"}:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            hits.append(relative)
    return hits


def _write(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({**BASE, **overrides}), encoding="utf-8")
    return path


def test_every_app_config_field_is_classified() -> None:
    assert set(CONFIG_POLICY_STATUS) == set(AppConfig.model_fields)
    assert all(isinstance(status, PolicyStatus) for status in CONFIG_POLICY_STATUS.values())


@pytest.mark.parametrize(
    "field",
    sorted(
        name
        for name, status in CONFIG_POLICY_STATUS.items()
        if status in {PolicyStatus.APPLIED, PolicyStatus.PARTIAL}
    ),
)
def test_applied_and_partial_fields_are_really_read(field: str) -> None:
    assert _production_config_reads(field), f"{field} is classified as used but nothing reads it"


@pytest.mark.parametrize(
    "field",
    sorted(
        name
        for name, status in CONFIG_POLICY_STATUS.items()
        if status in {PolicyStatus.RESERVED, PolicyStatus.REJECTED_WHEN_SET}
    ),
)
def test_reserved_fields_are_not_read_by_production_code(field: str) -> None:
    # If this fails, someone started enforcing the field: reclassify it and document it.
    assert _production_config_reads(field) == []


def test_partial_fields_are_only_the_documented_ones() -> None:
    partial = {n for n, s in CONFIG_POLICY_STATUS.items() if s is PolicyStatus.PARTIAL}
    assert partial == {"categories"}


def test_unenforced_blocking_fields_are_the_safety_looking_filters() -> None:
    assert set(UNENFORCED_BLOCKING_FIELDS) == {
        "blacklist",
        "allowed_sellers",
        "blocked_sellers",
        "maximum_price",
    }
    rejected = {n for n, s in CONFIG_POLICY_STATUS.items() if s is PolicyStatus.REJECTED_WHEN_SET}
    assert rejected == set(UNENFORCED_BLOCKING_FIELDS)


def test_defaults_are_accepted_and_policy_defaults_are_unchanged() -> None:
    config = AppConfig.model_validate(BASE)

    assert config.blacklist == ()
    assert config.allowed_sellers == ()
    assert config.blocked_sellers == ()
    assert config.maximum_price is None
    assert str(config.minimum_discount_percent) == "10"
    assert config.minimum_score == 50
    assert config.cooldown_hours == 24
    assert config.maximum_promotions_per_hour == 10


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("blacklist", ["golpe"]),
        ("blacklist", [""]),
        ("blocked_sellers", ["Loja Ruim"]),
        ("allowed_sellers", ["Loja Boa"]),
        ("maximum_price", "1999.90"),
    ],
)
def test_unenforced_blocking_fields_are_rejected_when_set(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ConfigLoadError) as captured:
        load_app_config(_write(tmp_path, **{field: value}))

    message = str(captured.value)
    assert "CONFIG_POLICY_NOT_ENFORCED" in message
    assert field in message
    assert str(value) not in message


def test_explicit_empty_values_for_blocking_fields_are_accepted(tmp_path: Path) -> None:
    config = load_app_config(
        _write(
            tmp_path,
            blacklist=[],
            allowed_sellers=[],
            blocked_sellers=[],
            maximum_price=None,
        )
    )

    assert config.maximum_price is None


def test_all_unenforced_fields_are_reported_together(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError) as captured:
        load_app_config(_write(tmp_path, blacklist=["x"], maximum_price="10"))

    message = str(captured.value)
    assert "blacklist" in message and "maximum_price" in message


def test_reserved_numeric_policy_fields_stay_accepted_and_validated(tmp_path: Path) -> None:
    config = load_app_config(
        _write(
            tmp_path,
            minimum_discount_percent="25.5",
            minimum_score=80,
            cooldown_hours=48,
            maximum_promotions_per_hour=3,
            search_intervals_minutes={"aliexpress": 120},
            provider_rate_limits_per_minute={"aliexpress": 5},
        )
    )

    assert config.minimum_score == 80
    assert config.cooldown_hours == 48
    assert config.maximum_promotions_per_hour == 3
    assert config.search_intervals_minutes == {"aliexpress": 120}
    assert config.provider_rate_limits_per_minute == {"aliexpress": 5}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_score", 101),
        ("minimum_discount_percent", "101"),
        ("cooldown_hours", 0),
        ("maximum_promotions_per_hour", 0),
        ("search_intervals_minutes", {"aliexpress": 0}),
        ("provider_rate_limits_per_minute", {"aliexpress": 0}),
        ("presentation_timezone", "Mars/Olympus"),
        ("templates", []),
    ],
)
def test_reserved_fields_still_reject_invalid_values(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ConfigLoadError):
        load_app_config(_write(tmp_path, **{field: value}))


def test_gates_are_not_loosened_by_policy_fields(tmp_path: Path) -> None:
    config = load_app_config(_write(tmp_path, providers={"aliexpress": {"enabled": False}}))

    assert config.providers["aliexpress"].enabled is False
    assert config.providers["aliexpress"].affiliate_mode == "disabled"
    with pytest.raises(ConfigLoadError):
        load_app_config(
            _write(
                tmp_path,
                providers={"aliexpress": {"enabled": False, "affiliate_mode": "official_api"}},
            )
        )
