"""Alembic environment for the control-plane PostgreSQL database.

The URL comes from `PROMO_API_DATABASE_URL` (or `-x database_url=...`); nothing is read from the
bot's configuration and no SQLite database is ever touched.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from promo_api.db.models import Base

target_metadata = Base.metadata


def _database_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("database_url") or os.environ.get(
        "PROMO_API_DATABASE_URL", ""
    )
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("PROMO_API_DATABASE_URL must be a postgresql+asyncpg:// URL")
    return url


def _migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
