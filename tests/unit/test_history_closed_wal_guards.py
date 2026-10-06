"""Private-copy admission is bounded to closed, stable, sidecar-free fixtures."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from alembic import command

from promo_bot import cli
from promo_bot.database import history_storage
from tests.unit.test_affiliate_history_schema import config_for
from tests.unit.test_coin_listener_pilot import _argv, _config_file, _settings
from tests.unit.test_history_schema_readonly_preflight import allow_test_storage, physical_files


@pytest.fixture(autouse=True)
def no_operator_database(monkeypatch):
    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)


def closed_wal(path):
    command.upgrade(config_for(path), "head")
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def refuse_without_transports(path, tmp_path, monkeypatch, capsys, opt_in, code):
    allow_test_storage(monkeypatch, path)
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(coin_gate=True))
    constructions = []

    def forbidden(*_args, **_kwargs):
        constructions.append(True)
        pytest.fail("rejected preflight built an external transport")

    monkeypatch.setattr(cli, "build_telegram_user_client", forbidden)
    monkeypatch.setattr(cli, "build_offline_safe_http_client", forbidden)
    argv = _argv(_config_file(tmp_path), path)
    if opt_in:
        argv.append("--allow-multiple-coin-shorts")
        argv[argv.index("--max-links-per-message") + 1] = "3"
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert code in captured.err
    assert str(path) not in captured.err
    assert constructions == []


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("sidecars", ["wal-only", "shm-only", "wal-directory", "shm-directory"])
def test_incomplete_sidecars_refused_without_modification(
    tmp_path, monkeypatch, capsys, opt_in, sidecars
):
    path = tmp_path / "incomplete.sqlite3"
    closed_wal(path)
    wal, shm = Path(str(path) + "-wal"), Path(str(path) + "-shm")
    if sidecars == "wal-only":
        wal.write_bytes(b"synthetic unverified WAL")
    elif sidecars == "shm-only":
        shm.write_bytes(b"synthetic unverified SHM")
    elif sidecars == "wal-directory":
        wal.mkdir()
        shm.touch()
    else:
        wal.touch()
        shm.mkdir()
    before = physical_files(path)
    presence = wal.exists(), shm.exists()
    refuse_without_transports(
        path, tmp_path, monkeypatch, capsys, opt_in, "AFFILIATE_HISTORY_WAL_UNVERIFIABLE"
    )
    assert physical_files(path) == before
    assert (wal.exists(), shm.exists()) == presence


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("journal_content", [b"", b"synthetic recovery journal"])
def test_recovery_journal_is_never_ignored(tmp_path, monkeypatch, capsys, opt_in, journal_content):
    path = tmp_path / "recovery.sqlite3"
    closed_wal(path)
    journal = Path(str(path) + "-journal")
    journal.write_bytes(journal_content)
    before = physical_files(path)
    monkeypatch.setattr(
        history_storage.sqlite3, "connect", lambda *_a, **_k: pytest.fail("SQLite opened")
    )
    refuse_without_transports(
        path, tmp_path, monkeypatch, capsys, opt_in, "AFFILIATE_HISTORY_RECOVERY_REQUIRED"
    )
    assert physical_files(path) == before
    assert journal.read_bytes() == journal_content
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("change", ["main", "sidecars", "journal"])
def test_external_change_during_private_read_is_refused_without_bot_writes(
    tmp_path, monkeypatch, capsys, opt_in, change
):
    path = tmp_path / "changing.sqlite3"
    closed_wal(path)
    native = sqlite3.connect
    after_actor, copies = [], []

    def changed_before_query(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro&immutable=1")
        uri_path = unquote(urlparse(database_uri).path)
        copied = Path(uri_path.lstrip("/") if os.name == "nt" else uri_path)
        assert copied != path
        copies.append(copied)
        # This simulates an unsupported external actor, not a bot write/recovery.
        if change == "main":
            with path.open("r+b") as file:
                file.seek(100)
                file.write(b"external write")
        elif change == "sidecars":
            Path(str(path) + "-wal").write_bytes(b"external WAL")
            Path(str(path) + "-shm").write_bytes(b"external SHM")
        else:
            Path(str(path) + "-journal").write_bytes(b"external journal")
        after_actor.append(physical_files(path))
        return native(database_uri, **kwargs)

    monkeypatch.setattr(history_storage.sqlite3, "connect", changed_before_query)
    refuse_without_transports(
        path, tmp_path, monkeypatch, capsys, opt_in, "AFFILIATE_HISTORY_STORAGE_UNSTABLE"
    )
    assert len(copies) == 1 and not copies[0].exists() and not copies[0].parent.exists()
    assert physical_files(path) == after_actor[0]
    if change == "sidecars":
        assert Path(str(path) + "-shm").read_bytes() == b"external SHM"
    else:
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()
    if change == "journal":
        assert Path(str(path) + "-journal").read_bytes() == b"external journal"


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("kind", ["no-schema", "invalid-revision"])
def test_closed_wal_with_missing_or_invalid_schema_is_not_admitted(
    tmp_path, monkeypatch, capsys, opt_in, kind
):
    path = tmp_path / "invalid-schema.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        if kind == "invalid-revision":
            conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
            conn.execute("INSERT INTO alembic_version VALUES ('invalid')")
            for name in (
                "affiliate_link_generations",
                "affiliate_link_uses",
                "affiliate_link_use_links",
            ):
                conn.execute(f"CREATE TABLE {name} (id INTEGER)")
        conn.commit()
    before = physical_files(path)
    refuse_without_transports(
        path, tmp_path, monkeypatch, capsys, opt_in, "AFFILIATE_HISTORY_SCHEMA_REQUIRED"
    )
    assert physical_files(path) == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
def test_delete_header_with_wal_sidecars_is_ambiguous_before_sqlite_open(
    tmp_path, monkeypatch, capsys, opt_in
):
    path = tmp_path / "ambiguous.sqlite3"
    command.upgrade(config_for(path), "head")
    assert path.read_bytes()[18:20] == b"\x01\x01"
    Path(str(path) + "-wal").write_bytes(b"unverified external WAL")
    Path(str(path) + "-shm").write_bytes(b"unverified external SHM")
    before = physical_files(path)
    monkeypatch.setattr(
        history_storage.sqlite3, "connect", lambda *_a, **_k: pytest.fail("SQLite opened")
    )
    refuse_without_transports(
        path, tmp_path, monkeypatch, capsys, opt_in, "AFFILIATE_HISTORY_WAL_UNVERIFIABLE"
    )
    assert physical_files(path) == before
    assert Path(str(path) + "-shm").read_bytes() == b"unverified external SHM"
