from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from promo_bot.database.migrations import upgrade_database
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
    } <= names


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
