"""Told to slow down (429), the agent waits as asked and keeps its pairing and its process. The
HTTP is real (loopback fake); waiting is simulated, nothing sleeps."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from promo_agent.client import ApiClient, RateLimited, retry_after_seconds
from promo_agent.credentials import load
from promo_agent.runner import Runner
from tests.conftest import TOKEN, FakeApi
from tests.test_runner import FakeSupervisor


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("7", 7.0),
        (" 7 ", 7.0),
        ("0", 1.0),
        ("999999", 3600.0),
        (None, 15.0),
        ("", 15.0),
        ("-5", 15.0),
        ("Wed, 21 Oct 2026 07:28:00 GMT", 15.0),
        ("nan", 15.0),
    ],
)
def test_retry_after_is_read_in_seconds_with_a_sane_default(
    header: str | None, expected: float
) -> None:
    assert retry_after_seconds(header) == expected


@pytest.mark.parametrize("call", ["sync", "pair"])
def test_429_says_how_long_to_wait(api: FakeApi, call: str) -> None:
    api.status, api.retry_after = 429, "7"
    client = ApiClient(api.url, TOKEN)

    with pytest.raises(RateLimited) as limited:
        client.sync("stopped") if call == "sync" else client.pair("ABCD-EFGH-JKMN")

    assert limited.value.seconds == 7.0


def test_the_agent_waits_as_asked_and_recovers_with_the_same_pairing(api: FakeApi) -> None:
    supervisor = FakeSupervisor()
    supervisor.state = "running"
    messages: list[str] = []
    runner = Runner(ApiClient(api.url, TOKEN), supervisor, log=messages.append)
    delays: list[float] = []

    def wait(delay: float) -> bool:
        delays.append(delay)
        if len(delays) == 1:
            api.status, api.retry_after = 429, "40"  # from the second call on
        elif len(delays) == 3:
            api.status = 200
            assert supervisor.stops == 0  # being limited stops nothing
        return len(delays) == 4

    assert runner.run(wait) is True  # left because asked, never because "not paired"

    assert delays == [1.0, 40.0, 40.0, 1.0]
    assert api.authorizations == [f"Bearer {TOKEN}"] * 3  # same token: two answered, goodbye
    assert [report["process_state"] for report in api.reports] == ["running", "running", "stopped"]
    assert sum("Muitas chamadas" in message for message in messages) == 1


def test_a_short_wait_never_makes_the_agent_call_more_often_than_usual() -> None:
    class Limited:
        def sync(self, *_: object, **__: object) -> object:
            raise RateLimited(1.0)

    delays: list[float] = []

    def wait(delay: float) -> bool:
        delays.append(delay)
        return True

    Runner(Limited(), FakeSupervisor(), log=lambda _: None).run(wait)  # type: ignore[arg-type]

    assert delays == [3.0]


def test_a_result_that_could_not_be_reported_is_reported_after_the_wait(api: FakeApi) -> None:
    supervisor = FakeSupervisor()
    runner = Runner(ApiClient(api.url, TOKEN), supervisor, log=lambda _: None)
    api.command = {"id": 9, "action": "start"}
    delays: list[float] = []

    def wait(delay: float) -> bool:
        delays.append(delay)
        if len(delays) == 1:  # right after running the command
            api.status, api.retry_after = 429, "5"
        else:
            api.status = 200
        return len(delays) == 3

    runner.run(wait)

    assert delays[:2] == [0.0, 5.0]
    assert api.reports[1]["completed_command_id"] == 9
    assert supervisor.starts == 1


def test_pairing_while_limited_keeps_the_code_for_later(tmp_path: Path, api: FakeApi) -> None:
    api.status, api.retry_after = 429, "12"

    result = subprocess.run(
        [sys.executable, "-m", "promo_agent.cli", "--state-dir", str(tmp_path), "pair",
         "--api", api.url],
        input="ABCD-EFGH-JKMN\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=60,
    )  # fmt: skip

    assert result.returncode == 1
    assert "12 s" in result.stdout and "continua válido" in result.stdout
    assert load(tmp_path) is None
