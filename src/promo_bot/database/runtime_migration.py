"""Guarded schema preparation for the runtime SQLite database (F-07).

Startup (`listen`) may only *create* a brand-new database. Any upgrade of an existing
database is an explicit `init-db` operation that first takes a verified, consistent
backup. Nothing here logs or raises filesystem paths or database content.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy.engine import make_url

from promo_bot.database import migrations
from promo_bot.database.migrations import upgrade_database_async

_SIDECARS = ("-journal", "-wal", "-shm")
_SPACE_MARGIN_BYTES = 1024 * 1024


class SchemaStatus(StrEnum):
    MISSING = "missing"
    EMPTY = "empty"
    CURRENT = "current"
    PENDING = "pending"
    UNKNOWN_REVISION = "unknown_revision"
    UNVERSIONED = "unversioned"
    UNREADABLE = "unreadable"


class RuntimeMigrationError(RuntimeError):
    """Fail-closed database preparation error; `str()` is safe to print and log."""

    def __init__(self, code: str, guidance: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {guidance}" if guidance else code)


@dataclass(frozen=True)
class MigrationOutcome:
    status: str  # "upgraded" | "current"
    backup_name: str | None = None


def _database_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
        if (
            url.drivername != "sqlite+aiosqlite"
            or not url.database
            or url.database == ":memory:"
            or url.query
        ):
            raise ValueError
        return Path(url.database).expanduser().resolve()
    except Exception:
        raise RuntimeMigrationError("RUNTIME_DB_URL_UNSUPPORTED") from None


def _lock_path(path: Path) -> Path:
    return Path(f"{path}.migrate.lock")


def _read_revision(connection: sqlite3.Connection) -> str | None:
    rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return str(rows[0][0]) if len(rows) == 1 else None


def inspect_schema(database_url: str) -> SchemaStatus:
    """Classify the database read-only. Never creates, migrates or recovers anything."""

    path = _database_path(database_url)
    if not os.path.lexists(path):
        return SchemaStatus.MISSING
    try:
        if not path.is_file() or os.path.lexists(f"{path}-journal"):
            # A hot rollback journal would be rolled back by a writer; never decide on it.
            return SchemaStatus.UNREADABLE
        if path.stat().st_size == 0:
            return SchemaStatus.UNREADABLE if _has_sidecar(path) else SchemaStatus.EMPTY
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)) as con:
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not tables:
                return SchemaStatus.EMPTY
            if "alembic_version" not in tables:
                return SchemaStatus.UNVERSIONED
            revision = _read_revision(con)
    except (sqlite3.Error, OSError):
        return SchemaStatus.UNREADABLE
    script = migrations.migration_script_directory()
    if revision is None:
        return SchemaStatus.UNKNOWN_REVISION
    if revision == script.get_current_head():
        return SchemaStatus.CURRENT
    try:
        known = script.get_revision(revision) is not None
    except Exception:
        known = False
    return SchemaStatus.PENDING if known else SchemaStatus.UNKNOWN_REVISION


def _has_sidecar(path: Path) -> bool:
    return any(os.path.lexists(f"{path}{suffix}") for suffix in _SIDECARS)


@contextmanager
def _migration_lock(path: Path) -> Iterator[None]:
    lock = _lock_path(path)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise _in_progress() from None
    except OSError:
        raise RuntimeMigrationError("RUNTIME_DB_LOCK_FAILED") from None
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        with suppress(OSError):
            lock.unlink()


def _in_progress() -> RuntimeMigrationError:
    return RuntimeMigrationError(
        "RUNTIME_DB_MIGRATION_IN_PROGRESS",
        "another init-db run holds the migration lock; if none is running, verify the "
        "database and remove the stale '.migrate.lock' file beside it",
    )


def _refusal(status: SchemaStatus) -> RuntimeMigrationError:
    codes = {
        SchemaStatus.UNKNOWN_REVISION: "RUNTIME_DB_REVISION_UNKNOWN",
        SchemaStatus.UNVERSIONED: "RUNTIME_DB_UNVERSIONED",
        SchemaStatus.UNREADABLE: "RUNTIME_DB_UNREADABLE",
    }
    return RuntimeMigrationError(
        codes[status], "refusing to modify a database this version cannot safely migrate"
    )


def _apply_upgrade(database_url: str, path: Path, *, created_new: bool, backup: str | None) -> None:
    try:
        asyncio.run(upgrade_database_async(database_url))
    except Exception:
        if created_new:
            for suffix in ("", *_SIDECARS):  # we created it, so nothing of value is lost
                with suppress(OSError):
                    Path(f"{path}{suffix}").unlink()
            raise RuntimeMigrationError("RUNTIME_DB_MIGRATION_FAILED") from None
        raise RuntimeMigrationError(
            "RUNTIME_DB_MIGRATION_FAILED",
            f"the database may be partially migrated and is blocked; verified backup "
            f"'{backup}' was kept beside it (see docs/ARCHITECTURE.md, database recovery)",
        ) from None


def prepare_runtime_database_for_startup(database_url: str) -> SchemaStatus:
    """Create a new database if absent; otherwise only verify it is exactly current."""

    path = _database_path(database_url)
    if os.path.lexists(_lock_path(path)):
        raise _in_progress()
    status = inspect_schema(database_url)
    if status is SchemaStatus.CURRENT:
        return status
    if status in (SchemaStatus.MISSING, SchemaStatus.EMPTY):
        with _migration_lock(path):
            status = inspect_schema(database_url)
            if status in (SchemaStatus.MISSING, SchemaStatus.EMPTY):
                _apply_upgrade(
                    database_url, path, created_new=status is SchemaStatus.MISSING, backup=None
                )
                return status
            if status is SchemaStatus.CURRENT:
                return status
    if status is SchemaStatus.PENDING:
        raise RuntimeMigrationError(
            "RUNTIME_DB_MIGRATION_REQUIRED",
            "the runtime database schema is behind this version; stop other bot processes "
            "and run 'promo-bot init-db' explicitly (it takes a verified backup first)",
        )
    raise _refusal(status)


def _copy_snapshot(source: Path, dest: Path) -> None:
    """Consistent online copy through SQLite's backup API (WAL-safe, unlike a file copy)."""

    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(dest)) as dst:
            src.backup(dst)
            # The copy inherits WAL mode from the source; keep the backup one self-contained file.
            if dst.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise sqlite3.Error("backup journal mode")


def _verify_backup(partial: Path) -> str | None:
    try:
        with closing(sqlite3.connect(f"{partial.as_uri()}?mode=ro", uri=True, timeout=5)) as con:
            if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeMigrationError("RUNTIME_DB_BACKUP_INVALID")
            return _read_revision(con)
    except sqlite3.Error:
        raise RuntimeMigrationError("RUNTIME_DB_BACKUP_INVALID") from None


def _remove_partial(partial: Path) -> None:
    for suffix in ("", *_SIDECARS):
        with suppress(OSError):
            Path(f"{partial}{suffix}").unlink()


def _make_backup(database_url: str, path: Path, expected_revision: str) -> Path | None:
    """Return a verified backup path, or None when the database became current meanwhile."""

    wal = Path(f"{path}-wal")
    size = path.stat().st_size + (wal.stat().st_size if wal.is_file() else 0)
    try:
        free = shutil.disk_usage(path.parent).free
    except OSError:
        raise RuntimeMigrationError("RUNTIME_DB_BACKUP_FAILED") from None
    # One copy for the backup plus headroom for SQLite's table rebuilds during migration.
    if free < 2 * size + _SPACE_MARGIN_BYTES:
        raise RuntimeMigrationError("RUNTIME_DB_BACKUP_NO_SPACE")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    final = path.with_name(f"{path.name}.pre-{expected_revision}-{stamp}.bak")
    partial = Path(f"{final}.partial")
    try:
        if os.path.lexists(final) or os.path.lexists(partial):
            raise RuntimeMigrationError("RUNTIME_DB_BACKUP_FAILED")
        try:
            _copy_snapshot(path, partial)
            revision = _verify_backup(partial)
            if revision != expected_revision:
                if (
                    revision == migrations.migration_script_directory().get_current_head()
                    and inspect_schema(database_url) is SchemaStatus.CURRENT
                ):
                    _remove_partial(partial)
                    return None  # lost a race to another upgrade; this copy is not "pre-upgrade"
                raise RuntimeMigrationError("RUNTIME_DB_BACKUP_INVALID")
            with partial.open("r+b") as handle:
                os.fsync(handle.fileno())
        except (OSError, sqlite3.Error):
            raise RuntimeMigrationError("RUNTIME_DB_BACKUP_FAILED") from None
        for suffix in _SIDECARS:  # a valid backup is one self-contained file
            if os.path.lexists(f"{partial}{suffix}"):
                raise RuntimeMigrationError("RUNTIME_DB_BACKUP_INVALID")
        partial.replace(final)
        return final
    except BaseException:
        _remove_partial(partial)
        raise


def upgrade_runtime_database(database_url: str) -> MigrationOutcome:
    """Explicit upgrade: lock, re-inspect, verified backup of existing data, then migrate."""

    path = _database_path(database_url)
    with _migration_lock(path):
        status = inspect_schema(database_url)
        if status is SchemaStatus.CURRENT:
            return MigrationOutcome("current")
        if status in (SchemaStatus.MISSING, SchemaStatus.EMPTY):
            _apply_upgrade(
                database_url, path, created_new=status is SchemaStatus.MISSING, backup=None
            )
            return MigrationOutcome("upgraded")
        if status is not SchemaStatus.PENDING:
            raise _refusal(status)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)) as con:
            expected = _read_revision(con)
        if expected is None:
            raise _refusal(SchemaStatus.UNKNOWN_REVISION)
        backup = _make_backup(database_url, path, expected)
        if backup is None:
            return MigrationOutcome("current")
        _apply_upgrade(database_url, path, created_new=False, backup=backup.name)
        return MigrationOutcome("upgraded", backup.name)
