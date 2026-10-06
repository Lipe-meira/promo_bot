"""Closing the last accepted connection must not make a valid DB unusable."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from alembic import command

from tests.unit.test_affiliate_history_schema import config_for
from tests.unit.test_history_schema_readonly_preflight import leave_committed_wal, physical_files


def child(action, path, opt_in):
    # No inherited operator config, database override, credentials or live gates.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "COMSPEC"}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.offline_wal_lifecycle",
            action,
            str(path),
            "multi" if opt_in else "singleton",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


@pytest.fixture(autouse=True)
def no_operator_database(monkeypatch):
    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)


def close_in_wal(path):
    # No auxiliary connection. Closing this last connection owns normal cleanup.
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute("CREATE TABLE offline_normal_close (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO offline_normal_close VALUES (1)")
        conn.commit()
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()
    assert path.read_bytes()[18:20] == b"\x02\x02"


def accepted(report):
    assert report["exit_code"] == 0, report
    assert report["transports"] == 2
    assert report["external_calls"] == report["top_calls"] == report["send_calls"] == 0
    assert report["report"]["stop_reason"] == "timeout"


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("scenario", ["current-wal", "upgrade-wal", "accepted-close-restart"])
def test_real_admission_of_closed_wal_and_normal_restart(tmp_path, opt_in, scenario):
    path = tmp_path / "lifecycle.sqlite3"
    if scenario == "upgrade-wal":
        command.upgrade(config_for(path), "9b3d5e7f1a20")
        close_in_wal(path)
        assert child("init", path, opt_in)["exit_code"] == 0
    else:
        assert child("init", path, opt_in)["exit_code"] == 0
        if scenario == "current-wal":
            close_in_wal(path)
        else:
            # A finished preparer leaves committed WAL, without a live connection.
            leave_committed_wal(path)
            assert physical_files(path)[1] is not None
            accepted(child("admit", path, opt_in))
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()
    before = physical_files(path)
    report = child("admit", path, opt_in)
    if report["exit_code"] != 0:
        # RED is precisely operational refusal, not external construction or writes.
        assert report["error_code"] == "AFFILIATE_HISTORY_WAL_UNVERIFIABLE", report
        assert report["transports"] == 0
        assert physical_files(path) == before
    accepted(report)
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()
    accepted(child("admit", path, opt_in))


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("upgrade", [False, True], ids=["new", "explicit-upgrade"])
def test_documented_delete_creation_upgrade_and_restart(tmp_path, opt_in, upgrade):
    path = tmp_path / "delete.sqlite3"
    if upgrade:
        command.upgrade(config_for(path), "9b3d5e7f1a20")
    # The same init-db entrypoint as the documented wrapper, in its own process.
    assert child("init", path, opt_in)["exit_code"] == 0
    assert path.read_bytes()[18:20] == b"\x01\x01"
    accepted(child("admit", path, opt_in))
    accepted(child("admit", path, opt_in))
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()
