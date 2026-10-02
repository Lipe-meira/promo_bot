from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from promo_bot.database import history_storage
from promo_bot.database.session import Database


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///file::memory:?cache=shared",
        "sqlite+aiosqlite:///relative.sqlite3",
        "sqlite+aiosqlite:///file:demo?mode=memory",
    ],
)
def test_real_history_rejects_transient_storage_before_opening(url: str) -> None:
    validator = getattr(history_storage, "durable_sqlite_path", None)
    assert validator is not None, "real generation storage validation is missing"
    with pytest.raises(ValueError, match="AFFILIATE_HISTORY_STORAGE_NOT_DURABLE"):
        validator(url)


def test_real_history_rejects_named_database_under_temporary_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="AFFILIATE_HISTORY_STORAGE_NOT_DURABLE"):
        history_storage.durable_sqlite_path(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/audit.db")


async def test_database_enforces_foreign_keys_and_full_synchronous_for_audit(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'synthetic.db').as_posix()}")
    try:
        async with database.session() as session:
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            assert await session.scalar(text("PRAGMA synchronous")) == 2
            assert await session.scalar(text("PRAGMA journal_mode")) not in {"off", "memory"}
    finally:
        await database.dispose()
