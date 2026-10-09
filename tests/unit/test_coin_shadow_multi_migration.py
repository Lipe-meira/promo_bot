import sqlite3

import pytest
from alembic import command

from tests.unit.test_affiliate_history_schema import config_for


def test_multi_migration_empty_roundtrip_and_delivery_exclusivity(tmp_path):
    path = tmp_path / "multi-migration.sqlite3"
    config = config_for(path)
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        assert "multi_preview_id" in {
            row[1] for row in conn.execute("PRAGMA table_info(aliexpress_coin_shadow_deliveries)")
        }
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='aliexpress_coin_shadow_multi_previews'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO aliexpress_coin_shadow_deliveries (preview_id,multi_preview_id,"
                "source_message_fingerprint,destination_fingerprint,state,attempt_count,"
                "created_at,updated_at) VALUES (1,1,?,?, 'pending',0,'2026-10-02','2026-10-02')",
                ("a" * 64, "b" * 64),
            )
    command.downgrade(config, "9b3d5e7f1a20")
    command.upgrade(config, "head")
    command.check(config)


def test_multi_upgrade_preserves_singleton_data_and_downgrade_refuses_new_records(tmp_path):
    path = tmp_path / "representative.sqlite3"
    config = config_for(path)
    command.upgrade(config, "9b3d5e7f1a20")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO aliexpress_coin_shadow_deliveries (source_message_fingerprint,"
            "destination_fingerprint,state,attempt_count,created_at,updated_at) "
            "VALUES (?,?,'uncertain',1,'2026-10-02','2026-10-02')",
            ("a" * 64, "b" * 64),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT state,preview_id,multi_preview_id FROM aliexpress_coin_shadow_deliveries"
        ).fetchall() == [("uncertain", None, None)]
        conn.execute(
            "INSERT INTO aliexpress_coin_shadow_multi_previews (source_message_fingerprint,"
            "rendered_text,content_expires_at,created_at,updated_at) "
            "VALUES (?,'synthetic','2026-10-03','2026-10-02','2026-10-02')",
            ("c" * 64,),
        )
    with pytest.raises(RuntimeError, match="COIN_MULTI_DOWNGRADE_BLOCKED_NONEMPTY"):
        command.downgrade(config, "9b3d5e7f1a20")
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM aliexpress_coin_shadow_multi_previews"
        ).fetchone() == (1,)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() != (
            "9b3d5e7f1a20",
        )


def test_downgrade_refuses_durable_multi_use_after_operational_purge(tmp_path):
    path = tmp_path / "durable-use.sqlite3"
    config = config_for(path)
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO affiliate_link_uses (id,scope,kind,state,occurred_at,operational_kind,"
            "operational_id,created_at,updated_at) VALUES ('synthetic-use','shadow','PREVIEW',"
            "'PREVIEW_READY','2026-10-02','coin-multi-preview',1,'2026-10-02','2026-10-02')"
        )
    with pytest.raises(RuntimeError, match="COIN_MULTI_DOWNGRADE_BLOCKED_NONEMPTY"):
        command.downgrade(config, "9b3d5e7f1a20")


@pytest.mark.asyncio
async def test_current_migration_passes_existing_durable_storage_validation(tmp_path, monkeypatch):
    from promo_bot.affiliate.history_context import validate_history_storage
    from promo_bot.database.migrations import upgrade_database_async
    from promo_bot.database.session import create_affiliate_shadow_database
    from promo_bot.database.shadow import shadow_database_url

    path = tmp_path / "storage.sqlite3"
    await upgrade_database_async(shadow_database_url(path))
    database = create_affiliate_shadow_database(path)
    monkeypatch.setattr(
        "promo_bot.affiliate.history_context.durable_sqlite_path", lambda _url: path.resolve()
    )
    try:
        await validate_history_storage(database, real=True)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_upgrade_preserves_real_singleton_preview_reservation_and_history(tmp_path):
    import asyncio

    from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewService
    from promo_bot.database.coin_shadow_repository import CoinShadowDeliveryRepository
    from promo_bot.database.session import create_affiliate_shadow_database
    from tests.unit.test_coin_shadow_multi_contract import NOW, A, incoming
    from tests.unit.test_coin_shadow_multi_storage import SECRET, TRACKING, Client

    path = tmp_path / "singleton-upgrade.sqlite3"
    config = config_for(path)
    await asyncio.to_thread(command.upgrade, config, "head")
    database = create_affiliate_shadow_database(path)
    try:
        gen = CoinShadowGenerationService(
            database,
            Client(),
            app_secret=SECRET,
            tracking_id=TRACKING,
            clock=lambda: NOW,
            observer_wait_seconds=0,
        )
        preview = await CoinShadowPreviewService(
            database, gen, app_secret=SECRET, clock=lambda: NOW
        ).prepare(incoming(A))
        async with database.session() as session:
            delivery, _ = await CoinShadowDeliveryRepository(session).reserve(
                preview_id=preview.preview_id, destination_fingerprint="d" * 64, now=NOW
            )
            await CoinShadowDeliveryRepository(session).mark_sending(delivery.id, now=NOW)
            await CoinShadowDeliveryRepository(session).finish(
                delivery.id, state="uncertain", now=NOW
            )
    finally:
        await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "9b3d5e7f1a20")

    def state():
        with sqlite3.connect(path) as conn:
            return {
                table: conn.execute(f"SELECT * FROM {table}").fetchall()
                for table in (
                    "aliexpress_coin_shadow_evidence",
                    "aliexpress_coin_shadow_previews",
                    "affiliate_link_generations",
                    "affiliate_link_uses",
                    "affiliate_link_use_links",
                )
            }

    before = state()
    await asyncio.to_thread(command.upgrade, config, "head")
    assert state() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT state,preview_id,multi_preview_id FROM aliexpress_coin_shadow_deliveries"
        ).fetchall() == [("uncertain", preview.preview_id, None)]
