"""Fail closed before constructing real generation transports or sending TOP."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from promo_bot.database.history_repository import AffiliateHistoryError

HISTORY_SCHEMA_VERSION = "b8c2e4f6a901"


def validate_history_schema_readonly(path: Path) -> None:
    """Admit the committed SQLite view without a writable open/checkpoint.

    mode=ro (not immutable) reads committed WAL frames. Existing shm may be
    rebuilt/updated by SQLite. WAL mode requires existing WAL and shm, rather than
    creating sidecars or inspecting a stale main-file-only view. No recovery writes
    to the database/WAL are permitted, and any read/locking failure fails closed.
    Full storage/integrity/FK validation still follows for admitted databases.
    """
    try:
        if not path.is_file():
            raise AffiliateHistoryError("AFFILIATE_HISTORY_DATABASE_REQUIRED")
        wal = Path(str(path) + "-wal")
        shm = Path(str(path) + "-shm")
        with path.open("rb") as main_file:
            wal_mode = main_file.read(20)[18:20] == b"\x02\x02"
        if (wal_mode or (wal.exists() and wal.stat().st_size > 0)) and not (
            wal.is_file() and shm.is_file()
        ):
            # Even mode=ro creates an empty WAL for a WAL-mode main file when
            # sidecars are absent. Do not open SQLite in that situation.
            raise AffiliateHistoryError("AFFILIATE_HISTORY_WAL_UNVERIFIABLE")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                not {
                    "alembic_version",
                    "affiliate_link_generations",
                    "affiliate_link_uses",
                    "affiliate_link_use_links",
                }
                <= tables
            ):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_SCHEMA_REQUIRED")
            if conn.execute("SELECT version_num FROM alembic_version").fetchall() != [
                (HISTORY_SCHEMA_VERSION,)
            ]:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_SCHEMA_REQUIRED")
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
