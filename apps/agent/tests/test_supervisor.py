"""Real processes on Windows: at most one tree, stopped completely, never left behind."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Iterator

import pytest

from promo_agent.supervisor import Supervisor
from tests.winprocess import alive, wait_until

# Ignores the polite request (end of input) and starts a child of its own.
STUBBORN = textwrap.dedent(
    """
    import subprocess, sys, time
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    time.sleep(120)
    """
)


@pytest.fixture
def supervisor() -> Iterator[Supervisor]:
    instance = Supervisor()
    yield instance
    instance.stop()


def test_it_starts_and_stops_the_harmless_process(supervisor: Supervisor) -> None:
    assert supervisor.state == "stopped"

    supervisor.start()
    pids = supervisor.pids()

    assert supervisor.state == "running"
    assert pids and all(alive(pid) for pid in pids)

    supervisor.stop()

    assert supervisor.state == "stopped"
    assert wait_until(lambda: not any(alive(pid) for pid in pids))
    assert supervisor.take_unexpected_exit() is False  # we asked for it


def test_a_second_start_creates_no_second_process(supervisor: Supervisor) -> None:
    supervisor.start()
    first = supervisor.pid

    supervisor.start()
    supervisor.start()

    assert supervisor.pid == first


def test_stopping_what_is_not_running_does_nothing(supervisor: Supervisor) -> None:
    supervisor.stop()
    supervisor.stop()

    assert supervisor.state == "stopped"


def test_a_process_that_ignores_the_request_is_ended_with_its_children() -> None:
    supervisor = Supervisor([sys.executable, "-c", STUBBORN], grace_seconds=0.5)
    supervisor.start()
    assert wait_until(lambda: len(supervisor.pids()) >= 2)
    pids = supervisor.pids()

    supervisor.stop()

    assert supervisor.state == "stopped"
    assert wait_until(lambda: not any(alive(pid) for pid in pids))


def test_an_exit_we_did_not_ask_for_is_reported_once() -> None:
    supervisor = Supervisor([sys.executable, "-c", "pass"])
    supervisor.start()

    assert wait_until(lambda: supervisor.state == "stopped")
    assert supervisor.take_unexpected_exit() is True
    assert supervisor.take_unexpected_exit() is False


def test_a_program_that_cannot_start_raises_and_stays_stopped() -> None:
    supervisor = Supervisor(["C:\\does-not-exist\\nothing.exe"])

    with pytest.raises(OSError):
        supervisor.start()

    assert supervisor.state == "stopped"


def test_killing_the_supervising_process_kills_what_it_started() -> None:
    # The owner may end the agent from the Task Manager: nothing may be left running, or the
    # next start would create a second process.
    parent = textwrap.dedent(
        f"""
        import os, sys, time
        from promo_agent.supervisor import Supervisor
        supervisor = Supervisor([sys.executable, "-c", {STUBBORN!r}])
        supervisor.start()
        while len(supervisor.pids()) < 2:
            time.sleep(0.05)
        print(os.getpid(), *supervisor.pids(), flush=True)
        time.sleep(120)
        """
    )
    process = subprocess.Popen([sys.executable, "-c", parent], stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    parent_pid, *pids = (int(value) for value in process.stdout.readline().split())
    assert len(pids) >= 2 and all(alive(pid) for pid in pids)

    # Only the supervising interpreter, never its tree (`taskkill /T` would hide a leak).
    subprocess.run(["taskkill", "/F", "/PID", str(parent_pid)], capture_output=True, check=True)

    assert wait_until(lambda: not any(alive(pid) for pid in pids))
    process.stdout.close()
    process.wait(timeout=15)
