"""Fail closed before constructing real generation transports or sending TOP."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url


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
