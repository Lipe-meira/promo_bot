"""`/readyz` is public and queries the database, so it must not be a way to keep Neon awake.

A free Neon compute suspends after five idle minutes and the plan allows 100 CU-hours a month: a
0.25 CU compute that never sleeps would use that in about 17 days. Probing the database on every
call lets any caller who knows the URL prevent the suspension. A success is therefore remembered
for a long time and a failure for a few seconds only, so that an operator who has just applied a
migration sees the 200 almost at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from promo_api.app import create_app
from promo_api.readiness import (
    FAILURE_TTL_SECONDS,
    SUCCESS_TTL_SECONDS,
    CachedReadiness,
)
from tests.conftest import UNREACHABLE_DATABASE, settings_for


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Probe:
    def __init__(self, *results: bool) -> None:
        self.results = list(results)
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        await asyncio.sleep(0)  # a real probe awaits the network: lets concurrent callers pile up
        return self.results[min(self.calls, len(self.results)) - 1]


def test_the_ttls_keep_neon_asleep_most_of_the_time() -> None:
    # A probe every SUCCESS_TTL seconds keeps the compute active for about 5 minutes of each
    # cycle: that fraction of a 0.25 CU compute over a month must stay far below 100 CU-hours.
    active_fraction = 300 / (SUCCESS_TTL_SECONDS + 300)
    assert 0.25 * 730 * active_fraction < 40
    assert FAILURE_TTL_SECONDS <= 10


async def test_a_success_is_remembered_until_the_ttl_expires() -> None:
    clock, probe = Clock(), Probe(True)
    cache = CachedReadiness(clock=clock)

    assert await cache.check(probe) is True
    clock.now += SUCCESS_TTL_SECONDS - 1
    assert await cache.check(probe) is True
    assert probe.calls == 1

    clock.now += 2
    assert await cache.check(probe) is True
    assert probe.calls == 2


async def test_a_failure_is_remembered_only_briefly() -> None:
    clock, probe = Clock(), Probe(False, True)
    cache = CachedReadiness(clock=clock)

    assert await cache.check(probe) is False
    assert await cache.check(probe) is False
    assert probe.calls == 1  # a burst of failures is one probe, not one per request

    clock.now += FAILURE_TTL_SECONDS + 0.1
    assert await cache.check(probe) is True  # recovery is seen within seconds
    assert probe.calls == 2


async def test_concurrent_callers_share_a_single_probe() -> None:
    probe = Probe(True)
    cache = CachedReadiness(clock=Clock())

    results = await asyncio.gather(*(cache.check(probe) for _ in range(50)))

    assert results == [True] * 50
    assert probe.calls == 1


async def test_an_endpoint_flood_reaches_the_database_once(
    key_source: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = Probe(True)

    async def database_is_ready(_db: Any, _heads: Any) -> bool:
        return await probe()

    monkeypatch.setattr("promo_api.app.database_is_ready", database_is_ready)
    app = create_app(settings_for(UNREACHABLE_DATABASE), key_source=key_source)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        responses = await asyncio.gather(*(client.get("/readyz") for _ in range(30)))

    assert {response.status_code for response in responses} == {200}
    assert probe.calls == 1
