"""Programmatic Alembic entry point used by the CLI."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from promo_bot.database.session import ensure_sqlite_parent


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def upgrade_database(database_url: str) -> None:
    ensure_sqlite_parent(database_url)
    root = project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
