"""Programmatic Alembic entry point used by the CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from promo_bot.database.session import ensure_sqlite_parent


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _migration_config(database_url: str) -> Config:
    ensure_sqlite_parent(database_url)
    root = project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _upgrade_with_connection(connection: Connection, config: Config) -> None:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def upgrade_database_async(database_url: str) -> None:
    config = _migration_config(database_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade_with_connection, config)
    finally:
        await engine.dispose()


def upgrade_database(database_url: str) -> None:
    asyncio.run(upgrade_database_async(database_url))
