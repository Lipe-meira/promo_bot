from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from promo_bot.database.models import AffiliateShadowPreviewLinkModel, Base
from promo_bot.database.repositories import (
    AffiliateShadowPreviewLinkInput,
    AffiliateShadowPreviewRepository,
)
from promo_bot.database.session import Database, create_affiliate_shadow_database

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_shadow_preview_content_expires_but_metadata_remains(tmp_path: Path) -> None:
    database = create_affiliate_shadow_database(tmp_path / "shadow.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with database.session() as session:
            repository = AffiliateShadowPreviewRepository(session)
            preview = await repository.save_ready(
                provider="aliexpress_official",
                store="aliexpress",
                source_message_id=11,
                affiliate_proof_id=22,
                replacement_count=1,
                cache_hit=False,
                affiliate_host="s.click.aliexpress.com",
                rendered_text="Oferta convertida https://s.click.aliexpress.com/e/secret",
                affiliate_link="https://s.click.aliexpress.com/e/secret",
                created_at=NOW,
                content_ttl=timedelta(hours=24),
            )
            preview_id = preview.id

        async with database.session() as session:
            repository = AffiliateShadowPreviewRepository(session)
            listed = await repository.list_metadata(limit=10)
            visible = await repository.get(preview_id, include_content=True, now=NOW)

        assert len(listed) == 1
        assert not hasattr(listed[0], "rendered_text")
        assert not hasattr(listed[0], "affiliate_link")
        assert visible is not None
        assert visible.rendered_text is not None
        assert visible.affiliate_link is not None

        async with database.session() as session:
            purged = await AffiliateShadowPreviewRepository(session).purge_expired_content(
                now=NOW + timedelta(hours=24)
            )
        assert purged == 1

        async with database.session() as session:
            repository = AffiliateShadowPreviewRepository(session)
            expired = await repository.get(
                preview_id,
                include_content=True,
                now=NOW + timedelta(hours=24),
            )
            metadata = await repository.list_metadata(limit=10)

        assert expired is not None
        assert expired.rendered_text is None
        assert expired.affiliate_link is None
        assert expired.content_available is False
        assert len(metadata) == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_shadow_preview_repository_rejects_main_database_session(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'main.sqlite3').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with database.session() as session:
            with pytest.raises(ValueError, match="AFFILIATE_SHADOW_DATABASE_REQUIRED"):
                AffiliateShadowPreviewRepository(session)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_shadow_preview_persists_ordered_generic_link_correlations(tmp_path: Path) -> None:
    database = create_affiliate_shadow_database(tmp_path / "shadow.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with database.session() as session:
            preview = await AffiliateShadowPreviewRepository(session).save_ready(
                provider="aliexpress_official",
                store="aliexpress",
                source_message_id=11,
                affiliate_proof_id=22,
                replacement_count=3,
                cache_hit=False,
                affiliate_host="s.click.aliexpress.com",
                rendered_text="fixture",
                affiliate_link="https://s.click.aliexpress.com/e/first",
                created_at=NOW,
                content_ttl=timedelta(hours=24),
                link_correlations=(
                    AffiliateShadowPreviewLinkInput(
                        source_message_link_id=31,
                        affiliate_proof_id=22,
                        ordinal=0,
                        occurrence_count=2,
                        cache_hit=True,
                    ),
                    AffiliateShadowPreviewLinkInput(
                        source_message_link_id=32,
                        affiliate_proof_id=23,
                        ordinal=1,
                        occurrence_count=1,
                        cache_hit=False,
                    ),
                ),
            )
            preview_id = preview.id

        async with database.session() as session:
            rows = list(
                (
                    await session.execute(
                        select(AffiliateShadowPreviewLinkModel).order_by(
                            AffiliateShadowPreviewLinkModel.ordinal
                        )
                    )
                ).scalars()
            )

        assert [(row.preview_id, row.source_message_link_id) for row in rows] == [
            (preview_id, 31),
            (preview_id, 32),
        ]
        assert [(row.affiliate_proof_id, row.occurrence_count, row.cache_hit) for row in rows] == [
            (22, 2, True),
            (23, 1, False),
        ]
    finally:
        await database.dispose()
