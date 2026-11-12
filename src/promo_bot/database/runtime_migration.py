"""Guarded schema preparation for the runtime SQLite database (F-07).

Startup (`listen`) may only *create* a brand-new database. Any upgrade of an existing
database is an explicit `init-db` operation that first takes a verified, consistent
backup. Nothing here logs or raises filesystem paths or database content.

Two cooperative OS-level locks (released by the operating system when the owner dies,
so a forced kill leaves no orphan) are always taken in the same order, without blocking:

1. migration lock (`<db>.migrate.lock`): held by `init-db` for the whole upgrade and by a
   listener only while it inspects or creates the database at start-up;
2. listener lock (`<db>.runtime-listener.lock`): held by `listen` for the whole life of the
   monitor and by `init-db` for the whole upgrade.

A listener keeps the listener lock *before* releasing the migration lock, so there is no
unprotected interval between database preparation and the running monitor. The locks
are cooperative: tools that ignore them (for example the `sqlite3` shell) are not covered.
"""

from __future__ import annotations

import asyncio
import errno
import importlib
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from sqlalchemy.engine import make_url

from promo_bot.database import migrations
from promo_bot.database.migrations import upgrade_database_async

_SIDECARS = ("-journal", "-wal", "-shm")
_SPACE_MARGIN_BYTES = 1024 * 1024
_READ_TIMEOUT_SECONDS = 5.0
_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}


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


def _migration_lock_path(path: Path) -> Path:
    return Path(f"{path}.migrate.lock")


def _listener_lock_path(path: Path) -> Path:
    return Path(f"{path}.runtime-listener.lock")


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
        with closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=_READ_TIMEOUT_SECONDS)
        ) as con:
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


class _OsLock:
    """Nonblocking, process-wide advisory lock that the OS releases when the owner dies."""

    def __init__(self, path: Path, busy_code: str, guidance: str = "") -> None:
        self.path = path
        self._busy = (busy_code, guidance)
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeMigrationError("RUNTIME_DB_LOCK_FAILED")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
        except OSError:
            raise RuntimeMigrationError("RUNTIME_DB_LOCK_FAILED") from None
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
        except OSError:
            handle.close()
            raise RuntimeMigrationError("RUNTIME_DB_LOCK_FAILED") from None
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if isinstance(exc, BlockingIOError) or exc.errno in _BUSY_ERRNOS:
                raise RuntimeMigrationError(*self._busy) from None
            raise RuntimeMigrationError("RUNTIME_DB_LOCK_FAILED") from None
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass  # closing the handle releases the lock anyway
        finally:
            handle.close()

    def __enter__(self) -> _OsLock:
        self.acquire()
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.release()


def _migration_lock(path: Path) -> _OsLock:
    return _OsLock(
        _migration_lock_path(path),
        "RUNTIME_DB_MIGRATION_IN_PROGRESS",
        "an init-db run or a listener start-up is using this database; wait for it to "
        "finish and retry",
    )


def _listener_lock(path: Path) -> _OsLock:
    return _OsLock(
        _listener_lock_path(path),
        "RUNTIME_DB_LISTENER_ACTIVE",
        "a listener (or init-db) already holds this database; stop the listener before "
        "running init-db and never start a second listener on the same database",
    )


@contextmanager
def _sanitized_storage_errors() -> Iterator[None]:
    """Last line of defence: no raw OSError/sqlite3.Error (with paths) leaves this module."""

    try:
        yield
    except (OSError, sqlite3.Error):
        raise RuntimeMigrationError("RUNTIME_DB_STORAGE_ERROR") from None


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


def _prepare_locked(database_url: str, path: Path) -> SchemaStatus:
    """Create a new database if absent; otherwise only verify it is exactly current.

    The caller must hold both locks.
    """

    status = inspect_schema(database_url)
    if status is SchemaStatus.CURRENT:
        return status
    if status in (SchemaStatus.MISSING, SchemaStatus.EMPTY):
        _apply_upgrade(database_url, path, created_new=status is SchemaStatus.MISSING, backup=None)
        return status
    if status is SchemaStatus.PENDING:
        raise RuntimeMigrationError(
            "RUNTIME_DB_MIGRATION_REQUIRED",
            "the runtime database schema is behind this version; stop other bot processes "
            "and run 'promo-bot init-db' explicitly (it takes a verified backup first)",
        )
    raise _refusal(status)


@contextmanager
def runtime_listener_session(database_url: str) -> Iterator[SchemaStatus]:
    """Hold the database for one listener: prepare it, then keep it locked until exit.

    Creates a brand-new database, otherwise requires it to be exactly current. Any other
    state, a running `init-db` or another listener raises `RuntimeMigrationError`
    before the caller builds the monitor.
    """

    path = _database_path(database_url)
    migration, listener = _migration_lock(path), _listener_lock(path)
    with _sanitized_storage_errors():
        migration.acquire()
        try:
            listener.acquire()
            try:
                status = _prepare_locked(database_url, path)
            except BaseException:
                listener.release()
                raise
        finally:
            migration.release()
    try:
        yield status
    finally:
        listener.release()


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
        with closing(
            sqlite3.connect(f"{partial.as_uri()}?mode=ro", uri=True, timeout=_READ_TIMEOUT_SECONDS)
        ) as con:
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
    try:
        size = path.stat().st_size + (wal.stat().st_size if wal.is_file() else 0)
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
            for suffix in _SIDECARS:  # a valid backup is one self-contained file
                if os.path.lexists(f"{partial}{suffix}"):
                    raise RuntimeMigrationError("RUNTIME_DB_BACKUP_INVALID")
            partial.replace(final)
        except (OSError, sqlite3.Error):
            raise RuntimeMigrationError("RUNTIME_DB_BACKUP_FAILED") from None
        return final
    except BaseException:
        _remove_partial(partial)
        raise


def _read_expected_revision(path: Path) -> str | None:
    try:
        with closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=_READ_TIMEOUT_SECONDS)
        ) as con:
            return _read_revision(con)
    except sqlite3.Error:
        raise _refusal(SchemaStatus.UNREADABLE) from None


def upgrade_runtime_database(database_url: str) -> MigrationOutcome:
    """Explicit upgrade: both locks, inspect, verified backup of existing data, then migrate.

    Refuses while a listener (or another init-db) holds the database.
    """

    path = _database_path(database_url)
    with _sanitized_storage_errors(), _migration_lock(path), _listener_lock(path):
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
        expected = _read_expected_revision(path)
        if expected is None:
            raise _refusal(SchemaStatus.UNKNOWN_REVISION)
        backup = _make_backup(database_url, path, expected)
        if backup is None:
            return MigrationOutcome("current")
        _apply_upgrade(database_url, path, created_new=False, backup=backup.name)
        return MigrationOutcome("upgraded", backup.name)
