"""Fail closed before constructing real generation transports or sending TOP."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.engine import make_url

from promo_bot.database.history_repository import AffiliateHistoryError

HISTORY_SCHEMA_VERSION = "b8c2e4f6a901"


def _read_history_schema(uri: str) -> None:
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {
            "alembic_version",
            "affiliate_link_generations",
            "affiliate_link_uses",
            "affiliate_link_use_links",
        } <= tables or conn.execute("SELECT version_num FROM alembic_version").fetchall() != [
            (HISTORY_SCHEMA_VERSION,)
        ]:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_SCHEMA_REQUIRED")


def _identity(stat: os.stat_result) -> tuple[int, ...]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _check_closed_source(
    path: Path,
    source: BinaryIO,
    identity: tuple[int, ...],
    path_identity: tuple[int, ...],
    digest: bytes | None = None,
) -> None:
    if (
        _identity(os.fstat(source.fileno())) != identity
        or _identity(path.stat()) != path_identity
        or any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))
    ):
        raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_UNSTABLE")
    if digest is not None:
        source.seek(0)
        current = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            current.update(chunk)
        if current.digest() != digest:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_UNSTABLE")
        # Also detect changes made during the second read.
        _check_closed_source(path, source, identity, path_identity)


def _read_closed_wal_schema(path: Path, source: BinaryIO) -> None:
    """Query a private frozen copy, only when *all* original sidecars are absent.

    Identity/hash checks detect changes; they are not an atomic snapshot or a
    lock against external SQLite writers. External writes/migrations/replacement
    during preflight are unsupported. The listener lock covers only cooperating
    listeners using the same database. No SQLite connection opens the original.
    """
    identity = _identity(os.fstat(source.fileno()))
    path_identity = _identity(path.stat())
    # fstat/stat timestamps differ on Windows/Python 3.12; compare each against
    # its own baseline, but require the descriptor and path to identify one file.
    if identity[:2] != path_identity[:2]:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_UNSTABLE")
    _check_closed_source(path, source, identity, path_identity)
    with tempfile.TemporaryDirectory(prefix="promo-bot-schema-") as directory:
        copied = Path(directory) / "schema.sqlite3"
        source.seek(0)
        digest = hashlib.sha256()
        with copied.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                target.write(chunk)
        source_digest = digest.digest()
        _check_closed_source(path, source, identity, path_identity, source_digest)
        try:
            # Only this function owns/writes the copy, now closed and frozen.
            # immutable is safe here, never applied to the original database.
            _read_history_schema(copied.as_uri() + "?mode=ro&immutable=1")
        finally:
            _check_closed_source(path, source, identity, path_identity, source_digest)


def validate_history_schema_readonly(path: Path) -> None:
    """Decide schema without writing/checkpointing the original database or WAL.

    With both WAL sidecars present, mode=ro reads committed WAL frames; existing
    shm may be rebuilt/updated. With neither present, a closed WAL database is
    checked via a private copy. Incomplete sidecars and rollback journals fail
    closed without recovery. Full storage/integrity/FK checks follow admission.
    """
    try:
        if not path.is_file():
            raise AffiliateHistoryError("AFFILIATE_HISTORY_DATABASE_REQUIRED")
        wal = Path(str(path) + "-wal")
        shm = Path(str(path) + "-shm")
        if os.path.lexists(str(path) + "-journal"):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_RECOVERY_REQUIRED")
        wal_present, shm_present = os.path.lexists(wal), os.path.lexists(shm)
        if wal_present != shm_present or (
            wal_present
            and (not wal.is_file() or not shm.is_file() or wal.is_symlink() or shm.is_symlink())
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_WAL_UNVERIFIABLE")
        with path.open("rb") as main_file:
            wal_mode = main_file.read(20)[18:20] == b"\x02\x02"
            if wal_present and not wal_mode:
                # SQLite could ignore WAL for a non-WAL header. Do not decide
                # schema from that ambiguous main-file-only view.
                raise AffiliateHistoryError("AFFILIATE_HISTORY_WAL_UNVERIFIABLE")
            if wal_mode and not wal_present:
                _read_closed_wal_schema(path, main_file)
                return
        _read_history_schema(path.as_uri() + "?mode=ro")
    except AffiliateHistoryError:
        raise
    except Exception:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_UNAVAILABLE") from None


def durable_sqlite_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
        if (
            url.drivername != "sqlite+aiosqlite"
            or not url.database
            or url.query
            or url.database.startswith("file:")
            or url.database == ":memory:"
        ):
            raise ValueError()
        path = Path(url.database)
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
            raise ValueError()
        resolved = path.resolve()
        temporary_roots = {Path(tempfile.gettempdir()).resolve()}
        temporary_roots.update(
            Path(value).resolve()
            for name in ("TEMP", "TMP", "TMPDIR")
            if (value := os.environ.get(name))
        )
        if resolved.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"} or any(
            resolved.is_relative_to(root) for root in temporary_roots
        ):
            raise ValueError()
        if os.name == "nt":
            import ctypes

            # Only a local fixed drive; mapped network, removable and RAM drives fail closed.
            if ctypes.windll.kernel32.GetDriveTypeW(str(resolved.anchor)) != 3:
                raise ValueError()
        return resolved
    except Exception:
        raise ValueError("AFFILIATE_HISTORY_STORAGE_NOT_DURABLE") from None
