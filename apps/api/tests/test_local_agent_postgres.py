"""Pairing, commands and the observed state of the local agent, against an ephemeral PostgreSQL
built by the real migrations. The "agent" here is plain HTTP calls: no process is started.

Run explicitly: `pytest -m postgres` with `PROMO_API_TEST_DATABASE_URL`.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import TokenFactory
from tests.test_api_postgres import ALICE, BOB, auth, create_workspace, invite

pytestmark = pytest.mark.postgres


async def setup(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    who: tuple[str, str] = ALICE,
) -> tuple[dict[str, str], str]:
    await invite(engine, who[1])
    headers = auth(make_token, who)
    workspace = await create_workspace(client, headers)
    return headers, f"/v1/workspaces/{workspace['id']}/agent"


async def new_code(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> str:
    response = await client.post(f"{url}/pairing-code", headers=headers)
    assert response.status_code == 201, response.text
    code: str = response.json()["code"]
    return code


async def pair(client: httpx.AsyncClient, code: str) -> dict[str, str]:
    response = await client.post("/v1/agent/pair", json={"code": code})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['agent_token']}"}


async def sync(
    client: httpx.AsyncClient, agent: dict[str, str], state: str = "stopped", **extra: Any
) -> dict[str, Any]:
    response = await client.post(
        "/v1/agent/sync", json={"process_state": state, **extra}, headers=agent
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def command(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], action: str
) -> httpx.Response:
    return await client.post(f"{url}/commands", json={"action": action}, headers=headers)


async def view(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def connected_agent(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> dict[str, str]:
    agent = await pair(client, await new_code(client, url, headers))
    await sync(client, agent)
    return agent


async def execute(engine: AsyncEngine, statement: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(statement))


# --- pairing ------------------------------------------------------------------------------


async def test_without_a_pairing_nothing_is_connected_and_the_state_is_unknown(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)

    body = await view(client, url, headers)

    assert body == {
        "workspace_id": url.split("/")[3],
        "paired": False,
        "connected": False,
        "process_state": "unknown",
        "pending_action": None,
        "last_seen_at": None,
        "last_event": None,
    }


async def test_pairing_yields_a_token_once_and_only_hashes_are_stored(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    code = await new_code(client, url, headers)

    paired = await client.post("/v1/agent/pair", json={"code": code})
    again = await client.post("/v1/agent/pair", json={"code": code})

    assert paired.status_code == 201
    token = paired.json()["agent_token"]
    assert token.startswith("pbagent_") and len(token) > 40
    assert paired.json()["workspace_id"] == url.split("/")[3]
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "PAIRING_CODE_INVALID"
    async with engine.connect() as connection:
        stored = await connection.scalar(text("SELECT token_hash FROM agents"))
        codes = await connection.scalar(text("SELECT count(*) FROM agent_pairing_codes"))
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert codes == 0
    body = await view(client, url, headers)
    assert body["paired"] is True
    assert body["connected"] is False  # paired is not connected: the agent has not called yet
    assert body["process_state"] == "unknown"


async def test_the_code_is_accepted_in_any_case_and_without_dashes(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    code = await new_code(client, url, headers)

    response = await client.post("/v1/agent/pair", json={"code": code.replace("-", "").lower()})

    assert response.status_code == 201


async def test_an_expired_or_replaced_code_is_refused(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    replaced = await new_code(client, url, headers)
    expired = await new_code(client, url, headers)

    first = await client.post("/v1/agent/pair", json={"code": replaced})
    await execute(engine, "UPDATE agent_pairing_codes SET expires_at = now() - interval '1 second'")
    second = await client.post("/v1/agent/pair", json={"code": expired})

    assert first.status_code == second.status_code == 401
    assert first.json()["error"]["code"] == "PAIRING_CODE_INVALID"


async def test_one_code_cannot_pair_two_agents_even_concurrently(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    code = await new_code(client, url, headers)

    responses = await asyncio.gather(
        *(client.post("/v1/agent/pair", json={"code": code}) for _ in range(5))
    )

    assert sorted(r.status_code for r in responses) == [201, 401, 401, 401, 401]


async def test_pairing_again_replaces_the_previous_agent(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    old = await connected_agent(client, url, headers)

    new = await pair(client, await new_code(client, url, headers))
    refused = await client.post("/v1/agent/sync", json={"process_state": "stopped"}, headers=old)

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "AGENT_TOKEN_INVALID"
    await sync(client, new)
    assert (await view(client, url, headers))["connected"] is True


async def test_removing_the_agent_revokes_its_token_and_any_pending_code(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)
    code = await new_code(client, url, headers)

    removed = await client.delete(url, headers=headers)
    twice = await client.delete(url, headers=headers)

    assert removed.status_code == twice.status_code == 200
    assert removed.json()["paired"] is False
    refused = await client.post("/v1/agent/sync", json={"process_state": "stopped"}, headers=agent)
    assert refused.status_code == 401
    assert (await client.post("/v1/agent/pair", json={"code": code})).status_code == 401


# --- commands -----------------------------------------------------------------------------


async def test_a_start_travels_to_the_agent_and_the_state_is_what_the_agent_reports(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)

    sent = await command(client, url, headers, "start")
    delivered = await sync(client, agent)

    assert sent.status_code == 200
    assert sent.json()["pending_action"] == "start"
    assert sent.json()["process_state"] == "stopped"  # asking is not running
    assert delivered["command"]["action"] == "start"
    done = await sync(client, agent, "running", completed_command_id=delivered["command"]["id"])
    assert done["command"] is None
    body = await view(client, url, headers)
    assert (body["process_state"], body["pending_action"]) == ("running", None)


async def test_repeated_clicks_keep_one_command(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)

    clicks = await asyncio.gather(*(command(client, url, headers, "start") for _ in range(5)))
    first = await sync(client, agent)
    redelivered = await sync(client, agent)

    assert [r.status_code for r in clicks] == [200] * 5
    assert first["command"] == redelivered["command"]
    assert first["command"]["id"] == 1  # five clicks, one command

    await sync(client, agent, "running", completed_command_id=1)
    after = await command(client, url, headers, "start")  # already running: nothing to do
    assert after.json()["pending_action"] is None
    assert (await sync(client, agent, "running"))["command"] is None


async def test_a_stop_replaces_a_start_that_was_not_yet_completed(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)

    await command(client, url, headers, "start")
    started = await sync(client, agent)
    await command(client, url, headers, "stop")
    # the agent finishes the older start only now: the newer stop must survive that report
    latest = await sync(client, agent, "running", completed_command_id=started["command"]["id"])

    assert latest["command"]["action"] == "stop"
    assert latest["command"]["id"] > started["command"]["id"]


async def test_an_agent_that_stopped_calling_is_disconnected_and_takes_no_commands(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)
    await sync(client, agent, "running")
    await execute(engine, "UPDATE agents SET last_seen_at = now() - interval '1 minute'")

    body = await view(client, url, headers)
    refused = await command(client, url, headers, "stop")

    assert body["connected"] is False
    assert body["process_state"] == "unknown"  # never present an old report as the current state
    assert body["last_seen_at"] is not None
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "AGENT_OFFLINE"
    assert (await sync(client, agent, "running"))["command"] is None  # nothing was queued


async def test_a_command_not_taken_in_time_is_dropped(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)
    await command(client, url, headers, "start")
    await execute(engine, "UPDATE agents SET command_issued_at = now() - interval '5 minutes'")

    assert (await view(client, url, headers))["pending_action"] is None
    assert (await sync(client, agent))["command"] is None


async def test_an_agent_that_says_goodbye_is_disconnected_at_once(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)
    await command(client, url, headers, "start")

    goodbye = await sync(client, agent, closing=True)

    assert goodbye["command"] is None
    body = await view(client, url, headers)
    assert (body["connected"], body["pending_action"]) == (False, None)


async def test_commands_need_a_paired_agent(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)

    response = await command(client, url, headers, "start")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_NOT_PAIRED"


@pytest.mark.parametrize(
    "body",
    [
        {"action": "restart"},
        {"action": "START"},
        {"action": "start", "command_line": "calc.exe"},
        {},
    ],
)
async def test_only_start_and_stop_exist(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    body: dict[str, str],
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)

    response = await client.post(f"{url}/commands", json=body, headers=headers)

    assert response.status_code == 422
    assert (await sync(client, agent))["command"] is None


async def test_the_agent_report_cannot_carry_anything_else(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)

    response = await client.post(
        "/v1/agent/sync",
        json={"process_state": "stopped", "telegram_api_hash": "never"},
        headers=agent,
    )

    assert response.status_code == 422
    assert "never" not in response.text


# --- isolation ----------------------------------------------------------------------------


async def test_workspaces_cannot_see_or_drive_each_others_agent(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    alice, alice_url = await setup(client, engine, make_token)
    bob, bob_url = await setup(client, engine, make_token, BOB)
    agent = await connected_agent(client, alice_url, alice)

    attempts = [
        await client.get(alice_url, headers=bob),
        await client.post(f"{alice_url}/pairing-code", headers=bob),
        await command(client, alice_url, bob, "start"),
        await client.delete(alice_url, headers=bob),
    ]

    assert [r.status_code for r in attempts] == [404] * 4
    assert {r.json()["error"]["code"] for r in attempts} == {"WORKSPACE_NOT_FOUND"}
    assert (await sync(client, agent))["command"] is None
    assert (await view(client, bob_url, bob))["paired"] is False


async def test_an_agent_token_opens_no_user_route(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await connected_agent(client, url, headers)

    assert (await client.get(url, headers=agent)).status_code == 401
    assert (await client.get("/v1/me", headers=agent)).status_code == 401
    refused = await client.post(
        "/v1/agent/sync", json={"process_state": "stopped"}, headers=headers
    )
    assert refused.status_code == 401


async def test_deleting_the_workspace_removes_its_agent(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    await connected_agent(client, url, headers)

    await execute(engine, "DELETE FROM workspaces")

    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM agents")) == 0
