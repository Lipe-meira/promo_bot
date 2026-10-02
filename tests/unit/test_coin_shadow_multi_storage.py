from datetime import timedelta

import pytest
from sqlalchemy import select, text

from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
from promo_bot.database.models import Base
from promo_bot.database.session import create_affiliate_shadow_database
from tests.unit.test_coin_shadow_multi_contract import NOW, A, B

SECRET = "multi-fixture-secret"
TRACKING = "multi-fixture-tracking"
LINKS = {
    A: "https://s.click.aliexpress.com/e/_Aa11Bb2",
    B: "https://s.click.aliexpress.com/e/_Cc33Dd4",
}


class Client:
    def __init__(self):
        self.calls = []

    async def execute(self, operation, payload):
        source = payload["source_values"]
        self.calls.append(source)
        return {
            "code": "0",
            "aliexpress_affiliate_link_generate_response": {
                "resp_result": {
                    "resp_code": "200",
                    "result": {
                        "tracking_id": TRACKING,
                        "promotion_links": [
                            {"source_value": source, "promotion_link": LINKS[source]}
                        ],
                    },
                }
            },
        }


async def seeded(tmp_path):
    database = create_affiliate_shadow_database(tmp_path / "multi.sqlite3")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    client = Client()
    generation = CoinShadowGenerationService(
        database,
        client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: NOW,
        observer_wait_seconds=0,
    )
    rows = [await generation.generate(value) for value in (A, B)]
    return database, client, generation, rows


async def save_multi(session, rows):
    from promo_bot.database.coin_shadow_multi_repository import (
        CoinMultiOccurrenceInput,
        CoinShadowMultiPreviewRepository,
    )

    return await CoinShadowMultiPreviewRepository(session).save_ready(
        source_message_fingerprint="f" * 64,
        rendered_text="converted fixture",
        occurrences=tuple(
            CoinMultiOccurrenceInput(
                ordinal=i,
                start=i * 50,
                end=i * 50 + len(source),
                evidence_id=row.evidence_id,
                generation_id=row.generation_id,
            )
            for i, (row, source) in enumerate([(rows[0], A), (rows[1], B), (rows[0], A)])
        ),
        cache_hits={rows[0].generation_id: True, rows[1].generation_id: False},
        now=NOW,
    )


@pytest.mark.asyncio
async def test_aggregate_preview_keeps_repeated_occurrences_and_unique_history_links(tmp_path):
    database, _, _, rows = await seeded(tmp_path)
    try:
        async with database.session() as session:
            preview, created = await save_multi(session, rows)
            assert created and preview.content_expires_at == NOW + timedelta(hours=24)
            from promo_bot.database.history_repository import AffiliateLinkHistoryRepository

            ids = await AffiliateLinkHistoryRepository(session).validate_preview(preview)
            assert ids == (rows[0].generation_id, rows[1].generation_id)
            from promo_bot.database.history_models import (
                AffiliateLinkUseLinkModel,
                AffiliateLinkUseModel,
            )

            use = await session.get(AffiliateLinkUseModel, preview.history_use_id)
            assert use.origin["occurrence_map_version"] == 1
            assert [item["generation_id"] for item in use.origin["occurrences"]] == [
                ids[0],
                ids[1],
                ids[0],
            ]
            links = list(
                await session.scalars(
                    select(AffiliateLinkUseLinkModel)
                    .where(AffiliateLinkUseLinkModel.use_id == use.id)
                    .order_by(AffiliateLinkUseLinkModel.ordinal)
                )
            )
            assert [link.cache_hit for link in links] == [True, False]
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM aliexpress_coin_shadow_multi_preview_occurrences")
                )
                == 3
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_shared_reservation_purge_preserves_send_and_history(tmp_path):
    database, _, generation, rows = await seeded(tmp_path)
    try:
        from promo_bot.database.coin_shadow_repository import CoinShadowDeliveryRepository
        from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
        from promo_bot.database.models import AliExpressCoinShadowDeliveryModel

        async with database.session() as session:
            preview, _ = await save_multi(session, rows)
            row, created = await CoinShadowDeliveryRepository(session).reserve(
                preview_id=preview.id, multi=True, destination_fingerprint="d" * 64, now=NOW
            )
            assert created and row.preview_id is None and row.multi_preview_id == preview.id
            await CoinShadowDeliveryRepository(session).mark_sending(row.id, now=NOW)
            await CoinShadowDeliveryRepository(session).finish(
                row.id, state="sent", now=NOW, telegram_message_id="7"
            )
            use_ids = await AffiliateLinkHistoryRepository(session).use_generation_ids(
                row.history_use_id, scope="shadow"
            )
            assert len(use_ids) == 2
        generation.clock = lambda: NOW + timedelta(hours=25)
        await generation.generate(A)
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowDeliveryModel, row.id)
            assert row.multi_preview_id is None and row.state == "sent"
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM aliexpress_coin_shadow_multi_previews")
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM aliexpress_coin_shadow_multi_preview_occurrences")
                )
                == 0
            )
            assert (
                await AffiliateLinkHistoryRepository(session).use_generation_ids(
                    row.history_use_id, scope="shadow"
                )
                == use_ids
            )
            assert not list(await session.execute(text("PRAGMA foreign_key_check")))
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_database_rejects_both_preview_pointers(tmp_path):
    from sqlalchemy.exc import IntegrityError

    database, _, _, rows = await seeded(tmp_path)
    try:
        async with database.session() as session:
            preview, _ = await save_multi(session, rows)
        async with database.session() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO aliexpress_coin_shadow_deliveries "
                        "(preview_id,multi_preview_id,"
                        "source_message_fingerprint,destination_fingerprint,state,attempt_count,"
                        "created_at,updated_at) VALUES (1,:id,:fp,:fp,'pending',0,:now,:now)"
                    ),
                    {"id": preview.id, "fp": "a" * 64, "now": NOW.isoformat()},
                )
    finally:
        await database.dispose()
