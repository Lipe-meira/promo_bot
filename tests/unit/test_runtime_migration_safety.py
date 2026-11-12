"""F-07: automatic runtime-database migrations must be guarded (offline, temp SQLite only)."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import threading
from collections.abc import Callable
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
    prepare_runtime_database_for_startup,
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
    return sorted(p for p in path.parent.iterdir() if p.name.endswith((".partial", ".lock")))


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


def test_startup_blocked_while_migration_lock_exists(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    upgrade_database(url_for(path))
    Path(f"{path}.migrate.lock").write_text("held")

    with pytest.raises(RuntimeMigrationError) as raised:
        prepare_runtime_database_for_startup(url_for(path))

    assert raised.value.code == "RUNTIME_DB_MIGRATION_IN_PROGRESS"


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
    assert not Path(f"{path}.migrate.lock").exists()
    # The failed in-place attempt is known to leave a half-migrated file; startup must refuse it.
    with pytest.raises(RuntimeMigrationError):
        prepare_runtime_database_for_startup(url)


# ----------------------------------------------------------------------- concurrency


def test_existing_migration_lock_blocks_upgrade_and_is_not_removed(
    old_db: tuple[Path, str],
) -> None:
    path, url = old_db
    lock = Path(f"{path}.migrate.lock")
    lock.write_text("held by another process")
    before = digest(path)

    with pytest.raises(RuntimeMigrationError) as raised:
        upgrade_runtime_database(url)

    assert raised.value.code == "RUNTIME_DB_MIGRATION_IN_PROGRESS"
    assert lock.exists()
    assert digest(path) == before and backups(path) == []


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
