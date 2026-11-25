"""Rate limit of the two routes the local agent calls, without a database and without sleeping:
time is a number the test moves. Every request here is refused before any database access, either
by the limit (429) or by the format of the code or token (401)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from promo_api.app import create_app
from promo_api.ratelimit import AgentRateLimiter, TokenBuckets
from tests.conftest import UNREACHABLE_DATABASE, StaticKeys, settings_for

# Both are refused by their format alone: `0` is not in the code alphabet, and the token lacks
# the agent prefix.
BAD_CODE = "0000-0000-0000"
BAD_TOKEN = {"Authorization": "Bearer not-an-agent-token"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


# --- the buckets ---------------------------------------------------------------------------


def test_a_bucket_allows_its_burst_then_says_how_long_to_wait(clock: Clock) -> None:
    buckets = TokenBuckets(capacity=3, refill_seconds=10, clock=clock)

    assert [buckets.take("a") for _ in range(3)] == [None, None, None]
    assert buckets.take("a") == 10
    clock.now += 4
    assert buckets.take("a") == 6  # refused requests cost nothing: the wait only shrinks
    clock.now += 6
    assert buckets.take("a") is None
    assert buckets.take("a") == 10


def test_keys_do_not_share_a_bucket(clock: Clock) -> None:
    buckets = TokenBuckets(capacity=1, refill_seconds=5, clock=clock)

    assert buckets.take("a") is None
    assert buckets.take("b") is None
    assert buckets.take("a") is not None


def test_an_idle_bucket_never_holds_more_than_its_capacity(clock: Clock) -> None:
    buckets = TokenBuckets(capacity=2, refill_seconds=1, clock=clock)
    buckets.take("a")
    clock.now += 3600

    assert [buckets.take("a") for _ in range(3)] == [None, None, 1]


def test_the_number_of_remembered_keys_is_bounded(clock: Clock) -> None:
    buckets = TokenBuckets(capacity=1, refill_seconds=60, clock=clock, max_keys=100)

    for number in range(10_000):
        buckets.take(f"address-{number}")

    assert len(buckets) == 100


def test_a_known_agent_has_its_own_budget_far_above_one_call_every_three_seconds(
    clock: Clock,
) -> None:
    limiter = AgentRateLimiter(clock=clock)
    limiter.trust("digest")

    for _ in range(2_000):  # 100 minutes of normal polling
        assert limiter.check_sync("digest", "10.0.0.1") is None
        clock.now += 3


def test_strangers_cannot_use_up_a_known_agents_budget(clock: Clock) -> None:
    limiter = AgentRateLimiter(clock=clock)
    limiter.trust("digest")
    for number in range(1_000):  # many addresses, enough to exhaust the shared ceiling
        limiter.check_sync(f"stranger-{number}", f"address-{number}")

    assert limiter.check_sync("stranger", "new-address") is not None
    assert limiter.check_sync("digest", "address-0") is None


def test_one_noisy_address_does_not_use_up_the_shared_ceiling(clock: Clock) -> None:
    limiter = AgentRateLimiter(clock=clock)
    for _ in range(1_000):
        limiter.check_sync(None, "noisy")
        limiter.check_pair("noisy")

    assert limiter.check_sync(None, "quiet") is None
    assert limiter.check_pair("quiet") is None


def test_a_distrusted_token_is_a_stranger_again(clock: Clock) -> None:
    limiter = AgentRateLimiter(clock=clock)
    limiter.trust("digest")
    limiter.distrust("digest")
    for _ in range(100):
        limiter.check_sync("digest", "address")

    assert limiter.check_sync("digest", "address") is not None


# --- the routes ----------------------------------------------------------------------------


@pytest.fixture
def app(key_source: StaticKeys, clock: Clock) -> FastAPI:
    return create_app(
        settings_for(UNREACHABLE_DATABASE),
        key_source=key_source,
        agent_limiter=AgentRateLimiter(clock=clock),
    )


@pytest.fixture
async def http(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client


async def pair(http: httpx.AsyncClient, **headers: str) -> httpx.Response:
    return await http.post("/v1/agent/pair", json={"code": BAD_CODE}, headers=headers)


async def sync(http: httpx.AsyncClient, **headers: str) -> httpx.Response:
    return await http.post(
        "/v1/agent/sync", json={"process_state": "stopped"}, headers={**BAD_TOKEN, **headers}
    )


def assert_limited(response: httpx.Response) -> int:
    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert response.headers["cache-control"] == "no-store"
    wait = int(response.headers["retry-after"])
    assert wait >= 1
    return wait


@pytest.mark.parametrize(("call", "burst"), [(pair, 5), (sync, 10)], ids=["pair", "sync"])
async def test_too_many_calls_get_429_and_waiting_as_asked_recovers(
    http: httpx.AsyncClient, clock: Clock, call: Any, burst: int
) -> None:
    allowed = [(await call(http)).status_code for _ in range(burst)]

    wait = assert_limited(await call(http))
    assert_limited(await call(http))
    clock.now += wait
    recovered = await call(http)

    assert allowed == [401] * burst
    assert recovered.status_code == 401  # back to the ordinary answer


async def test_a_forwarded_address_is_ignored_unless_a_proxy_is_declared(
    http: httpx.AsyncClient,
) -> None:
    # Otherwise anyone would get a fresh budget by inventing a header.
    for number in range(5):
        await pair(http, **{"X-Forwarded-For": f"203.0.113.{number}"})

    assert_limited(await pair(http, **{"X-Forwarded-For": "203.0.113.99"}))


async def test_behind_a_declared_proxy_each_client_has_its_own_budget(
    key_source: StaticKeys, clock: Clock
) -> None:
    settings = settings_for(UNREACHABLE_DATABASE).model_copy(update={"trusted_proxy_hops": 1})
    app = create_app(settings, key_source=key_source, agent_limiter=AgentRateLimiter(clock=clock))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        # The proxy appends the address it saw: whatever the client wrote before it is not used.
        for number in range(5):
            await pair(http, **{"X-Forwarded-For": f"10.9.9.{number}, 198.51.100.7"})

        assert_limited(await pair(http, **{"X-Forwarded-For": "10.9.9.200, 198.51.100.7"}))
        assert (await pair(http, **{"X-Forwarded-For": "198.51.100.8"})).status_code == 401


async def test_the_signed_in_routes_are_not_limited_by_this(http: httpx.AsyncClient) -> None:
    for _ in range(100):
        await pair(http)

    assert (await http.get("/v1/me")).status_code == 401
    assert (await http.get("/healthz")).status_code == 200


def test_the_contract_declares_429_on_the_two_agent_routes(app: FastAPI) -> None:
    paths = app.openapi()["paths"]

    for path in ("/v1/agent/pair", "/v1/agent/sync"):
        assert "429" in paths[path]["post"]["responses"], path
