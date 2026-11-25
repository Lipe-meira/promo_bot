"""The real `promo-agent` command as its own process, against the loopback fake of the API."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from promo_agent.credentials import load
from tests.conftest import TOKEN, FakeApi
from tests.winprocess import alive, wait_until

AGENT = [sys.executable, "-m", "promo_agent.cli"]
HARMLESS = "promo_agent.harmless_process"


def run(state: Path, *args: str, code: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*AGENT, "--state-dir", str(state), *args],
        input=code,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=60,
    )


def python_pids(command_line_like: str) -> list[int]:
    """Python processes whose command line matches, asked to Windows itself."""

    query = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and "
        f"$_.CommandLine -like '{command_line_like}' }} | ForEach-Object ProcessId"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", query], capture_output=True, text=True, check=True
    ).stdout
    return [int(line) for line in out.split()]


def harmless_pids() -> list[int]:
    return python_pids(f"*-m {HARMLESS}*")


@pytest.fixture
def paired(tmp_path: Path, api: FakeApi) -> Path:
    result = run(tmp_path, "pair", "--api", api.url, code="ABCD-EFGH-JKMN\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert TOKEN not in result.stdout + result.stderr
    return tmp_path


@pytest.fixture
def start_agent() -> Iterator[Callable[[Path], subprocess.Popen[bytes]]]:
    processes: list[subprocess.Popen[bytes]] = []

    def start(state: Path) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [*AGENT, "--state-dir", str(state), "run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    yield start
    for process in processes:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
        process.wait(timeout=15)


def running(api: FakeApi) -> bool:
    return bool(api.reports) and api.reports[-1]["process_state"] == "running"


def test_pairing_with_a_wrong_code_stores_nothing(tmp_path: Path, api: FakeApi) -> None:
    result = run(tmp_path, "pair", "--api", api.url, code="ZZZZ-ZZZZ-ZZZZ\n")

    assert result.returncode == 1
    assert load(tmp_path) is None


def test_start_stop_and_a_hard_kill_leave_no_second_process(
    paired: Path, api: FakeApi, start_agent: Callable[[Path], subprocess.Popen[bytes]]
) -> None:
    before = set(harmless_pids())
    agent = start_agent(paired)
    assert wait_until(lambda: bool(api.reports))

    api.command = {"id": 1, "action": "start"}
    assert wait_until(lambda: running(api))
    first = set(harmless_pids()) - before
    assert first

    api.command = {"id": 2, "action": "start"}  # another start: nothing new may appear
    assert wait_until(lambda: api.command is None)
    assert set(harmless_pids()) - before == first

    second_agent = run(paired, "run")  # a second agent on the same PC refuses to run
    assert second_agent.returncode == 1
    assert set(harmless_pids()) - before == first

    api.command = {"id": 3, "action": "stop"}
    assert wait_until(lambda: api.reports[-1]["process_state"] == "stopped")
    assert wait_until(lambda: not any(alive(pid) for pid in first))

    api.command = {"id": 4, "action": "start"}
    assert wait_until(lambda: running(api))
    again = set(harmless_pids()) - before
    assert again

    # Ending only the agent (not its tree) must take the process with it.
    agent_pids = python_pids(f"*promo_agent.cli --state-dir *{paired.name}* run")
    assert agent_pids
    for pid in agent_pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    assert agent.wait(timeout=15) is not None
    assert wait_until(lambda: not any(alive(pid) for pid in again))


def test_status_and_unpair_work_offline(paired: Path, api: FakeApi) -> None:
    status = run(paired, "status")
    assert status.returncode == 0 and api.url in status.stdout
    assert TOKEN not in status.stdout

    assert run(paired, "unpair").returncode == 0
    assert load(paired) is None
    assert run(paired, "run").returncode == 1
