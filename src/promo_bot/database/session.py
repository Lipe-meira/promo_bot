"""Async engine and session lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def ensure_sqlite_parent(database_url: str) -> None:
    """Create only the configured database parent directory."""

    url = make_url(database_url)
    if url.drivername != "sqlite+aiosqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


class Database:
    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        ensure_sqlite_parent(database_url)
        self.engine: AsyncEngine = create_async_engine(database_url, echo=echo)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()
