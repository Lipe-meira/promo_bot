from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from promo_bot.database.models import Base

TABLES = {
    "affiliate_link_generations",
    "affiliate_link_uses",
    "affiliate_link_use_links",
}


def config_for(path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    return config


def test_history_schema_has_no_raw_tracking_or_response_and_restricts_internal_deletion() -> None:
    assert TABLES <= set(Base.metadata.tables)
    for name in TABLES:
        table = Base.metadata.tables[name]
        assert not {"tracking_id", "app_secret", "raw_response", "signature"} & set(table.c.keys())
        assert all(fk.ondelete == "RESTRICT" for fk in table.foreign_keys)


def test_history_migration_empty_roundtrip_and_nonempty_downgrade_refusal(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    config = config_for(path)
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert TABLES <= tables
    command.downgrade(config, "7e2b9c4d5a10")
    command.upgrade(config, "head")
    command.check(config)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO affiliate_link_generations "
            "(id,platform,provider,operation,scope,state,identity_key,created_at,updated_at,"
            "attribution_unverified,tracking_confirmed,origin_missing_reason) "
            "VALUES ('request-1','aliexpress','official_api','link.generate','shadow',"
            "'REQUESTED','identity-1','2026-10-01','2026-10-01',1,0,'NOT_PROVIDED')"
        )
    with pytest.raises(RuntimeError, match="AFFILIATE_HISTORY_DOWNGRADE_BLOCKED_NONEMPTY"):
        command.downgrade(config, "7e2b9c4d5a10")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM affiliate_link_generations").fetchone() == (
            1,
        )
        assert TABLES <= {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}


def test_history_upgrade_refuses_existing_orphans_without_ddl(tmp_path: Path) -> None:
    path = tmp_path / "orphan.sqlite3"
    config = config_for(path)
    command.upgrade(config, "7e2b9c4d5a10")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO affiliate_link_proofs "
            "(candidate_id,provider,operation,requested_at,responded_at,source_external_product_id,"
            "canonical_url,short_link,official_endpoint_host,credential_profile_id,contract_version,"
            "sub_ids,created_at,updated_at,generation_state,official_response_validated) "
            "VALUES (999,'aliexpress_official','link.generate','2026-10-01','2026-10-01',"
            "'1','legacy','legacy','fixture','fixture','fixture','[]','2026-10-01',"
            "'2026-10-01','CONFIRMED',1)"
        )
    with pytest.raises(RuntimeError, match="AFFILIATE_HISTORY_FOREIGN_KEY_INVALID"):
        command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert not TABLES & {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "7e2b9c4d5a10",
        )


def test_history_snapshot_and_confirmed_facts_cannot_be_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "immutable.sqlite3"
    command.upgrade(config_for(path), "head")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO affiliate_link_generations "
            "(id,platform,provider,operation,scope,state,identity_key,created_at,updated_at,"
            "attribution_unverified,tracking_confirmed,origin_missing_reason,"
            "legacy_record_snapshot) "
            "VALUES ('request-1','aliexpress','official_api','link.generate','shadow',"
            "'REQUESTED','identity-1','2026-10-01','2026-10-01',1,0,'NOT_PROVIDED',"
            '\'{"label":"LEGACY_NOT_REVALIDATED"}\')'
        )
        with pytest.raises(sqlite3.IntegrityError, match="AFFILIATE_HISTORY_SNAPSHOT_IMMUTABLE"):
            connection.execute("UPDATE affiliate_link_generations SET legacy_record_snapshot='{}'")
        connection.execute(
            "UPDATE affiliate_link_generations SET state='CONFIRMED', generated_url='synthetic',"
            "generated_at='2026-10-01', tracking_confirmed=1, contract_version='synthetic',"
            "correlation_mode='synthetic', validation_facts='{}', call_started_at='2026-10-01'"
        )
        with pytest.raises(sqlite3.IntegrityError, match="AFFILIATE_HISTORY_CONFIRMED_IMMUTABLE"):
            connection.execute("UPDATE affiliate_link_generations SET generated_url='replacement'")


def test_database_disallows_two_unfinished_generations_for_one_identity(tmp_path: Path) -> None:
    path = tmp_path / "one-claim.sqlite3"
    command.upgrade(config_for(path), "head")
    with sqlite3.connect(path) as connection:
        sql = (
            "INSERT INTO affiliate_link_generations "
            "(id,platform,provider,operation,scope,state,identity_key,created_at,updated_at,"
            "attribution_unverified,tracking_confirmed) "
            "VALUES (?,'aliexpress','official_api','link.generate','shadow',"
            "'PREPARED','same-identity','2026-10-01','2026-10-01',1,0)"
        )
        connection.execute(sql, ("attempt-a",))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, ("attempt-b",))
