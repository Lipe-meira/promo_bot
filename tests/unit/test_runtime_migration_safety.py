"""F-07: automatic runtime-database migrations must be guarded (offline, temp SQLite only)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine

from promo_bot.cli import main
from promo_bot.config import EnvironmentSettings
from promo_bot.database import migrations
from promo_bot.database import runtime_migration as rm
from promo_bot.database.migrations import _migration_config, project_root, upgrade_database
from promo_bot.database.runtime_migration import (
    RuntimeMigrationError,
    SchemaStatus,
    inspect_schema,
    runtime_listener_session,
    upgrade_runtime_database,
)

OLD_REVISION = "8ea6f1e5c7b2"
HEAD_REVISION = "b8c2e4f6a901"
EXAMPLE_CONFIG = str(Path(__file__).resolve().parents[2] / "config.example.yaml")


def url_for(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def build_db(path: Path, revision: str, *, root: Path | None = None) -> str:
    url = url_for(path)

    def run(connection: Any, config: Any) -> None:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    async def go() -> None:
        config = _migration_config(url)
        if root is not None:
            config.set_main_option("script_location", str(root / "migrations"))
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(run, config)
        finally:
            await engine.dispose()

    asyncio.run(go())
    return url


def add_marker(path: Path, value: str = "keep-me") -> None:
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE operator_marker(value TEXT)")
        con.execute("INSERT INTO operator_marker VALUES (?)", (value,))


def revision_of(path: Path) -> str | None:
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        con.close()
    return rows[0][0] if rows else None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob("*.bak"))


def leftovers(path: Path) -> list[Path]:
    """Incomplete backups. OS lock marker files are expected to persist and are not leftovers."""
    return sorted(p for p in path.parent.iterdir() if p.name.endswith(".partial"))


def prepare_runtime_database_for_startup(url: str) -> SchemaStatus:
    with runtime_listener_session(url) as session:
        return session.status


def wait_until_locks_free(path: Path, timeout: float = 10.0) -> bool:
    """The OS frees a dead owner's lock a few milliseconds after exit (measured max 6 ms)."""
    deadline = time.monotonic() + timeout
    while not locks_are_free(path):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def locks_are_free(path: Path) -> bool:
    try:
        with (
            rm._OsLock(rm._migration_lock_path(path), "X"),
            rm._OsLock(rm._listener_lock_path(path), "Y"),
        ):
            return True
    except RuntimeMigrationError:
        return False


@pytest.fixture
def old_db(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "runtime.sqlite3"
    url = build_db(path, OLD_REVISION)
    add_marker(path)
    return path, url


@pytest.fixture
def failing_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    shutil.copy(project_root() / "alembic.ini", root / "alembic.ini")
    shutil.copytree(
        project_root() / "migrations",
        root / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (root / "migrations" / "versions" / "ffff00000001_boom.py").write_text(
        "import sqlalchemy as sa\n"
        "from alembic import op\n"
        'revision = "ffff00000001"\n'
        f'down_revision = "{HEAD_REVISION}"\n'
        "def upgrade():\n"
        '    op.create_table("boom_marker", sa.Column("id", sa.Integer, primary_key=True))\n'
        '    raise RuntimeError("simulated migration failure")\n'
        "def downgrade():\n"
        "    pass\n"
    )
    monkeypatch.setattr(migrations, "project_root", lambda: root)
    return root


# --------------------------------------------------------------------- inspection


def test_inspect_missing_file_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "absent.sqlite3"

    assert inspect_schema(url_for(path)) is SchemaStatus.MISSING
    assert not path.exists()
    assert not path.parent.exists()


def test_inspect_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    path.write_bytes(b"")

    assert inspect_schema(url_for(path)) is SchemaStatus.EMPTY


def test_inspect_current_pending_and_does_not_write(tmp_path: Path) -> None:
    current = tmp_path / "current.sqlite3"
    upgrade_database(url_for(current))
    old = tmp_path / "old.sqlite3"
    build_db(old, OLD_REVISION)
    before = (digest(current), digest(old))

    assert inspect_schema(url_for(current)) is SchemaStatus.CURRENT
    assert inspect_schema(url_for(old)) is SchemaStatus.PENDING
    assert (digest(current), digest(old)) == before
    assert leftovers(old) == [] and leftovers(current) == []


def test_inspect_unknown_unversioned_and_unreadable(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(unknown) as con:
        con.execute("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)")
        con.execute("INSERT INTO alembic_version VALUES ('zzzz_not_ours')")
    unversioned = tmp_path / "unversioned.sqlite3"
    with sqlite3.connect(unversioned) as con:
        con.execute("CREATE TABLE products(id INTEGER)")
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"this is not sqlite" * 200)
    hot = tmp_path / "hot.sqlite3"
    upgrade_database(url_for(hot))
    Path(f"{hot}-journal").write_bytes(b"hot journal that must never be rolled back by us")

    assert inspect_schema(url_for(unknown)) is SchemaStatus.UNKNOWN_REVISION
    assert inspect_schema(url_for(unversioned)) is SchemaStatus.UNVERSIONED
    assert inspect_schema(url_for(garbage)) is SchemaStatus.UNREADABLE
    assert inspect_schema(url_for(hot)) is SchemaStatus.UNREADABLE


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "postgresql://x/y", "sqlite://"])
def test_inspect_rejects_unsupported_urls(url: str) -> None:
    with pytest.raises(RuntimeMigrationError) as raised:
        inspect_schema(url)
    assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"


# ------------------------------------------------------------------ startup guard


def test_startup_creates_new_database_as_before(tmp_path: Path) -> None:
    path = tmp_path / "fresh" / "runtime.sqlite3"

    assert prepare_runtime_database_for_startup(url_for(path)) is SchemaStatus.MISSING
    assert revision_of(path) == HEAD_REVISION
    assert backups(path) == []


def test_startup_on_current_database_runs_no_migration_and_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime.sqlite3"
    upgrade_database(url_for(path))
    before = digest(path)

    async def forbidden(_url: str) -> None:
        raise AssertionError("alembic must not run against an up-to-date database")

    monkeypatch.setattr(rm, "upgrade_database_async", forbidden)

    assert prepare_runtime_database_for_startup(url_for(path)) is SchemaStatus.CURRENT
    assert digest(path) == before


def test_startup_blocks_pending_migration_without_touching_database(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db

    async def forbidden(_url: str) -> None:
        raise AssertionError("startup must never migrate an existing database")

    monkeypatch.setattr(rm, "upgrade_database_async", forbidden)
    before = digest(path)

    with pytest.raises(RuntimeMigrationError) as raised:
        prepare_runtime_database_for_startup(url)

    assert raised.value.code == "RUNTIME_DB_MIGRATION_REQUIRED"
    assert str(path.parent) not in str(raised.value)
    assert digest(path) == before
    assert revision_of(path) == OLD_REVISION
    assert backups(path) == [] and leftovers(path) == []


@pytest.mark.parametrize("kind", ["unknown", "unversioned", "garbage"])
def test_startup_blocks_incompatible_databases(tmp_path: Path, kind: str) -> None:
    path = tmp_path / f"{kind}.sqlite3"
    if kind == "garbage":
        path.write_bytes(b"not sqlite" * 300)
    else:
        with sqlite3.connect(path) as con:
            if kind == "unknown":
                con.execute("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)")
                con.execute("INSERT INTO alembic_version VALUES ('zzzz_not_ours')")
            else:
                con.execute("CREATE TABLE products(id INTEGER)")
    before = digest(path)

    with pytest.raises(RuntimeMigrationError):
        prepare_runtime_database_for_startup(url_for(path))

    assert digest(path) == before


# ------------------------------------------------------------------------ CLI


@pytest.fixture(autouse=True)
def isolate_runtime_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let any test here resolve the operator's real runtime directory or database."""
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(tmp_path / "guard-runtime"))
    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)


def _settings(url: str) -> EnvironmentSettings:
    # The field is only populated through its environment alias, never by keyword.
    settings = EnvironmentSettings(_env_file=None, **{"PROMO_BOT_DATABASE_URL": url})
    assert settings.resolved_database_url == url
    return settings


@pytest.fixture
def offline_listener_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests independent of the operator's (possibly edited) example config."""
    monkeypatch.setattr(
        "promo_bot.cli.load_app_config", lambda _path: SimpleNamespace(telegram_relay=None)
    )
    monkeypatch.setattr("promo_bot.cli.DurableRelayQueue", lambda *_a, **_k: object())


def test_listen_with_pending_migration_exits_without_starting_listener(
    offline_listener_wiring: None,
    old_db: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, url = old_db
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(url))

    class ForbiddenMonitor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("listener must not start after a failed database preparation")

    monkeypatch.setattr("promo_bot.cli.TelegramMonitor", ForbiddenMonitor)
    before = digest(path)

    assert main(["listen", "--config", EXAMPLE_CONFIG]) == 2

    captured = capsys.readouterr()
    assert "RUNTIME_DB_MIGRATION_REQUIRED" in captured.err
    assert "init-db" in captured.err
    assert str(path.parent) not in captured.err + captured.out
    assert digest(path) == before


def test_listen_on_new_database_still_prepares_and_starts(
    offline_listener_wiring: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "new" / "runtime.sqlite3"
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(url_for(path)))
    started: list[bool] = []

    class FakeMonitor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, *, authorize: bool) -> SimpleNamespace:
            started.append(authorize)
            return SimpleNamespace()

    monkeypatch.setattr("promo_bot.cli.TelegramMonitor", FakeMonitor)

    assert main(["listen", "--config", EXAMPLE_CONFIG]) == 0
    assert started == [False]
    assert revision_of(path) == HEAD_REVISION


def test_init_db_on_pending_database_backs_up_then_upgrades(
    old_db: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    path, url = old_db

    assert main(["init-db", "--database-url", url]) == 0

    out = capsys.readouterr().out
    assert '"status": "upgraded"' in out
    assert '"backup_created": true' in out
    assert str(path.parent) not in out
    assert revision_of(path) == HEAD_REVISION
    (backup,) = backups(path)
    assert backup.name in out
    assert revision_of(backup) == OLD_REVISION
    for target in (path, backup):
        with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as con:
            assert con.execute("SELECT value FROM operator_marker").fetchall() == [("keep-me",)]
            assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert leftovers(path) == []


def test_init_db_on_new_and_current_databases_makes_no_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "fresh" / "runtime.sqlite3"

    assert main(["init-db", "--database-url", url_for(path)]) == 0
    assert '"backup_created": false' in capsys.readouterr().out
    before = digest(path)

    assert main(["init-db", "--database-url", url_for(path)]) == 0
    assert '"status": "current"' in capsys.readouterr().out
    assert digest(path) == before
    assert backups(path) == []


@pytest.mark.parametrize("kind", ["unknown", "unversioned", "garbage"])
def test_init_db_refuses_incompatible_databases_untouched(tmp_path: Path, kind: str) -> None:
    path = tmp_path / f"{kind}.sqlite3"
    if kind == "garbage":
        path.write_bytes(b"not sqlite" * 300)
    else:
        with sqlite3.connect(path) as con:
            if kind == "unknown":
                con.execute("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)")
                con.execute("INSERT INTO alembic_version VALUES ('zzzz_not_ours')")
            else:
                con.execute("CREATE TABLE products(id INTEGER)")
    before = digest(path)

    assert main(["init-db", "--database-url", url_for(path)]) == 2
    assert digest(path) == before
    assert backups(path) == [] and leftovers(path) == []


# ------------------------------------------------------------------------ WAL


def test_backup_of_wal_database_includes_uncheckpointed_commits(tmp_path: Path) -> None:
    path = tmp_path / "wal.sqlite3"
    build_db(path, OLD_REVISION)
    holder = sqlite3.connect(path)
    try:
        assert holder.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        holder.execute("PRAGMA wal_autocheckpoint=0")
        holder.execute("CREATE TABLE operator_marker(value TEXT)")
        holder.execute("INSERT INTO operator_marker VALUES ('only-in-wal')")
        holder.commit()
        assert Path(f"{path}-wal").stat().st_size > 0
        naive = tmp_path / "naive.copy"
        shutil.copy(path, naive)
        with sqlite3.connect(naive) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        assert "operator_marker" not in tables  # a naive file copy would lose the commit

        outcome = upgrade_runtime_database(url_for(path))
    finally:
        holder.close()

    assert outcome.status == "upgraded" and outcome.backup_name is not None
    backup = path.parent / outcome.backup_name
    with sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True) as con:
        assert con.execute("SELECT value FROM operator_marker").fetchall() == [("only-in-wal",)]
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert revision_of(backup) == OLD_REVISION
    assert revision_of(path) == HEAD_REVISION


# ------------------------------------------------- incomplete backups are never valid


def _assert_not_migrated_and_clean(path: Path, before: str) -> None:
    assert digest(path) == before
    assert revision_of(path) == OLD_REVISION
    assert backups(path) == [] and leftovers(path) == []


def test_interrupted_backup_leaves_no_valid_backup_and_blocks_migration(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)

    def interrupted(_source: Path, dest: Path) -> None:
        dest.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        raise KeyboardInterrupt

    monkeypatch.setattr(rm, "_copy_snapshot", interrupted)

    with pytest.raises(KeyboardInterrupt):
        upgrade_runtime_database(url)

    _assert_not_migrated_and_clean(path, before)


def test_corrupt_backup_fails_verification_and_blocks_migration(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)

    def truncated(source: Path, dest: Path) -> None:
        dest.write_bytes(source.read_bytes()[:2048])

    monkeypatch.setattr(rm, "_copy_snapshot", truncated)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_INVALID"
    _assert_not_migrated_and_clean(path, before)


def test_backup_with_wrong_revision_is_rejected(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, url = old_db
    before = digest(path)
    other = tmp_path / "other.sqlite3"
    upgrade_database(url_for(other))

    monkeypatch.setattr(rm, "_copy_snapshot", lambda _s, dest: shutil.copy(other, dest))

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_INVALID"
    _assert_not_migrated_and_clean(path, before)


def test_insufficient_disk_space_blocks_before_any_write(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)
    monkeypatch.setattr(
        rm.shutil, "disk_usage", lambda _p: SimpleNamespace(total=10, used=10, free=0)
    )

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_NO_SPACE"
    _assert_not_migrated_and_clean(path, before)


@pytest.mark.parametrize("error", [PermissionError("denied: secret-dir"), OSError(28, "no space")])
def test_backup_os_errors_are_sanitized_and_block_migration(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    path, url = old_db
    before = digest(path)

    def failing(_source: Path, _dest: Path) -> None:
        raise error

    monkeypatch.setattr(rm, "_copy_snapshot", failing)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_FAILED"
    assert "secret-dir" not in str(raised.value) and str(path.parent) not in str(raised.value)
    _assert_not_migrated_and_clean(path, before)


# --------------------------------------------------------- failure during migration


def test_failed_migration_keeps_verified_backup_and_reports_recovery(
    tmp_path: Path, failing_root: Path
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = build_db(path, OLD_REVISION, root=failing_root)
    add_marker(path)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_MIGRATION_FAILED"
    (backup,) = backups(path)
    assert backup.name in str(raised.value)
    assert str(path.parent) not in str(raised.value)
    assert "simulated migration failure" not in str(raised.value)
    assert revision_of(backup) == OLD_REVISION
    with sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True) as con:
        assert con.execute("SELECT value FROM operator_marker").fetchall() == [("keep-me",)]
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert locks_are_free(path)
    # The failed in-place attempt is known to leave a half-migrated file; startup must refuse it.
    with pytest.raises(RuntimeMigrationError):
        prepare_runtime_database_for_startup(url)


# ----------------------------------------------------------------------- concurrency


def test_concurrent_upgrades_leave_one_backup_and_a_current_database(
    old_db: tuple[Path, str],
) -> None:
    path, url = old_db
    barrier = threading.Barrier(2)
    results: list[object] = []

    def worker() -> None:
        barrier.wait()
        try:
            results.append(upgrade_runtime_database(url))
        except RuntimeMigrationError as exc:
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert len(results) == 2
    failures = [r for r in results if isinstance(r, RuntimeMigrationError)]
    assert all(f.code == "RUNTIME_DB_MIGRATION_IN_PROGRESS" for f in failures)
    assert len(failures) < 2
    assert inspect_schema(url) is SchemaStatus.CURRENT
    assert len(backups(path)) == 1
    assert leftovers(path) == []
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as con:
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert con.execute("SELECT value FROM operator_marker").fetchall() == [("keep-me",)]


def test_backup_taken_after_concurrent_upgrade_is_discarded(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racer that backs up an already-upgraded file must not label it as pre-upgrade."""
    path, url = old_db
    real: Callable[[Path, Path], None] = rm._copy_snapshot

    def upgrade_then_copy(source: Path, dest: Path) -> None:
        upgrade_database(url)  # someone else finishes the migration first
        real(source, dest)

    monkeypatch.setattr(rm, "_copy_snapshot", upgrade_then_copy)

    outcome = upgrade_runtime_database(url)

    assert outcome.status == "current" and outcome.backup_name is None
    assert backups(path) == [] and leftovers(path) == []
    assert revision_of(path) == HEAD_REVISION


def test_failed_creation_of_new_database_leaves_no_half_built_file(
    tmp_path: Path, failing_root: Path
) -> None:
    path = tmp_path / "fresh" / "runtime.sqlite3"

    with pytest.raises(RuntimeMigrationError) as raised:
        prepare_runtime_database_for_startup(url_for(path))

    assert raised.value.code == "RUNTIME_DB_MIGRATION_FAILED"
    assert not path.exists()
    assert leftovers(path) == []


def test_backup_with_corrupt_data_page_but_readable_revision_is_rejected(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)
    real: Callable[[Path, Path], None] = rm._copy_snapshot

    def corrupt_unrelated_page(source: Path, dest: Path) -> None:
        real(source, dest)
        with closing(sqlite3.connect(dest)) as con:
            root = con.execute(
                "SELECT rootpage FROM sqlite_master WHERE name='operator_marker'"
            ).fetchone()[0]
            page_size = con.execute("PRAGMA page_size").fetchone()[0]
        with dest.open("r+b") as handle:  # alembic_version stays readable; this page does not
            handle.seek((root - 1) * page_size)
            handle.write(b"\xff" * 16)

    monkeypatch.setattr(rm, "_copy_snapshot", corrupt_unrelated_page)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_INVALID"
    _assert_not_migrated_and_clean(path, before)


def test_backup_with_leftover_sidecar_is_not_a_single_file_and_is_rejected(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)
    real: Callable[[Path, Path], None] = rm._copy_snapshot

    def copy_with_sidecar(source: Path, dest: Path) -> None:
        real(source, dest)
        Path(f"{dest}-wal").write_bytes(b"")

    monkeypatch.setattr(rm, "_copy_snapshot", copy_with_sidecar)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_INVALID"
    _assert_not_migrated_and_clean(path, before)


def test_inspect_never_opens_a_connection_when_a_rollback_journal_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hot.sqlite3"
    upgrade_database(url_for(path))
    Path(f"{path}-journal").write_bytes(b"pending rollback data")

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("must not open a database that has a rollback journal")

    monkeypatch.setattr(rm.sqlite3, "connect", forbidden)

    assert inspect_schema(url_for(path)) is SchemaStatus.UNREADABLE


# ------------------------------------------- OS lock between independent processes (F1/F3)

HOLD_LISTENER = """
import sys
from promo_bot.database.runtime_migration import RuntimeMigrationError, runtime_listener_session
try:
    with runtime_listener_session(sys.argv[1]) as session:
        print("READY", session.status.value, flush=True)
        sys.stdin.readline()
except RuntimeMigrationError as exc:
    print("ERR", exc.code, flush=True)
"""

HOLD_RAW_LOCK = """
import sys
from pathlib import Path
from promo_bot.database import runtime_migration as rm
with rm._OsLock(Path(sys.argv[1]), "X"):
    print("READY", flush=True)
    sys.stdin.readline()
"""

INIT_DB_MID_BACKUP = """
import sys
from promo_bot.database import runtime_migration as rm
real = rm._copy_snapshot
def slow(source, dest):
    dest.write_bytes(b"not-a-valid-backup")  # an in-flight, incomplete backup
    print("READY", flush=True)
    sys.stdin.readline()
    dest.unlink()  # continue with the real copy, as a healthy init-db would
    real(source, dest)
rm._copy_snapshot = slow
print("DONE", rm.upgrade_runtime_database(sys.argv[1]).status, flush=True)
"""


@pytest.fixture
def spawn() -> Iterator[Callable[..., subprocess.Popen[str]]]:
    children: list[subprocess.Popen[str]] = []

    def start(code: str, *args: str) -> subprocess.Popen[str]:
        child = subprocess.Popen(
            [sys.executable, "-c", code, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        children.append(child)
        return child

    yield start
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=30)
        for stream in (child.stdin, child.stdout):
            if stream:
                stream.close()


def first_line(child: subprocess.Popen[str]) -> str:
    assert child.stdout is not None
    return child.stdout.readline().strip()


def release(child: subprocess.Popen[str]) -> str:
    assert child.stdin is not None and child.stdout is not None
    child.stdin.write("\n")
    child.stdin.flush()
    out = child.stdout.read().strip()
    child.wait(timeout=60)
    return out


def test_active_listener_blocks_init_db_without_touching_database(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = url_for(path)
    listener = spawn(HOLD_LISTENER, url)
    assert first_line(listener) == "READY missing"
    before = digest(path)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)
    assert raised.value.code == "RUNTIME_DB_LISTENER_ACTIVE"
    assert str(tmp_path) not in str(raised.value)
    assert main(["init-db", "--database-url", url]) == 2
    err = capsys.readouterr().err
    assert "RUNTIME_DB_LISTENER_ACTIVE" in err and str(tmp_path) not in err
    assert digest(path) == before and backups(path) == [] and leftovers(path) == []

    release(listener)
    assert upgrade_runtime_database(url).status == "current"


def test_listener_lock_blocks_upgrade_of_pending_database_before_any_write(
    old_db: tuple[Path, str], spawn: Callable[..., subprocess.Popen[str]]
) -> None:
    path, url = old_db
    holder = spawn(HOLD_RAW_LOCK, str(rm._listener_lock_path(path)))
    assert first_line(holder) == "READY"
    before = digest(path)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_LISTENER_ACTIVE"
    assert digest(path) == before and backups(path) == [] and leftovers(path) == []
    assert revision_of(path) == OLD_REVISION
    # The failed attempt must not leave the migration lock held.
    release(holder)
    assert upgrade_runtime_database(url).status == "upgraded"


def test_running_init_db_blocks_listener_startup(
    offline_listener_wiring: None,
    old_db: tuple[Path, str],
    spawn: Callable[..., subprocess.Popen[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, url = old_db
    init_db = spawn(INIT_DB_MID_BACKUP, url)
    assert first_line(init_db) == "READY"  # holds both locks, in the middle of the backup
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(url))

    class ForbiddenMonitor:
        def __init__(self, *_a: object, **_k: object) -> None:
            raise AssertionError("listener must not start while init-db is migrating")

    monkeypatch.setattr("promo_bot.cli.TelegramMonitor", ForbiddenMonitor)

    with pytest.raises(RuntimeMigrationError) as raised:
        prepare_runtime_database_for_startup(url)
    assert raised.value.code == "RUNTIME_DB_MIGRATION_IN_PROGRESS"
    assert main(["listen", "--config", EXAMPLE_CONFIG]) == 2
    err = capsys.readouterr().err
    assert "RUNTIME_DB_MIGRATION_IN_PROGRESS" in err and str(path.parent) not in err
    assert revision_of(path) == OLD_REVISION

    assert release(init_db) == "DONE upgraded"
    assert prepare_runtime_database_for_startup(url) is SchemaStatus.CURRENT


def test_second_listener_cannot_start_on_the_same_database(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = url_for(path)
    first = spawn(HOLD_LISTENER, url)
    assert first_line(first) == "READY missing"

    second = spawn(HOLD_LISTENER, url)
    assert first_line(second) == "ERR RUNTIME_DB_LISTENER_ACTIVE"
    with pytest.raises(RuntimeMigrationError) as raised:
        prepare_runtime_database_for_startup(url)
    assert raised.value.code == "RUNTIME_DB_LISTENER_ACTIVE"

    release(first)
    assert prepare_runtime_database_for_startup(url) is SchemaStatus.CURRENT
    assert revision_of(path) == HEAD_REVISION


def test_simultaneous_listener_processes_on_a_new_database_start_exactly_one(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = url_for(path)
    children = [spawn(HOLD_LISTENER, url) for _ in range(3)]

    lines = sorted(first_line(child) for child in children)

    ready = [line for line in lines if line.startswith("READY")]
    refused = [line for line in lines if line.startswith("ERR")]
    assert len(ready) == 1 and len(refused) == 2
    assert all(
        line in {"ERR RUNTIME_DB_LISTENER_ACTIVE", "ERR RUNTIME_DB_MIGRATION_IN_PROGRESS"}
        for line in refused
    )
    assert revision_of(path) == HEAD_REVISION
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as con:
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_forced_kill_of_listener_releases_the_lock_immediately(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = url_for(path)
    listener = spawn(HOLD_LISTENER, url)
    assert first_line(listener) == "READY missing"
    with pytest.raises(RuntimeMigrationError):
        upgrade_runtime_database(url)

    listener.kill()  # TerminateProcess / SIGKILL: no cleanup code runs
    listener.wait(timeout=30)

    assert wait_until_locks_free(path)  # released by the OS; no orphan
    assert upgrade_runtime_database(url).status == "current"
    assert prepare_runtime_database_for_startup(url) is SchemaStatus.CURRENT


def test_forced_kill_of_init_db_mid_backup_releases_locks_and_ignores_stale_partial(
    old_db: tuple[Path, str], spawn: Callable[..., subprocess.Popen[str]]
) -> None:
    path, url = old_db
    init_db = spawn(INIT_DB_MID_BACKUP, url)
    assert first_line(init_db) == "READY"
    init_db.kill()
    init_db.wait(timeout=30)

    stale = leftovers(path)
    assert len(stale) == 1 and stale[0].read_bytes() == b"not-a-valid-backup"
    assert backups(path) == [] and revision_of(path) == OLD_REVISION
    assert wait_until_locks_free(path)

    outcome = upgrade_runtime_database(url)

    assert outcome.status == "upgraded" and outcome.backup_name is not None
    (backup,) = backups(path)  # the stale partial was never promoted to a valid backup
    assert backup.name == outcome.backup_name and revision_of(backup) == OLD_REVISION
    assert stale[0].read_bytes() == b"not-a-valid-backup"


def test_listener_session_blocks_init_db_in_process_and_releases_on_exit_or_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = url_for(path)

    with runtime_listener_session(url):
        with pytest.raises(RuntimeMigrationError) as raised:
            upgrade_runtime_database(url)
        assert raised.value.code == "RUNTIME_DB_LISTENER_ACTIVE"  # migration lock was released
        with pytest.raises(RuntimeMigrationError) as again:
            prepare_runtime_database_for_startup(url)
        assert again.value.code == "RUNTIME_DB_LISTENER_ACTIVE"
    assert locks_are_free(path)

    with pytest.raises(ZeroDivisionError):
        with runtime_listener_session(url):
            raise ZeroDivisionError
    assert locks_are_free(path)
    assert upgrade_runtime_database(url).status == "current"


def test_listen_command_holds_the_lock_for_the_whole_life_of_the_monitor(
    offline_listener_wiring: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "new" / "runtime.sqlite3"
    url = url_for(path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(url))
    seen: list[str] = []

    class ObservingMonitor:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def run(self, *, authorize: bool) -> SimpleNamespace:
            try:
                upgrade_runtime_database(url)
                seen.append("migrated-while-listening")
            except RuntimeMigrationError as exc:
                seen.append(exc.code)
            return SimpleNamespace()

    monkeypatch.setattr("promo_bot.cli.TelegramMonitor", ObservingMonitor)

    assert main(["listen", "--config", EXAMPLE_CONFIG]) == 0

    assert seen == ["RUNTIME_DB_LISTENER_ACTIVE"]
    assert locks_are_free(path)  # released when the monitor ends
    assert upgrade_runtime_database(url).status == "current"


def test_lock_file_errors_are_sanitized_and_leave_the_database_untouched(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)
    real_open = Path.open

    def deny(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name.endswith(".lock"):
            raise PermissionError(13, "Permission denied", "SECRETPATH-" + str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)

    for call in (upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call(url)
        assert raised.value.code == "RUNTIME_DB_LOCK_FAILED"
        assert "SECRETPATH" not in str(raised.value) and str(path.parent) not in str(raised.value)
    assert digest(path) == before and backups(path) == []


# ----------------------------------------------------------------- F4: sanitized errors


def test_sqlite_error_after_preflight_is_a_controlled_error_not_a_traceback(
    old_db: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, url = old_db
    before = digest(path)
    real = rm.inspect_schema
    holders: list[sqlite3.Connection] = []

    def inspect_then_lock_out(database_url: str) -> SchemaStatus:
        status = real(database_url)
        if status is SchemaStatus.PENDING and not holders:
            writer = sqlite3.connect(path, timeout=0)
            writer.execute(
                "BEGIN EXCLUSIVE"
            )  # a non-cooperating writer appears after the preflight
            holders.append(writer)
        return status

    monkeypatch.setattr(rm, "inspect_schema", inspect_then_lock_out)
    monkeypatch.setattr(rm, "_READ_TIMEOUT_SECONDS", 0.2)
    try:
        assert main(["init-db", "--database-url", url]) == 2  # an uncaught error would raise here
    finally:
        for writer in holders:
            writer.rollback()
            writer.close()

    err = capsys.readouterr().err
    assert json.loads(err.strip().splitlines()[-1])["message"].startswith("RUNTIME_DB_UNREADABLE")
    assert "Traceback" not in err and str(path.parent) not in err
    assert digest(path) == before and backups(path) == [] and leftovers(path) == []
    assert locks_are_free(path)


def test_os_error_while_sizing_the_backup_is_sanitized(
    old_db: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, url = old_db
    before = digest(path)
    real_stat = Path.stat

    def deny(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == path.name and sys._getframe(1).f_code.co_name == "_make_backup":
            raise PermissionError(13, "Permission denied", "SECRETPATH-" + str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny)

    assert main(["init-db", "--database-url", url]) == 2

    err = capsys.readouterr().err
    assert "RUNTIME_DB_BACKUP_FAILED" in err
    assert "SECRETPATH" not in err and str(path.parent) not in err
    assert digest(path) == before and backups(path) == [] and leftovers(path) == []


def test_os_error_while_promoting_the_backup_is_sanitized_and_cleans_up(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, url = old_db
    before = digest(path)
    real_replace = Path.replace

    def deny(self: Path, target: Any) -> Any:
        if self.name.endswith(".partial"):
            raise PermissionError(13, "Permission denied", "SECRETPATH-" + str(self))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", deny)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_BACKUP_FAILED"
    assert "SECRETPATH" not in str(raised.value) and str(path.parent) not in str(raised.value)
    assert digest(path) == before and backups(path) == [] and leftovers(path) == []
    assert revision_of(path) == OLD_REVISION


@pytest.mark.parametrize(
    "error", [OSError(5, "io", "SECRETPATH"), sqlite3.DatabaseError("SECRETPATH detail")]
)
def test_unforeseen_storage_errors_never_escape_unsanitized(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    path, url = old_db

    def explode(*_a: object, **_k: object) -> Path:
        raise error

    monkeypatch.setattr(rm, "_make_backup", explode)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_STORAGE_ERROR"
    assert "SECRETPATH" not in str(raised.value)
    assert locks_are_free(path)


# ======================================================= second audit: H-02, H-03, M-01..M-03, L-01


def _link_or_skip(source: Path, alias: Path) -> None:
    try:
        os.link(source, alias)
    except OSError:  # pragma: no cover - filesystem without hard links
        pytest.skip("hard links are not supported on this filesystem")


# ---- H-02: one identity for inspection, lock, backup, migration and the operational connection


@pytest.mark.parametrize("variant", ["tilde", "dot-segments", "relative"])
def test_every_url_spelling_protects_backs_up_and_migrates_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    """Two synthetic databases: the protected one must be the one that is migrated."""
    cwd = tmp_path / "cwd"
    (cwd / "~").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    if variant == "tilde":
        protected = Path.home() / "rt.sqlite3"  # sandbox home: what expanduser() resolves to
        decoy = cwd / "~" / "rt.sqlite3"  # what a literal relative '~/rt.sqlite3' opens
        url = "sqlite+aiosqlite:///~/rt.sqlite3"
    elif variant == "dot-segments":
        protected = cwd / "rt.sqlite3"
        decoy = cwd / "~" / "rt.sqlite3"
        url = "sqlite+aiosqlite:///./sub/../rt.sqlite3"
    else:
        protected = cwd / "rt.sqlite3"
        decoy = cwd / "~" / "rt.sqlite3"
        url = "sqlite+aiosqlite:///rt.sqlite3"
    protected.parent.mkdir(parents=True, exist_ok=True)
    build_db(protected, OLD_REVISION)
    add_marker(protected, "protected-database")
    build_db(decoy, OLD_REVISION)
    add_marker(decoy, "unprotected-database")
    decoy_before = digest(decoy)

    outcome = upgrade_runtime_database(url)

    assert outcome.status == "upgraded"
    assert revision_of(protected) == HEAD_REVISION
    (backup,) = backups(protected)
    assert revision_of(backup) == OLD_REVISION
    with closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as con:
        assert con.execute("SELECT value FROM operator_marker").fetchall() == [
            ("protected-database",)
        ]
    # The file that was NOT protected stays byte-for-byte intact and gets no backup.
    assert digest(decoy) == decoy_before and revision_of(decoy) == OLD_REVISION
    assert backups(decoy) == [] and leftovers(decoy) == []


def test_listener_connects_to_the_very_database_it_protected(
    offline_listener_wiring: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    (cwd / "~").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    decoy = cwd / "~" / "rt.sqlite3"
    build_db(decoy, OLD_REVISION)
    decoy_before = digest(decoy)
    monkeypatch.setattr(
        "promo_bot.cli.load_settings", lambda: _settings("sqlite+aiosqlite:///~/rt.sqlite3")
    )
    connected: list[Path] = []

    class Capture:
        def __init__(self, database: Any, *_a: object) -> None:
            connected.append(Path(str(database.engine.url.database)).resolve())

    monkeypatch.setattr("promo_bot.cli.DurableRelayQueue", Capture)

    class FakeMonitor:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def run(self, *, authorize: bool) -> SimpleNamespace:
            return SimpleNamespace()

    monkeypatch.setattr("promo_bot.cli.TelegramMonitor", FakeMonitor)

    assert main(["listen", "--config", EXAMPLE_CONFIG]) == 0

    protected = (Path.home() / "rt.sqlite3").resolve()
    assert connected == [protected]  # operational connection == inspected/locked/created file
    assert revision_of(protected) == HEAD_REVISION
    assert digest(decoy) == decoy_before and revision_of(decoy) == OLD_REVISION


def test_identity_helpers_agree_for_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "x" / "rt.sqlite3"
    url = f"sqlite+aiosqlite:///{(tmp_path / 'x' / '..' / 'x' / 'rt.sqlite3').as_posix()}"

    canonical = rm._canonical_url(rm._database_path(url))

    assert rm._database_path(canonical) == path.resolve()
    assert rm._database_path(url) == path.resolve()


def test_canonical_url_refuses_a_path_that_would_not_round_trip(tmp_path: Path) -> None:
    """A '?' would turn the tail of the path into a URL query: another file, so refuse."""
    path = tmp_path / "a?b" / "rt.sqlite3"

    with pytest.raises(RuntimeMigrationError) as raised:
        rm._canonical_url(path)

    assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"


# ---- H-03: hard-link aliases must not bypass the locks


@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_hard_link_alias_cannot_bypass_an_active_listener_lock(
    tmp_path: Path,
    spawn: Callable[..., subprocess.Popen[str]],
    journal_mode: str,
) -> None:
    primary = tmp_path / "primary.sqlite3"
    build_db(primary, OLD_REVISION)
    add_marker(primary)
    if journal_mode == "wal":
        with closing(sqlite3.connect(primary)) as con:
            assert con.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    alias = tmp_path / "alias.sqlite3"
    _link_or_skip(primary, alias)
    holder = spawn(HOLD_RAW_LOCK, str(rm._listener_lock_path(primary.resolve())))
    assert first_line(holder) == "READY"  # a listener owns `primary`
    before = digest(primary)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url_for(alias))  # same inode, different lock-file names

    assert raised.value.code == "RUNTIME_DB_ALIAS_UNSUPPORTED"
    assert str(tmp_path) not in str(raised.value)
    assert digest(primary) == before and revision_of(primary) == OLD_REVISION
    assert backups(primary) == [] and backups(alias) == [] and leftovers(primary) == []
    assert locks_are_free(alias)
    release(holder)


def test_hard_linked_database_is_refused_by_listen_and_init_db_without_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    primary = tmp_path / "primary.sqlite3"
    upgrade_database(url_for(primary))
    alias = tmp_path / "alias.sqlite3"
    _link_or_skip(primary, alias)
    before = digest(primary)

    for call in (upgrade_runtime_database, prepare_runtime_database_for_startup):
        for target in (primary, alias):  # the link count is a property of the file, not the name
            with pytest.raises(RuntimeMigrationError) as raised:
                call(url_for(target))
            assert raised.value.code == "RUNTIME_DB_ALIAS_UNSUPPORTED"
    assert main(["init-db", "--database-url", url_for(alias)]) == 2
    err = capsys.readouterr().err
    assert "RUNTIME_DB_ALIAS_UNSUPPORTED" in err and str(tmp_path) not in err
    assert digest(primary) == before and locks_are_free(primary) and locks_are_free(alias)

    alias.unlink()  # once the alias is gone the database is usable again
    assert upgrade_runtime_database(url_for(primary)).status == "current"


def test_alias_created_while_upgrading_is_caught_before_the_migration(
    old_db: tuple[Path, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, url = old_db
    real: Callable[[Path, Path], None] = rm._copy_snapshot

    def copy_then_alias(source: Path, dest: Path) -> None:
        real(source, dest)
        _link_or_skip(path, tmp_path / "late-alias.sqlite3")

    monkeypatch.setattr(rm, "_copy_snapshot", copy_then_alias)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_ALIAS_UNSUPPORTED"
    assert revision_of(path) == OLD_REVISION  # the migration never ran
    (backup,) = backups(path)  # the verified backup is kept
    assert revision_of(backup) == OLD_REVISION
    assert locks_are_free(path)


# ---- M-01: orphan sidecars are evidence, never silently adopted or deleted


@pytest.mark.parametrize("suffix", ["-wal", "-journal", "-shm"])
def test_orphan_sidecar_without_main_database_is_preserved_and_blocks_everything(
    tmp_path: Path, suffix: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    orphan = Path(f"{path}{suffix}")
    orphan.write_bytes(b"committed frames of a database that was moved away" * 40)
    before = digest(orphan)

    assert inspect_schema(url_for(path)) is SchemaStatus.ORPHAN_SIDECARS
    for call in (upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call(url_for(path))
        assert raised.value.code == "RUNTIME_DB_ORPHAN_SIDECARS"
        assert str(tmp_path) not in str(raised.value)
    assert main(["init-db", "--database-url", url_for(path)]) == 2
    assert "RUNTIME_DB_ORPHAN_SIDECARS" in capsys.readouterr().err

    assert not path.exists()  # no new database was created over the orphan
    assert orphan.exists() and digest(orphan) == before  # and the evidence is untouched
    assert locks_are_free(path)


# ---- M-02: the final lock release is sanitized and never masks the real outcome


def _failing_close(monkeypatch: pytest.MonkeyPatch, only_suffix: str | None, path: Path) -> None:
    real_close = rm._close_lock_handle

    def close_then_fail(handle: Any) -> None:
        real_close(handle)
        if only_suffix is None or str(handle.name).endswith(only_suffix):
            raise OSError(5, "I/O error", "SECRETPATH-" + str(path))

    monkeypatch.setattr(rm, "_close_lock_handle", close_then_fail)


def test_lock_release_failure_is_sanitized_and_still_releases_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime.sqlite3"
    url = url_for(path)
    _failing_close(monkeypatch, ".runtime-listener.lock", path)

    with pytest.raises(RuntimeMigrationError) as raised:  # normal end of the monitor
        with runtime_listener_session(url):
            pass
    assert raised.value.code == "RUNTIME_DB_LOCK_RELEASE_FAILED"
    assert "SECRETPATH" not in str(raised.value) and str(tmp_path) not in str(raised.value)

    with pytest.raises(ZeroDivisionError):  # the monitor's own failure is never masked
        with runtime_listener_session(url):
            raise ZeroDivisionError

    with pytest.raises(RuntimeMigrationError) as upgraded:  # init-db finished, release failed
        upgrade_runtime_database(url)
    assert upgraded.value.code == "RUNTIME_DB_LOCK_RELEASE_FAILED"
    assert "SECRETPATH" not in str(upgraded.value)

    monkeypatch.undo()
    assert locks_are_free(path)  # the handles really were closed


def test_start_up_release_failure_of_the_migration_lock_does_not_leave_the_listener_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime.sqlite3"
    _failing_close(monkeypatch, ".migrate.lock", path)

    with pytest.raises(RuntimeMigrationError) as raised:
        with runtime_listener_session(url_for(path)):
            raise AssertionError("the monitor must not start after a failed start-up")

    assert raised.value.code == "RUNTIME_DB_LOCK_RELEASE_FAILED"
    assert "SECRETPATH" not in str(raised.value)
    monkeypatch.undo()
    assert locks_are_free(path)


def test_lock_release_failure_exits_the_cli_with_a_controlled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    real_close = rm._close_lock_handle

    def close_then_fail(handle: Any) -> None:
        real_close(handle)
        raise OSError(5, "I/O error", "SECRETPATH-" + str(path))

    monkeypatch.setattr(rm, "_close_lock_handle", close_then_fail)

    assert main(["init-db", "--database-url", url_for(path)]) == 2

    err = capsys.readouterr().err
    assert "RUNTIME_DB_LOCK_RELEASE_FAILED" in err
    assert "SECRETPATH" not in err and "Traceback" not in err and str(tmp_path) not in err


# ---- M-03: what CURRENT does and does not prove about physical integrity


def _corrupt_marker_page(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        root = con.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='operator_marker'"
        ).fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
    with path.open("r+b") as handle:  # alembic_version stays readable; this page does not
        handle.seek((root - 1) * page_size)
        handle.write(b"\xff" * 16)


def test_current_status_is_not_a_physical_integrity_check_documented_limit(
    tmp_path: Path,
) -> None:
    """CURRENT means only: readable header, an `alembic_version` row equal to head."""
    path = tmp_path / "runtime.sqlite3"
    upgrade_database(url_for(path))
    add_marker(path)
    _corrupt_marker_page(path)
    with closing(sqlite3.connect(path)) as con, pytest.raises(sqlite3.DatabaseError):
        con.execute("SELECT * FROM operator_marker").fetchall()  # truly corrupt

    assert inspect_schema(url_for(path)) is SchemaStatus.CURRENT  # not detected, by design
    assert prepare_runtime_database_for_startup(url_for(path)) is SchemaStatus.CURRENT


def test_init_db_refuses_to_migrate_a_physically_corrupt_pending_database(
    old_db: tuple[Path, str],
) -> None:
    """Unlike startup, an upgrade verifies the copy it is about to rely on."""
    path, url = old_db
    _corrupt_marker_page(path)
    before = digest(path)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code in {"RUNTIME_DB_BACKUP_INVALID", "RUNTIME_DB_BACKUP_FAILED"}
    assert digest(path) == before and revision_of(path) == OLD_REVISION
    assert backups(path) == [] and leftovers(path) == []


# ---- L-01: runtime artifacts must never be committed by accident


@pytest.mark.parametrize(
    "name",
    [
        "runtime.sqlite3.pre-8ea6f1e5c7b2-20261112T213131412054Z.bak",
        "runtime.sqlite3.pre-8ea6f1e5c7b2-20261112T213131412054Z.bak.partial",
        "runtime.sqlite3.migrate.lock",
        "runtime.sqlite3.runtime-listener.lock",
        "runtime.sqlite3-wal",
        "runtime.sqlite3-shm",
        "runtime.sqlite3-journal",
    ],
)
def test_gitignore_covers_runtime_backups_partials_locks_and_sidecars(name: str) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    root = project_root()
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", name],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    assert result.returncode == 0, f"{name} is not ignored"


def test_release_failure_never_masks_an_error_already_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unversioned.sqlite3"
    with closing(sqlite3.connect(path)) as con:
        con.execute("CREATE TABLE products(id INTEGER)")
        con.commit()
    _failing_close(monkeypatch, None, path)  # every lock handle fails to close

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url_for(path))

    assert raised.value.code == "RUNTIME_DB_UNVERSIONED"  # the real reason, not the release error
    monkeypatch.undo()
    assert locks_are_free(path)


# ======================================== third audit N-01: Windows names that change on creation

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="trailing dot/space normalisation is a Windows file-name rule"
)

# Spellings whose effective file name differs from the literal one once the file exists.
AMBIGUOUS_NAMES = [
    "runtime.sqlite3.",
    "runtime.sqlite3..",
    "runtime.sqlite3 ",
    "runtime.sqlite3. ",
    "sub./runtime.sqlite3",
    "sub /runtime.sqlite3",
    "sub./nested/runtime.sqlite3",
    "sub\\runtime.sqlite3.",
    "sub .\\runtime.sqlite3",
]


def ambiguous_url(root: Path, name: str) -> str:
    return f"sqlite+aiosqlite:///{root.as_posix()}/{name}"


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def table_names(path: Path) -> set[str]:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as con:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@windows_only
@pytest.mark.parametrize("name", AMBIGUOUS_NAMES)
def test_ambiguous_windows_names_are_refused_before_any_filesystem_effect(
    tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    url = ambiguous_url(tmp_path, name)
    before = tree(tmp_path)

    with pytest.raises(RuntimeMigrationError) as inspected:
        inspect_schema(url)
    with pytest.raises(RuntimeMigrationError) as upgraded:
        upgrade_runtime_database(url)
    with pytest.raises(RuntimeMigrationError) as listened:
        prepare_runtime_database_for_startup(url)

    for raised in (inspected, upgraded, listened):
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
        assert str(tmp_path) not in str(raised.value)
    assert main(["init-db", "--database-url", url]) == 2
    err = capsys.readouterr().err
    assert "RUNTIME_DB_URL_UNSUPPORTED" in err and str(tmp_path) not in err
    # No lock file, sidecar, database, backup or directory appeared anywhere.
    assert tree(tmp_path) == before


@windows_only
def test_ambiguous_name_is_refused_even_when_the_file_already_exists(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    upgrade_database(url_for(path))
    before = digest(path)

    for call in (inspect_schema, upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call(ambiguous_url(tmp_path, "runtime.sqlite3."))
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
    assert digest(path) == before and locks_are_free(path)


@pytest.mark.parametrize("suffix", ["?name.sqlite3", "?", "?mode=ro", "?x=1"])
def test_any_query_marker_is_refused_even_when_the_parser_would_drop_it(
    tmp_path: Path, suffix: str
) -> None:
    """A '?' with no key=value pair is silently discarded by the URL parser: refuse it first."""
    url = f"{url_for(tmp_path / 'runtime.sqlite3')}{suffix}"
    before = tree(tmp_path)

    for call in (inspect_schema, upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call(url)
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
    assert tree(tmp_path) == before


@windows_only
def test_extended_length_prefix_cannot_smuggle_an_ambiguous_name(tmp_path: Path) -> None:
    before = tree(tmp_path)

    for text in (
        f"sqlite+aiosqlite:///\\\\?\\{tmp_path}\\runtime.sqlite3.",  # parsed as a query
        f"sqlite+aiosqlite:///{tmp_path}\\runtime.sqlite3.",  # plain backslash form
    ):
        with pytest.raises(RuntimeMigrationError) as raised:
            upgrade_runtime_database(text)
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
    assert tree(tmp_path) == before


@windows_only
@pytest.mark.parametrize("name", ["runtime.sqlite3.", "runtime.sqlite3 "])
def test_two_listener_processes_never_share_a_file_through_an_ambiguous_name(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]], name: str
) -> None:
    ambiguous = ambiguous_url(tmp_path, name)
    normal = url_for(tmp_path / "runtime.sqlite3")

    first = spawn(HOLD_LISTENER, ambiguous)
    first_answer = first_line(first)
    second = spawn(HOLD_LISTENER, normal)
    second_answer = first_line(second)

    assert first_answer == "ERR RUNTIME_DB_URL_UNSUPPORTED"  # never READY on a name that morphs
    assert second_answer == "READY missing"  # the plain spelling is the one true owner
    third = spawn(HOLD_LISTENER, normal)
    assert first_line(third) == "ERR RUNTIME_DB_LISTENER_ACTIVE"
    # Observe the real files: exactly one database, guarded by exactly one pair of lock files.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "runtime.sqlite3",
        "runtime.sqlite3.migrate.lock",
        "runtime.sqlite3.runtime-listener.lock",
    ]
    release(second)


@pytest.fixture
def additive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A synthetic migration graph with one extra additive revision above the real head."""
    root = tmp_path.parent / (tmp_path.name + "_synthetic_project")
    root.mkdir()
    shutil.copy(project_root() / "alembic.ini", root / "alembic.ini")
    shutil.copytree(
        project_root() / "migrations",
        root / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (root / "migrations" / "versions" / "ffffa001_additive.py").write_text(
        "import sqlalchemy as sa\n"
        "from alembic import op\n"
        'revision = "ffffa001"\n'
        f'down_revision = "{HEAD_REVISION}"\n'
        "def upgrade():\n"
        '    op.create_table("additive_marker", sa.Column("id", sa.Integer, primary_key=True))\n'
        "def downgrade():\n"
        "    pass\n"
    )
    monkeypatch.setattr(migrations, "project_root", lambda: root)
    return root


@windows_only
def test_no_listener_can_start_on_an_ambiguous_name_so_migration_never_races_one(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]], additive_root: Path
) -> None:
    listener = spawn(HOLD_LISTENER, ambiguous_url(tmp_path, "runtime.sqlite3."))
    answer = first_line(listener)
    path = tmp_path / "runtime.sqlite3"

    assert answer == "ERR RUNTIME_DB_URL_UNSUPPORTED"
    assert not path.exists()  # the refused listener created nothing


@windows_only
def test_listener_on_the_plain_name_blocks_migration_requested_through_an_ambiguous_one(
    tmp_path: Path, spawn: Callable[..., subprocess.Popen[str]], additive_root: Path
) -> None:
    path = tmp_path / "runtime.sqlite3"
    listener = spawn(HOLD_LISTENER, url_for(path))
    assert first_line(listener) == "READY missing"

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(ambiguous_url(tmp_path, "runtime.sqlite3."))

    assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
    assert revision_of(path) == HEAD_REVISION  # the synthetic additive revision was NOT applied
    assert "additive_marker" not in table_names(path)
    with pytest.raises(RuntimeMigrationError) as plain:  # and the plain spelling is still locked
        upgrade_runtime_database(url_for(path))
    assert plain.value.code == "RUNTIME_DB_LISTENER_ACTIVE"
    assert "additive_marker" not in table_names(path)
    release(listener)


def make_orphan_wal(path: Path) -> None:
    """A real WAL holding a committed, never-checkpointed row, whose main file is gone."""
    evidence = path.parent / "evidence"
    evidence.mkdir()
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("CREATE TABLE crash_marker(value TEXT)")
        con.execute("INSERT INTO crash_marker VALUES ('committed-before-crash')")
        con.commit()
        assert Path(f"{path}-wal").stat().st_size > 0
        # What a crashed writer leaves behind: copy the live set before any clean close.
        for suffix in ("", "-wal", "-shm"):
            shutil.copy(f"{path}{suffix}", evidence / f"runtime.sqlite3{suffix}")
    finally:
        con.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):  # restoration lost the main file but kept the sidecars
        shutil.copy(evidence / f"runtime.sqlite3{suffix}", f"{path}{suffix}")


@windows_only
@pytest.mark.parametrize("name", ["runtime.sqlite3.", "runtime.sqlite3 "])
def test_orphan_wal_with_recoverable_commit_survives_an_ambiguous_name(
    tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "runtime.sqlite3"
    make_orphan_wal(path)
    wal = Path(f"{path}-wal")
    before = digest(wal)
    listing = tree(tmp_path)

    for call in (upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call(ambiguous_url(tmp_path, name))
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
    assert main(["init-db", "--database-url", ambiguous_url(tmp_path, name)]) == 2
    capsys.readouterr()

    assert wal.exists() and digest(wal) == before  # evidence untouched
    assert not path.exists()  # no new database appeared over it
    assert tree(tmp_path) == listing
    # The plain spelling keeps the protection that the audit proved to be bypassable.
    with pytest.raises(RuntimeMigrationError) as plain:
        upgrade_runtime_database(url_for(path))
    assert plain.value.code == "RUNTIME_DB_ORPHAN_SIDECARS"
    with closing(sqlite3.connect(tmp_path / "evidence" / "runtime.sqlite3")) as con:
        assert con.execute("SELECT value FROM crash_marker").fetchall() == [
            ("committed-before-crash",)
        ]


@windows_only
@pytest.mark.parametrize("name", ["runtime.sqlite3.", "runtime.sqlite3 "])
def test_orphan_rollback_journal_survives_an_ambiguous_name(tmp_path: Path, name: str) -> None:
    path = tmp_path / "runtime.sqlite3"
    journal = Path(f"{path}-journal")
    journal.write_bytes(b"hot journal of a database that was moved away" * 40)
    before = digest(journal)

    for call in (upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call(ambiguous_url(tmp_path, name))
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"

    assert journal.exists() and digest(journal) == before and not path.exists()


@pytest.mark.parametrize(
    "name",
    [
        "runtime.sqlite3",
        "my runtime.sqlite3",  # inner space
        "runtime.v2.sqlite3",  # inner dots
        ".runtime.sqlite3",  # leading dot
        "dir with space/runtime.sqlite3",
        "./sub/../runtime.sqlite3",  # structural segments
        "sub/./nested/../runtime.sqlite3",
        "../work/runtime.sqlite3",
    ],
)
def test_normal_spellings_keep_working_and_use_the_files_they_name(
    tmp_path: Path, name: str
) -> None:
    base = tmp_path / "work"
    (base / "sub" / "nested").mkdir(parents=True)
    (base / "dir with space").mkdir()
    url = f"sqlite+aiosqlite:///{base.as_posix()}/{name}"
    expected = (base / name).resolve()

    assert inspect_schema(url) is SchemaStatus.MISSING
    with runtime_listener_session(url) as session:
        assert session.path == expected
        assert expected.is_file()  # the file actually opened is the file that was locked
        assert Path(f"{expected}.runtime-listener.lock").is_file()
        with pytest.raises(RuntimeMigrationError) as raised:
            upgrade_runtime_database(url)
        assert raised.value.code == "RUNTIME_DB_LISTENER_ACTIVE"
    assert revision_of(expected) == HEAD_REVISION
    assert upgrade_runtime_database(url).status == "current"


def test_the_ambiguity_rule_accepts_normal_names_and_structural_segments() -> None:
    for text in ("a/./b/c.sqlite3", "../x/c.sqlite3", "x y/.c.sqlite3", "x/c.d.sqlite3"):
        rm._reject_ambiguous_windows_name(text)  # must not raise


@windows_only
def test_home_expansion_cannot_introduce_an_ambiguous_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check also runs on the text AFTER '~' expansion, not only on the literal URL."""
    home = tmp_path / "home."
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    before = tree(tmp_path)

    for call in (inspect_schema, upgrade_runtime_database, prepare_runtime_database_for_startup):
        with pytest.raises(RuntimeMigrationError) as raised:
            call("sqlite+aiosqlite:///~/rt.sqlite3")
        assert raised.value.code == "RUNTIME_DB_URL_UNSUPPORTED"
    assert tree(tmp_path) == before
