from pathlib import Path

import pytest

from promo_bot.config.loader import ConfigLoadError, load_app_config
from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings


def test_shadow_delivery_gate_defaults_closed():
    assert EnvironmentSettings(_env_file=None).telegram_shadow_test_delivery_enabled is False


@pytest.mark.parametrize(
    "destinations",
    [
        'private-test: {chat_id: "-10012345", kind: private_channel}\n    '
        'private-test: {chat_id: "-10067890", kind: private_channel}',
        'first: {chat_id: "-10012345", kind: private_channel}\n    '
        'second: {chat_id: "-10012345", kind: private_channel}',
    ],
)
def test_duplicate_destination_alias_or_id_rejected(tmp_path: Path, destinations: str):
    path = tmp_path / "fixture.yaml"
    path.write_text(
        "templates: [fixture]\naffiliate_disclosure: fixture\n"
        "telegram_shadow_delivery:\n  allowed_destinations:\n    " + destinations
    )
    with pytest.raises(ConfigLoadError):
        load_app_config(path)


def test_empty_allowlist_is_fail_closed():
    config = AppConfig(templates=("fixture",), affiliate_disclosure="fixture")
    assert config.telegram_shadow_delivery.allowed_destinations == {}
