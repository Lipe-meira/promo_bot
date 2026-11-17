"""Alembic environment for the control-plane PostgreSQL database.

The URL comes from `PROMO_API_DATABASE_URL` (or `-x database_url=...`); nothing is read from the
bot's configuration and no SQLite database is ever touched.

Two guards run before anything connects, because this is the one step that changes a real schema:
the URL must be the *direct* endpoint (not a connection pooler), and a target that is not on the
local machine must be confirmed by name with `-x confirm=<host>`.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection

from promo_api.db.engine import confirm_target, create_operator_engine, prepare_database
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
    url = _database_url()
    confirm_target(url, context.get_x_argument(as_dictionary=True).get("confirm"))
    engine = create_operator_engine(url, require_direct=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=prepare_database(_database_url()).url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
