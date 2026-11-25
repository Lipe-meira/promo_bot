"""The agent and the API are separate programs: this keeps them agreeing on the contract that
the API publishes (`apps/api/openapi.json`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promo_agent.client import ACTIONS, PAIR_PATH, SYNC_PATH, report_body

CONTRACT = Path(__file__).resolve().parents[2] / "api" / "openapi.json"


def document() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return loaded


def test_the_routes_the_agent_calls_exist() -> None:
    paths = document()["paths"]

    assert "post" in paths[PAIR_PATH]
    assert "post" in paths[SYNC_PATH]


def test_the_report_matches_the_schema_and_the_actions_are_the_same() -> None:
    schemas = document()["components"]["schemas"]

    assert set(report_body("stopped", None, None, False)) == set(schemas["AgentSync"]["properties"])
    assert schemas["AgentCommandOut"]["properties"]["action"]["enum"] == list(ACTIONS)
