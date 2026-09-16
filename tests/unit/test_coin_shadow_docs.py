from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = PROJECT_ROOT / "docs" / "ALIEXPRESS_COIN_SHADOW.md"


def load_contract() -> dict[str, object]:
    text = DOCUMENT.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front_matter, _prose = text.removeprefix("---\n").split("\n---\n", 1)
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def test_coin_shadow_documented_commands_are_parseable_and_contract_is_fail_closed() -> None:
    from promo_bot.cli import build_parser

    contract = load_contract()
    assert contract["feature_gate"] == {
        "name": "ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED",
        "default": False,
    }
    assert contract["budgets"] == {
        "source_values": 1,
        "top_calls": 1,
        "attempts": 1,
        "retries": 0,
        "redirects_followed": 0,
    }
    assert contract["evidence_fields"] == [
        "tracking_confirmed",
        "correlation_mode",
        "attribution_unverified",
        "route_preservation_manually_observed",
    ]
    assert contract["retention_hours"] == 24
    assert contract["production_publication"] is False
    assert contract["fallback_to_canonical"] is False
    assert contract["commission_confirmed"] is False
    assert contract["secret_rotation_invalidates_cache"] is True

    parser = build_parser()
    parsed_commands = []
    for argv in contract["command_examples"]:
        assert isinstance(argv, list)
        parsed = parser.parse_args(argv)
        parsed_commands.append((parsed.command, parsed.aliexpress_command))
    assert parsed_commands == [
        ("aliexpress", "coin-shadow-preview"),
        ("aliexpress", "coin-shadow-auto-deliver"),
    ]
