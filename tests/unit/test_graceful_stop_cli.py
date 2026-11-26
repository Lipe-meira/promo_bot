"""The real `promo-bot listen` as its own process, stopped the way a person or a supervisor would
stop it, then started again on the same database. Fake Telegram client, temporary SQLite and no
external network (see `tests/fixtures/graceful_stop_cli_harness.py`).

On Windows the console stop is Ctrl+Break; elsewhere it is SIGINT.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "fixtures" / "graceful_stop_cli_harness.py"
WINDOWS = sys.platform == "win32"


class Bot:
    def __init__(self, sandbox: Path, *options: str, **environment: str) -> None:
        config = sandbox / "config.yaml"
        example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
        config.write_text(
            example.replace("source_channels: []", 'source_channels: ["-1001234567890"]'),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "DRY_RUN": "true",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT / "src"),
            "PROMO_BOT_RUNTIME_DIR": str(sandbox / "runtime"),
            "PROMO_BOT_DATABASE_URL": f"sqlite+aiosqlite:///{(sandbox / 'db.sqlite3').as_posix()}",
            **environment,
        }
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(HARNESS), "listen", "--config", str(config), *options],
            cwd=sandbox,  # no .env of the checkout can be read from here
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
        )
        self.output: list[str] = []
        self.errors: list[str] = []
        self._lines: queue.Queue[str] = queue.Queue()
        self._readers = [
            threading.Thread(
                target=self._read, args=(self.process.stdout, self.output), daemon=True
            ),
            threading.Thread(
                target=self._read, args=(self.process.stderr, self.errors), daemon=True
            ),
        ]
        for reader in self._readers:
            reader.start()

    def _read(self, stream: Any, into: list[str]) -> None:
        for line in stream:
            into.append(line.rstrip())
            self._lines.put(line.rstrip())

    def wait_for(self, *markers: str, seconds: float = 40) -> None:
        deadline = time.monotonic() + seconds
        missing = {f"HARNESS {marker}" for marker in markers}
        while missing - set(self.output):
            assert time.monotonic() < deadline, (
                f"missing {missing}: {self.output[-5:]} {self.errors[-5:]}"
            )
            assert self.process.poll() is None, f"exited early: {self.errors[-8:]}"
            try:
                self._lines.get(timeout=0.1)
            except queue.Empty:
                pass

    def close_input(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()

    def console_stop(self) -> None:
        self.process.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)

    def finished(self, seconds: float = 20) -> int:
        code = self.process.wait(timeout=seconds)
        for reader in self._readers:
            reader.join(timeout=5)
        return code

    def summary(self) -> dict[str, Any]:
        lines = [line for line in self.output if line.startswith("{")]
        assert lines, f"no summary: {self.output} {self.errors[-8:]}"
        summary: dict[str, Any] = json.loads(lines[-1])
        return summary

    def started(self) -> list[str]:
        return [line.split()[-1] for line in self.output if line.startswith("HARNESS start ")]


@pytest.fixture
def bot(tmp_path: Path) -> Iterator[Any]:
    running: list[Bot] = []

    def launch(*options: str, **environment: str) -> Bot:
        instance = Bot(tmp_path, *options, **environment)
        running.append(instance)
        return instance

    yield launch
    for instance in running:
        if instance.process.poll() is None:
            instance.process.kill()
            instance.process.wait(timeout=10)


def messages(sandbox: Path) -> list[tuple[str, str, int, int]]:
    with sqlite3.connect(sandbox / "db.sqlite3") as connection:
        return connection.execute(
            "select message_id, processing_status, attempt_count, "
            "processing_lease_until is not null from source_messages order by id"
        ).fetchall()


def drain_after_restart(bot: Any, last: str) -> Bot:
    again: Bot = bot("--stop-on-stdin-eof", HARNESS_PROCESS_SECONDS="0")
    again.wait_for("ready", f"end {last}")
    again.close_input()
    assert again.finished() == 0
    return again


@pytest.mark.parametrize(
    ("how", "reason"), [("close_input", "input_closed"), ("console_stop", "signal")]
)
def test_an_ordered_stop_finishes_one_message_and_the_restart_does_the_others(
    bot: Any, tmp_path: Path, how: str, reason: str
) -> None:
    first = bot("--stop-on-stdin-eof", HARNESS_MESSAGES="1,2,3", HARNESS_PROCESS_SECONDS="2")
    first.wait_for("ready", "start 1")

    getattr(first, how)()

    assert first.finished() == 0, first.errors[-10:]
    assert "HARNESS end 1" in first.output and "HARNESS disconnected" in first.output
    assert first.started() == ["1"]
    assert first.summary() == {
        "status": "stopped",
        "stop_reason": reason,
        "released": 0,
        "pending": 2,
    }
    assert messages(tmp_path) == [
        ("1", "COMPLETED", 1, 0),
        ("2", "RECEIVED", 0, 0),
        ("3", "RECEIVED", 0, 0),
    ]

    again = drain_after_restart(bot, "3")

    assert again.started() == ["2", "3"]  # message 1 is not done twice
    assert [row[1] for row in messages(tmp_path)] == ["COMPLETED"] * 3


def test_work_longer_than_the_deadline_is_given_back_and_redone_at_once(
    bot: Any, tmp_path: Path
) -> None:
    first = bot(
        "--stop-on-stdin-eof",
        "--stop-grace-seconds",
        "1",
        HARNESS_MESSAGES="1",
        HARNESS_PROCESS_SECONDS="120",
    )
    first.wait_for("ready", "start 1")
    began = time.monotonic()

    first.close_input()

    assert first.finished() == 0, first.errors[-10:]
    assert time.monotonic() - began < 15
    assert (first.summary()["released"], first.summary()["pending"]) == (1, 1)
    assert messages(tmp_path) == [("1", "FAILED_RETRYABLE", 0, 0)]

    drain_after_restart(bot, "1")

    assert messages(tmp_path) == [("1", "COMPLETED", 1, 0)]


def test_without_the_option_a_closed_input_changes_nothing(bot: Any) -> None:
    instance = bot(HARNESS_PROCESS_SECONDS="0")
    instance.wait_for("ready")

    instance.close_input()
    time.sleep(2)

    assert instance.process.poll() is None
    instance.console_stop()  # the console stop is ordered even without the option
    assert instance.finished() == 0, instance.errors[-10:]
    assert instance.summary()["stop_reason"] == "signal"


def test_asking_twice_from_the_console_does_not_wait_for_the_deadline(
    bot: Any, tmp_path: Path
) -> None:
    instance = bot(
        "--stop-grace-seconds", "60", HARNESS_MESSAGES="1", HARNESS_PROCESS_SECONDS="120"
    )
    instance.wait_for("ready", "start 1")

    instance.console_stop()
    time.sleep(1)
    assert instance.process.poll() is None
    instance.console_stop()

    assert instance.finished(seconds=15) == 130
    # Forced, like a kill: the claim stays and returns when its lease expires.
    assert messages(tmp_path) == [("1", "PROCESSING", 1, 1)]


def test_the_interactive_authorization_cannot_be_combined_with_the_input_trigger(
    bot: Any,
) -> None:
    instance = bot("--authorize", "--stop-on-stdin-eof")

    assert instance.finished() == 2
    assert any("stop-on-stdin-eof" in line for line in instance.errors)
