from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def migration_config(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    return config


def test_coin_shadow_migration_constraints_and_roundtrip(tmp_path: Path) -> None:
    database_path = tmp_path / "coin-shadow.sqlite3"
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
            "aliexpress_coin_shadow_evidence",
            "aliexpress_coin_shadow_previews",
            "aliexpress_coin_shadow_deliveries",
        } <= tables
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(aliexpress_coin_shadow_evidence)")
        }
        assert "raw_response" not in columns
        assert "source_value" not in columns
        assert "tracking_id" not in columns

        common = (
            "a" * 64,
            "b" * 64,
            0,
            "GENERATING",
            1,
            1,
            0,
            0,
            "2026-09-16T12:00:00+00:00",
            "2026-09-16T12:05:00+00:00",
            "lease-token",
            "2026-09-16T12:00:00+00:00",
            "2026-09-16T12:00:00+00:00",
        )
        connection.execute(
            "INSERT INTO aliexpress_coin_shadow_evidence "
            "(input_fingerprint,tracking_fingerprint,promotion_link_type,state,generation_count,"
            "attribution_unverified,route_preservation_manually_observed,tracking_confirmed,"
            "generation_started_at,lease_until,lease_token,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            common,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO aliexpress_coin_shadow_evidence "
                "(input_fingerprint,tracking_fingerprint,promotion_link_type,state,"
                "generation_count,attribution_unverified,route_preservation_manually_observed,"
                "tracking_confirmed,generation_started_at,lease_until,lease_token,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                common,
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE aliexpress_coin_shadow_evidence SET generation_count=2 WHERE id=1"
            )

    command.downgrade(config, "a91c2d4e6f80")
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'aliexpress_coin_shadow_%'"
        ).fetchall()
