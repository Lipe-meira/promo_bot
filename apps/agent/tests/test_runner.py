"""The loop between the API and the process: each command runs once, the report is the truth."""

from __future__ import annotations

import pytest

from promo_agent.client import Command, NotPaired, SyncReply, Unreachable
from promo_agent.runner import Runner


class ScriptedClient:
    def __init__(self) -> None:
        self.command: Command | None = None
        self.reports: list[tuple[str, int | None, str | None, bool]] = []
        self.failure: Exception | None = None

    def sync(
        self,
        state: str,
        completed_command_id: int | None = None,
        last_event: str | None = None,
        closing: bool = False,
    ) -> SyncReply:
        if self.failure is not None:
            raise self.failure
        self.reports.append((state, completed_command_id, last_event, closing))
        return SyncReply(self.command, 3.0)


class FakeSupervisor:
    def __init__(self) -> None:
        self.state = "stopped"
        self.starts = 0
        self.stops = 0
        self.exited = False
        self.cannot_start = False

    def start(self) -> None:
        if self.cannot_start:
            raise OSError("synthetic")
        if self.state == "stopped":
            self.starts += 1
            self.state = "running"

    def stop(self) -> None:
        if self.state == "running":
            self.stops += 1
        self.state = "stopped"

    def take_unexpected_exit(self) -> bool:
        exited, self.exited = self.exited, False
        return exited


@pytest.fixture
def client() -> ScriptedClient:
    return ScriptedClient()


@pytest.fixture
def supervisor() -> FakeSupervisor:
    return FakeSupervisor()


@pytest.fixture
def runner(client: ScriptedClient, supervisor: FakeSupervisor) -> Runner:
    return Runner(client, supervisor, log=lambda _: None)


def test_a_start_runs_once_and_is_reported_at_once(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    client.command = Command(7, "start")

    delay = runner.step()

    assert (supervisor.starts, delay) == (1, 0.0)
    runner.step()
    assert client.reports[-1] == ("running", 7, None, False)
    assert supervisor.starts == 1


def test_a_redelivered_command_is_not_executed_again(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    client.command = Command(7, "start")
    runner.step()
    supervisor.state = "stopped"  # it ended afterwards; the same old start arrives again

    delay = runner.step()

    assert supervisor.starts == 1
    assert delay == 3.0


def test_stop_stops(runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor) -> None:
    client.command = Command(1, "start")
    runner.step()
    client.command = Command(2, "stop")

    runner.step()

    assert (supervisor.state, supervisor.stops) == ("stopped", 1)


def test_a_failed_start_is_reported_honestly(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    supervisor.cannot_start = True
    client.command = Command(1, "start")

    runner.step()
    runner.step()

    assert client.reports[-1] == ("stopped", 1, "start_failed", False)


def test_an_unexpected_exit_is_reported_and_not_restarted(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    client.command = Command(1, "start")
    runner.step()
    client.command = None
    supervisor.state, supervisor.exited = "stopped", True

    runner.step()
    runner.step()

    assert client.reports[-1] == ("stopped", 1, "exited", False)
    assert supervisor.starts == 1


def test_leaving_stops_the_process_and_says_goodbye(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    client.command = Command(1, "start")
    runner.step()

    runner.close()

    assert supervisor.state == "stopped"
    assert client.reports[-1] == ("stopped", 1, None, True)


def test_losing_contact_keeps_the_process_and_backs_off(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    client.command = Command(1, "start")
    runner.step()
    client.failure = Unreachable("synthetic")
    delays: list[float] = []

    def wait(delay: float) -> bool:
        delays.append(delay)
        return len(delays) >= 5  # then ask the loop to end

    runner.run(wait)

    assert delays == [3.0, 6.0, 12.0, 15.0, 15.0]
    assert supervisor.stops == 1  # only by leaving, at the very end


def test_a_very_long_outage_never_stops_the_agent_from_trying(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    # 5,000 failures are about 20 hours of waiting, simulated: nothing sleeps here. The delay once
    # was computed as 2**failures, which overflowed after 1,024 of them and ended the agent.
    attempts = 5_000
    client.command = Command(1, "start")
    runner.step()
    client.failure = Unreachable("synthetic")
    delays: list[float] = []

    def wait(delay: float) -> bool:
        delays.append(delay)
        if len(delays) == attempts:
            client.failure = None  # contact returns
        elif len(delays) == attempts + 1:
            assert client.reports[-1] == ("running", 1, None, False)  # kept through the outage
            client.failure = Unreachable("synthetic")  # and is lost again
        return len(delays) == attempts + 2  # then ask the loop to end

    assert runner.run(wait) is True

    assert delays[:3] == [3.0, 6.0, 12.0]
    assert set(delays[3:attempts]) == {15.0}
    assert delays[attempts] == 3.0  # the normal interval, as the API asked
    assert delays[attempts + 1] == 3.0  # a new outage starts from the shortest delay again
    assert supervisor.stops == 1


def test_a_revoked_pairing_stops_the_process_and_ends_the_loop(
    runner: Runner, client: ScriptedClient, supervisor: FakeSupervisor
) -> None:
    client.command = Command(1, "start")
    runner.step()
    client.failure = NotPaired()

    assert runner.run(lambda _: False) is False
    assert supervisor.state == "stopped"
