from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def migration_config(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    return config


def test_discovery_migration_uses_composite_cache_identity_and_roundtrips(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "discovery.sqlite3"
    config = migration_config(database_path)
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "aliexpress_discovery_runs",
            "aliexpress_discovery_query_claims",
            "aliexpress_discovery_query_cache",
            "aliexpress_discovery_cache_products",
            "aliexpress_discovery_price_snapshots",
            "aliexpress_discovery_run_results",
        } <= tables
        cache_pk = {
            row[1]: row[5]
            for row in connection.execute("PRAGMA table_info(aliexpress_discovery_query_cache)")
            if row[5]
        }
        assert cache_pk == {"query_fingerprint": 1, "tracking_fingerprint": 2}
        product_fks = connection.execute(
            "PRAGMA foreign_key_list(aliexpress_discovery_cache_products)"
        ).fetchall()
        assert {(row[2], row[3], row[4]) for row in product_fks} == {
            (
                "aliexpress_discovery_query_cache",
                "query_fingerprint",
                "query_fingerprint",
            ),
            (
                "aliexpress_discovery_query_cache",
                "tracking_fingerprint",
                "tracking_fingerprint",
            ),
        }
        all_columns = {
            row[1]
            for table in tables
            if table.startswith("aliexpress_discovery_")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        assert "tracking_id" not in all_columns
        assert "raw_response" not in all_columns
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO aliexpress_discovery_query_cache "
                "(query_fingerprint,tracking_fingerprint,fetched_at,expires_at,item_count) "
                "VALUES (?,?,?,?,?)",
                (
                    "a" * 64,
                    "too-short",
                    "2026-09-17T12:00:00",
                    "2026-09-17T13:00:00",
                    0,
                ),
            )

    command.downgrade(config, "b72e4c9d1a30")
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'aliexpress_discovery_%'"
        ).fetchall()


def test_alembic_cli_config_honors_explicit_environment_database_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_path = tmp_path / "external" / "discovery.sqlite3"
    fallback_path = tmp_path / "promo_bot.sqlite3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "PROMO_BOT_DATABASE_URL",
        f"sqlite+aiosqlite:///{external_path.as_posix()}",
    )
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(config, "head")

    assert external_path.exists()
    assert not fallback_path.exists()
