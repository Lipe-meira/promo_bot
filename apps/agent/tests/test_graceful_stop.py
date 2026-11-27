"""The agent asks the process to stop (closes its input), lets it leave by itself, and forces the
end only after at least 60 seconds. Real Windows processes standing for the bot, a loopback fake
of the API, no network.

Two tests use the real clock and the real default deadline (together about 75 s); the others move
a fake clock to check the boundary exactly.
"""

from __future__ import annotations

import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from promo_agent.cli import waiter
from promo_agent.client import ApiClient, Command, SyncReply
from promo_agent.runner import Runner
from promo_agent.supervisor import STOP_GRACE_SECONDS, Supervisor
from tests.conftest import TOKEN, FakeApi
from tests.test_runner import FakeSupervisor, ScriptedClient
from tests.winprocess import alive, wait_until

# Stands for the bot: runs until its input closes. Then, as told, it either takes `seconds` and
# leaves by itself, or never leaves. It writes how long it lived after the request.
FAKE_BOT = textwrap.dedent(
    """
    import pathlib, sys, time
    folder, behaviour, seconds = pathlib.Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
    (folder / "ready").write_text("ready")
    sys.stdin.buffer.read()
    asked = time.monotonic()
    while behaviour == "ignore" or time.monotonic() - asked < seconds:
        (folder / "lived").write_text(f"{time.monotonic() - asked:.3f}")
        time.sleep(0.05)
    (folder / "left").write_text("left by itself")
    """
)


def fake_bot(folder: Path, behaviour: str, seconds: float = 0.0) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    return [sys.executable, "-c", FAKE_BOT, str(folder), behaviour, str(seconds)]


def lived(folder: Path) -> float:
    """Seconds the fake bot kept running after being asked to stop."""

    for _ in range(100):
        text = (folder / "lived").read_text() if (folder / "lived").exists() else ""
        if text:
            return float(text)
        time.sleep(0.02)
    raise AssertionError("the fake bot never saw the request")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(clock: Clock) -> Iterator[Callable[..., Supervisor]]:
    made: list[Supervisor] = []

    def build(command: list[str], **options: Any) -> Supervisor:
        supervisor = Supervisor(command, **options)
        made.append(supervisor)
        return supervisor

    yield build
    clock.now += 10_000  # whatever a failed test left running is past its deadline
    for supervisor in made:
        supervisor.stop()


def started(supervisor: Supervisor, folder: Path) -> list[int]:
    supervisor.start()
    assert wait_until(lambda: (folder / "ready").exists())
    return supervisor.pids()


# --- the deadline ----------------------------------------------------------------------------


def test_the_deadline_is_at_least_sixty_seconds_by_default() -> None:
    assert STOP_GRACE_SECONDS >= 60
    assert Supervisor()._grace == STOP_GRACE_SECONDS


def test_nothing_is_forced_before_the_deadline_and_it_is_at_the_deadline(
    tmp_path: Path, clock: Clock, make: Callable[..., Supervisor]
) -> None:
    supervisor = make(fake_bot(tmp_path, "ignore"), clock=clock)
    pids = started(supervisor, tmp_path)

    supervisor.request_stop()
    clock.now += STOP_GRACE_SECONDS - 0.001
    time.sleep(0.5)

    assert supervisor.state == "running" and supervisor.stopping
    assert supervisor.forced is False
    assert all(alive(pid) for pid in pids)

    clock.now += 0.001
    assert wait_until(lambda: supervisor.state == "stopped")

    assert supervisor.forced is True
    assert wait_until(lambda: not any(alive(pid) for pid in pids))
    assert not (tmp_path / "left").exists()
    assert supervisor.take_unexpected_exit() is False  # it was asked for


def test_asking_again_does_not_push_the_deadline_away(
    tmp_path: Path, clock: Clock, make: Callable[..., Supervisor]
) -> None:
    supervisor = make(fake_bot(tmp_path, "ignore"), clock=clock)
    started(supervisor, tmp_path)
    supervisor.request_stop()

    clock.now += STOP_GRACE_SECONDS - 1
    supervisor.request_stop()
    clock.now += 1

    assert wait_until(lambda: supervisor.state == "stopped")
    assert supervisor.forced is True


def test_no_second_process_while_the_first_is_still_leaving(
    tmp_path: Path, clock: Clock, make: Callable[..., Supervisor]
) -> None:
    supervisor = make(fake_bot(tmp_path, "leave", 1.0), clock=clock)
    started(supervisor, tmp_path)
    first = supervisor.pid
    supervisor.request_stop()

    supervisor.start()

    assert supervisor.pid == first
    assert wait_until(lambda: supervisor.state == "stopped")
    assert supervisor.forced is False


# --- both cases through the agent's own loop, real clock and real default ----------------------


def stop_through_the_agent(
    api: FakeApi, supervisor: Supervisor, folder: Path
) -> tuple[list[str], float]:
    """Panel asks start, then stop; returns the states reported while stopping and how long the
    stop took. Uses the agent's own loop and its own wait."""

    messages: list[str] = []
    runner = Runner(ApiClient(api.url, TOKEN), supervisor, log=messages.append)
    base = waiter([], supervisor)
    api.command = {"id": 1, "action": "start"}
    asked_at: float | None = None
    before = 0

    def wait(delay: float) -> bool:
        nonlocal asked_at, before
        if asked_at is None and (folder / "ready").exists() and api.command is None:
            api.command = {"id": 2, "action": "stop"}
            asked_at, before = time.monotonic(), len(api.reports)
            return False
        if asked_at is not None and api.reports[-1]["completed_command_id"] == 2:
            return True
        assert asked_at is None or time.monotonic() - asked_at < STOP_GRACE_SECONDS + 30
        return base(delay)

    try:
        assert runner.run(wait) is True
    finally:
        supervisor.stop()
    assert asked_at is not None
    during = [report["process_state"] for report in api.reports[before + 1 :]]
    return during, time.monotonic() - asked_at


def test_a_process_that_leaves_by_itself_is_never_forced(tmp_path: Path, api: FakeApi) -> None:
    # 8 s is longer than the 5 s the agent used to give.
    supervisor = Supervisor(fake_bot(tmp_path, "leave", 8.0))

    during, took = stop_through_the_agent(api, supervisor, tmp_path)

    assert (tmp_path / "left").read_text() == "left by itself"
    assert supervisor.forced is False
    assert 8 <= took < 20
    assert during.count("running") >= 3  # it kept calling: the panel does not lose the agent
    last = api.reports[-2]  # the one before the goodbye
    assert (last["process_state"], last["completed_command_id"]) == ("stopped", 2)


def test_a_process_that_does_not_leave_is_forced_only_after_sixty_seconds(
    tmp_path: Path, api: FakeApi
) -> None:
    supervisor = Supervisor(fake_bot(tmp_path, "ignore"))

    during, took = stop_through_the_agent(api, supervisor, tmp_path)

    assert not (tmp_path / "left").exists()
    assert supervisor.forced is True
    assert lived(tmp_path) >= 59.8  # measured by the process itself, from the request
    assert 60 <= took < 75
    assert during.count("running") >= 15
    assert api.reports[-1]["process_state"] == "stopped"


def test_a_long_stop_with_the_connection_lost_is_confirmed_when_it_comes_back(
    tmp_path: Path, api: FakeApi
) -> None:
    """The API keeps a pending stop for as long as it takes. The process takes 35 s to leave and
    the connection is down from 5 s to 45 s: it leaves, and the first attempt to confirm it is
    lost, while the API cannot be told."""

    attempts: list[tuple[str, int | None]] = []

    class Recording(ApiClient):
        """What the agent tried to send, whether or not it arrived."""

        def sync(
            self,
            state: str,
            completed_command_id: int | None = None,
            last_event: str | None = None,
            closing: bool = False,
        ) -> SyncReply:
            attempts.append((state, completed_command_id))
            return super().sync(state, completed_command_id, last_event, closing)

    supervisor = Supervisor(fake_bot(tmp_path, "leave", 35.0))
    runner = Runner(Recording(api.url, TOKEN), supervisor, log=lambda _: None)
    base = waiter([], supervisor)
    api.command = {"id": 1, "action": "start"}
    asked_at: float | None = None
    reports_before_the_cut = 0
    left_during_the_cut = False

    def wait(delay: float) -> bool:
        nonlocal asked_at, reports_before_the_cut, left_during_the_cut
        if asked_at is None:
            if (tmp_path / "ready").exists() and api.command is None:
                api.command = {"id": 2, "action": "stop"}
                asked_at = time.monotonic()
                return False
            return base(delay)
        if api.reports[-1]["completed_command_id"] == 2:
            return True
        # Waits in slices, as the agent does, so the connection follows the clock even while
        # the agent is backing off.
        until = time.monotonic() + delay
        while True:
            elapsed = time.monotonic() - asked_at
            assert elapsed < 90
            if 5 <= elapsed < 45:
                if api.status == 200:
                    api.status = 503
                    reports_before_the_cut = len(api.reports)
                left_during_the_cut = left_during_the_cut or (tmp_path / "left").exists()
            else:
                api.status = 200
            if time.monotonic() >= until:
                return False
            base(0.2)

    try:
        assert runner.run(wait) is True
    finally:
        supervisor.stop()

    assert supervisor.forced is False
    assert left_during_the_cut is True
    before = api.reports[:reports_before_the_cut]
    assert (before[-1]["process_state"], before[-1]["completed_command_id"]) == ("running", 1)
    back = api.reports[reports_before_the_cut]  # the first one that got through again
    assert (back["process_state"], back["completed_command_id"]) == ("stopped", 2)
    assert attempts.count(("stopped", 2)) >= 2  # lost at least once, then delivered
    assert api.command is None  # confirmed: the API drops the stop
    assert asked_at is not None and time.monotonic() - asked_at >= 45


# --- what the loop does while a stop is under way ----------------------------------------------


class SlowToLeave(FakeSupervisor):
    """A stop is only a request here: the test decides when the process really leaves."""

    def request_stop(self) -> None:
        if self.state == "running" and not self.stopping:
            self.stops += 1
            self.stopping = True

    def leave(self) -> None:
        self.state, self.stopping = "stopped", False


def test_a_stop_is_reported_done_only_when_the_process_has_left() -> None:
    client, process = ScriptedClient(), SlowToLeave()
    runner = Runner(client, process, log=lambda _: None)
    client.command = Command(1, "start")
    runner.step()
    client.command = Command(2, "stop")

    runner.step()
    runner.step()
    runner.step()

    assert process.stops == 1
    assert client.reports[-1] == ("running", 1, None, False)
    process.leave()
    runner.step()
    assert client.reports[-1] == ("stopped", 2, None, False)


def test_a_start_that_arrives_while_stopping_waits_for_the_old_process_to_leave() -> None:
    client, process = ScriptedClient(), SlowToLeave()
    runner = Runner(client, process, log=lambda _: None)
    client.command = Command(1, "start")
    runner.step()
    client.command = Command(2, "stop")
    runner.step()
    client.command = Command(3, "start")  # replaces the stop in the API

    runner.step()
    assert (process.starts, client.reports[-1][1]) == (1, 1)  # not run, not acknowledged

    process.leave()
    runner.step()
    assert (process.starts, process.state) == (2, "running")
    runner.step()
    assert client.reports[-1] == ("running", 3, None, False)


def test_leaving_the_agent_waits_for_the_process_and_keeps_reporting() -> None:
    client, process = ScriptedClient(), SlowToLeave()
    pauses: list[float] = []

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) == 40:
            process.leave()

    runner = Runner(client, process, log=lambda _: None, pause=pause)
    client.command = Command(1, "start")
    runner.step()
    client.command = None

    runner.close()

    assert process.stops == 1 and len(pauses) == 40
    states = [report[0] for report in client.reports[1:]]
    assert states.count("running") >= 2  # while waiting
    assert client.reports[-1] == ("stopped", 1, None, True)  # then goodbye


def test_the_wait_between_calls_enforces_the_deadline() -> None:
    class Watched:
        looks = 0

        @property
        def state(self) -> str:
            self.looks += 1
            return "running"

    process = Watched()

    assert waiter([], process)(0.5) is False  # type: ignore[arg-type]

    assert process.looks >= 2
