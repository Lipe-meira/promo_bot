from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

QUERY = "aliexpress.affiliate.product.query"
HOT = "aliexpress.affiliate.hotproduct.query"


def _config(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    return config


def test_hot_migration_backfills_and_enforces_run_snapshot_source(tmp_path: Path) -> None:
    path = tmp_path / "migration.sqlite3"
    config = _config(path)
    command.upgrade(config, "6d1f4a8c2e90")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO aliexpress_discovery_runs "
            "(id,profile_name,profile_fingerprint,state,started_at,finished_at,page_size,"
            "max_pages,max_results,max_api_calls,minimum_drop_percent,created_at,updated_at) "
            "VALUES (1,'sample',?,'COMPLETED',?, ?,5,1,10,2,5,?,?)",
            ("a" * 64,) + ("2026-09-23T12:00:00",) * 4,
        )
        connection.execute(
            "INSERT INTO aliexpress_discovery_query_cache "
            "(query_fingerprint,tracking_fingerprint,fetched_at,expires_at,item_count) "
            "VALUES (?,?,?,?,0)",
            ("b" * 64, "c" * 64, "2026-09-23T12:00:00", "2026-09-23T13:00:00"),
        )
        connection.execute(
            "INSERT INTO aliexpress_discovery_price_snapshots "
            "(run_id,query_fingerprint,tracking_fingerprint,product_id,price,currency,"
            "observed_at,source_operation) VALUES (1,?,?,?,100,'BRL',?,?)",
            ("b" * 64, "c" * 64, "123", "2026-09-23T12:00:00", QUERY),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT source_operation FROM aliexpress_discovery_runs WHERE id=1"
        ).fetchone() == (QUERY,)
        assert connection.execute(
            "SELECT source_operation FROM aliexpress_discovery_query_cache"
        ).fetchone() == (QUERY,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO aliexpress_discovery_price_snapshots "
                "(run_id,query_fingerprint,tracking_fingerprint,product_id,price,currency,"
                "observed_at,source_operation) VALUES (1,?,?,?,90,'BRL',?,?)",
                ("d" * 64, "e" * 64, "456", "2026-09-23T12:01:00", HOT),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE aliexpress_discovery_runs SET source_operation=? WHERE id=1", (HOT,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE aliexpress_discovery_query_cache SET source_operation='invalid'"
            )
        connection.execute(
            "INSERT INTO aliexpress_discovery_runs "
            "(id,profile_name,profile_fingerprint,source_operation,state,started_at,"
            "finished_at,page_size,max_pages,max_results,max_api_calls,minimum_drop_percent,"
            "created_at,updated_at) "
            "VALUES (2,'sample',?,?, 'COMPLETED',?,?,5,1,10,2,5,?,?)",
            ("f" * 64, HOT) + ("2026-09-23T12:00:00",) * 4,
        )
        connection.execute(
            "INSERT INTO aliexpress_discovery_price_snapshots "
            "(run_id,query_fingerprint,tracking_fingerprint,product_id,price,currency,"
            "observed_at,source_operation) VALUES (2,?,?,?,90,'BRL',?,?)",
            ("d" * 64, "e" * 64, "123", "2026-09-23T12:01:00", HOT),
        )
    with pytest.raises(
        RuntimeError, match="DISCOVERY_HOT_DATA_REQUIRE_EXPLICIT_RETENTION_DECISION"
    ):
        command.downgrade(config, "6d1f4a8c2e90")
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM aliexpress_discovery_price_snapshots WHERE run_id=2")
        connection.execute("DELETE FROM aliexpress_discovery_runs WHERE id=2")
    command.downgrade(config, "6d1f4a8c2e90")
    command.upgrade(config, "head")
    command.check(config)
