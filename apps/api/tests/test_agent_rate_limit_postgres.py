"""The rate limit together with real pairings, against an ephemeral PostgreSQL: normal use is
never limited, and being limited costs neither the pairing nor the code.

Run explicitly: `pytest -m postgres` with `PROMO_API_TEST_DATABASE_URL`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from promo_api.app import create_app
from promo_api.ratelimit import AgentRateLimiter
from tests.conftest import FakeClerkDirectory, StaticKeys, TokenFactory, settings_for
from tests.test_agent_rate_limit import Clock
from tests.test_local_agent_postgres import command, new_code, pair, setup, sync, view

pytestmark = pytest.mark.postgres


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(
    engine: AsyncEngine,
    clean: None,
    key_source: StaticKeys,
    clerk_directory: FakeClerkDirectory,
    clock: Clock,
) -> FastAPI:
    return create_app(
        settings_for(engine.url.render_as_string(hide_password=False)),
        engine=engine,
        key_source=key_source,
        primary_email=clerk_directory,
        agent_limiter=AgentRateLimiter(clock=clock),
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http


async def report(client: httpx.AsyncClient, agent: dict[str, str]) -> httpx.Response:
    return await client.post("/v1/agent/sync", json={"process_state": "stopped"}, headers=agent)


async def flood_as_a_stranger(client: httpx.AsyncClient) -> int:
    statuses = [
        (await report(client, {"Authorization": f"Bearer pbagent_guess-{number}"})).status_code
        for number in range(30)
    ]
    return statuses.count(429)


async def test_ten_minutes_of_normal_syncing_with_commands_is_never_limited(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory, clock: Clock
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await pair(client, await new_code(client, url, headers))
    state = "stopped"

    for number in range(200):
        body = await sync(client, agent, state)  # asserts 200
        if body["command"] is not None:  # the agent reports a result at once, without waiting
            state = "running" if body["command"]["action"] == "start" else "stopped"
            await sync(client, agent, state, completed_command_id=body["command"]["id"])
        if number % 5 == 0:
            await command(client, url, headers, "start" if state == "stopped" else "stop")
        clock.now += 3

    assert (await view(client, url, headers))["connected"] is True


async def test_a_flood_of_strangers_does_not_interrupt_the_paired_agent(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await pair(client, await new_code(client, url, headers))

    refused = await flood_as_a_stranger(client)  # same address as the agent

    assert refused > 0
    await sync(client, agent)
    assert (await view(client, url, headers))["connected"] is True


async def test_too_many_calls_of_one_agent_are_refused_and_waiting_recovers_the_same_pairing(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory, clock: Clock
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await pair(client, await new_code(client, url, headers))

    burst = [(await report(client, agent)).status_code for _ in range(40)]
    limited = await report(client, agent)

    assert burst[:30] == [200] * 30
    assert limited.status_code == 429
    clock.now += int(limited.headers["retry-after"])
    await sync(client, agent)  # the same token, still paired
    assert (await view(client, url, headers))["paired"] is True


async def test_a_refused_pairing_attempt_does_not_consume_the_code(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory, clock: Clock
) -> None:
    headers, url = await setup(client, engine, make_token)
    code = await new_code(client, url, headers)
    for _ in range(5):
        await client.post("/v1/agent/pair", json={"code": "ZZZZ-ZZZZ-ZZZZ"})

    limited = await client.post("/v1/agent/pair", json={"code": code})
    assert limited.status_code == 429
    clock.now += int(limited.headers["retry-after"])

    await pair(client, code)  # asserts 201


async def test_an_agent_removed_in_the_panel_loses_its_own_budget(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await pair(client, await new_code(client, url, headers))
    await client.delete(url, headers=headers)

    statuses = [(await report(client, agent)).status_code for _ in range(15)]

    # One answer from the database says it is revoked; from then on it is a stranger.
    assert statuses[0] == 401
    assert 429 in statuses


async def test_after_a_restart_the_agent_is_recognized_again_on_its_first_report(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    key_source: StaticKeys,
    clerk_directory: FakeClerkDirectory,
    clock: Clock,
) -> None:
    headers, url = await setup(client, engine, make_token)
    agent = await pair(client, await new_code(client, url, headers))
    restarted = create_app(
        settings_for(engine.url.render_as_string(hide_password=False)),
        engine=engine,
        key_source=key_source,
        primary_email=clerk_directory,
        agent_limiter=AgentRateLimiter(clock=clock),
    )
    transport = httpx.ASGITransport(app=restarted)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        await sync(http, agent)

        assert await flood_as_a_stranger(http) > 0
        await sync(http, agent)
