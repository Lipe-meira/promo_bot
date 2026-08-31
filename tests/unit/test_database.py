from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from promo_bot.database.migrations import project_root, upgrade_database
from promo_bot.database.models import Base, ProductModel
from promo_bot.database.repositories import ProcessedItemRepository, ProductRepository
from promo_bot.database.session import Database


@pytest.mark.asyncio
async def test_product_repository_round_trip(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'repository.sqlite3').as_posix()}"
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with database.session() as session:
        repository = ProductRepository(session)
        await repository.add(
            ProductModel(
                store="kabum",
                external_id="123",
                title="Fixture",
                canonical_url="https://www.kabum.com.br/produto/123",
                currency="BRL",
            )
        )

    async with database.session() as session:
        stored = await ProductRepository(session).get_by_external_id("kabum", "123")
        assert stored is not None
        assert stored.title == "Fixture"

    await database.dispose()


@pytest.mark.asyncio
async def test_processed_item_preserves_decimal_and_utc(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'processed.sqlite3').as_posix()}"
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    timestamp = datetime.now(UTC).replace(microsecond=0)
    async with database.session() as session:
        await ProcessedItemRepository(session).record(
            store="amazon",
            external_product_id="ASIN123",
            deal_hash="fixture-hash",
            last_sent_at=timestamp,
            last_price=Decimal("199.90"),
            cooldown_until=timestamp,
        )

    async with database.session() as session:
        stored = await ProcessedItemRepository(session).find("amazon", "ASIN123")
        assert stored is not None
        assert stored.last_price == Decimal("199.90")
        assert stored.last_sent_at == timestamp
        assert stored.last_sent_at is not None and stored.last_sent_at.tzinfo is UTC

    await database.dispose()


def test_initial_migration_creates_expected_schema(tmp_path: Path) -> None:
    path = tmp_path / "migration.sqlite3"
    upgrade_database(f"sqlite+aiosqlite:///{path.as_posix()}")

    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {
        "alembic_version",
        "source_messages",
        "products",
        "deals",
        "price_history",
        "coupons",
        "processed_items",
        "source_message_links",
        "telegram_channel_checkpoints",
        "affiliate_candidates",
        "affiliate_link_proofs",
        "shopee_product_snapshots",
    } <= names


def test_relay_migration_preserves_existing_source_messages(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.sqlite3"
    root = project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(config, "c501868f1334")
    timestamp = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO source_messages "
            "(platform, message_id, channel_id, occurred_at, original_text, links, "
            "processing_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "telegram",
                "1",
                "channel",
                timestamp,
                "legacy",
                '["https://www.kabum.com.br/produto/123"]',
                "DISCOVERED",
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT original_text, content_hash, processing_status, attempt_count, links "
            "FROM source_messages"
        ).fetchone()
    assert row is not None
    assert row[0] == "legacy"
    assert len(row[1]) == 64
    assert row[2:4] == ("RECEIVED", 0)
    assert '"source": "TEXT"' in row[4]


def test_affiliate_migration_backfills_pending_and_duplicate_shopee_links(
    tmp_path: Path,
) -> None:
    path = tmp_path / "affiliate-upgrade.sqlite3"
    root = project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path.as_posix()}")
    command.upgrade(config, "8ea6f1e5c7b2")
    timestamp = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection:
        for message_id in ("1", "2"):
            connection.execute(
                "INSERT INTO source_messages "
                "(platform, message_id, channel_id, occurred_at, original_text, links, "
                "content_hash, processing_status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "telegram",
                    message_id,
                    "channel",
                    timestamp,
                    "fixture",
                    "[]",
                    message_id.zfill(64),
                    "COMPLETED",
                    1,
                    timestamp,
                    timestamp,
                ),
            )
        connection.execute(
            "INSERT INTO source_message_links "
            "(source_message_id, ordinal, source_kind, input_hash, input_url, redirect_count, "
            "store, external_product_id, canonical_url, state, reason_code, "
            "created_at, updated_at) "
            "VALUES (1, 0, 'TEXT', ?, ?, 0, 'shopee', '10:20', ?, "
            "'PENDING_AFFILIATE', 'AFFILIATE_PROVIDER_REQUIRED', ?, ?)",
            (
                "a" * 64,
                "https://shopee.com.br/product/10/20",
                "https://shopee.com.br/product/10/20",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO source_message_links "
            "(source_message_id, ordinal, source_kind, input_hash, input_url, redirect_count, "
            "store, external_product_id, canonical_url, state, reason_code, "
            "created_at, updated_at) "
            "VALUES (2, 0, 'TEXT', ?, ?, 0, 'shopee', '10:20', ?, "
            "'IGNORED', 'DUPLICATE_CANONICAL', ?, ?)",
            (
                "b" * 64,
                "https://shopee.com.br/product/10/20",
                "https://shopee.com.br/product/10/20",
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        candidates = connection.execute(
            "SELECT store, external_product_id, state FROM affiliate_candidates"
        ).fetchall()
        candidate_ids = connection.execute(
            "SELECT affiliate_candidate_id FROM source_message_links ORDER BY id"
        ).fetchall()
    assert candidates == [("shopee", "10:20", "PENDING_AFFILIATE")]
    assert candidate_ids[0][0] is not None
    assert candidate_ids[0][0] == candidate_ids[1][0]


@pytest.mark.asyncio
async def test_schema_does_not_use_float_columns() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    column_types = {
        str(column.type).upper()
        for table in Base.metadata.tables.values()
        for column in table.columns
    }
    assert not any("FLOAT" in column_type or "REAL" in column_type for column_type in column_types)
    await engine.dispose()
