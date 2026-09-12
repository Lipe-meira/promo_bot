from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from promo_bot.database.migrations import project_root

PREVIOUS_REVISION = "f7a29b6c103e"


def _config(path: Path) -> Config:
    config = Config(str(project_root() / "alembic.ini"))
    config.set_main_option("script_location", str(project_root() / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    return config


def _seed_legacy_shadow_rows(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        timestamp = "2026-09-11 12:00:00"
        connection.execute(
            "INSERT INTO source_messages "
            "(id,platform,message_id,channel_id,occurred_at,original_text,links,content_hash,"
            "processing_status,attempt_count,created_at,updated_at) "
            "VALUES (1,'telegram','10','-1001',?,'fixture','[]','hash','COMPLETED',0,?,?)",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO affiliate_candidates "
            "(id,store,external_product_id,variation_key,canonical_url,state,attempt_count,"
            "created_at,updated_at) VALUES "
            "(2,'aliexpress','1005001','','https://www.aliexpress.com/item/1005001.html',"
            "'AFFILIATE_GENERATED',0,?,?)",
            (timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO source_message_links "
            "(id,source_message_id,ordinal,source_kind,input_hash,input_url,redirect_count,"
            "store,external_product_id,canonical_url,state,affiliate_candidate_id,created_at,"
            "updated_at) VALUES "
            "(3,1,0,'TEXT','link-hash','https://www.aliexpress.com/item/1005001.html',0,"
            "'aliexpress','1005001','https://www.aliexpress.com/item/1005001.html',"
            "'PENDING_AFFILIATE',2,?,?)",
            (timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO affiliate_link_proofs "
            "(id,candidate_id,provider,operation,requested_at,responded_at,"
            "source_external_product_id,canonical_url,short_link,official_endpoint_host,"
            "credential_profile_id,contract_version,sub_ids,generation_state,"
            "official_response_validated,created_at,updated_at) VALUES "
            "(4,2,'aliexpress_official','aliexpress.affiliate.link.generate',?,?,"
            "'1005001','https://www.aliexpress.com/item/1005001.html',"
            "'https://s.click.aliexpress.com/e/fixture','api-sg.aliexpress.com','configured',"
            "'fixture','[]','CONFIRMED',1,?,?)",
            (timestamp, timestamp, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO affiliate_shadow_previews "
            "(id,source_message_id,affiliate_proof_id,provider,store,status,replacement_count,"
            "cache_hit,affiliate_host,rendered_text,affiliate_link,content_expires_at,created_at,"
            "updated_at) VALUES "
            "(5,1,4,'aliexpress_official','aliexpress','READY',2,0,'s.click.aliexpress.com',"
            "'fixture','https://s.click.aliexpress.com/e/fixture',?,?,?)",
            (timestamp, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO affiliate_shadow_deliveries "
            "(id,preview_id,destination_key,state,attempt_count,created_at,updated_at) "
            "VALUES (6,5,'destination','sent',1,?,?)",
            (timestamp, timestamp),
        )


def test_multi_link_shadow_migration_backfills_and_enforces_identity(tmp_path: Path) -> None:
    path = tmp_path / "shadow.sqlite3"
    config = _config(path)
    command.upgrade(config, PREVIOUS_REVISION)
    _seed_legacy_shadow_rows(path)

    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        source_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_messages)")
        }
        delivery_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(affiliate_shadow_deliveries)")
        }
        assert "surface_metadata" in source_columns
        assert "source_message_id" in delivery_columns
        assert json.loads(
            connection.execute("SELECT surface_metadata FROM source_messages").fetchone()[0]
        ) == {"legacy_unknown": True}
        assert connection.execute(
            "SELECT preview_id,source_message_link_id,affiliate_proof_id,ordinal,"
            "occurrence_count,cache_hit FROM affiliate_shadow_preview_links"
        ).fetchall() == [(5, 3, 4, 0, 2, 0)]
        assert connection.execute(
            "SELECT source_message_id FROM affiliate_shadow_deliveries"
        ).fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO affiliate_shadow_deliveries "
                "(preview_id,source_message_id,destination_key,state,attempt_count,created_at,"
                "updated_at) VALUES (5,1,'destination','pending',0,'2026-09-11','2026-09-11')"
            )

    command.downgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(path) as connection:
        assert not connection.execute(
            "PRAGMA table_info(affiliate_shadow_preview_links)"
        ).fetchall()
        assert "surface_metadata" not in {
            row[1] for row in connection.execute("PRAGMA table_info(source_messages)")
        }
        assert "source_message_id" not in {
            row[1] for row in connection.execute("PRAGMA table_info(affiliate_shadow_deliveries)")
        }
