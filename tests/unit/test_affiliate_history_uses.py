from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from promo_bot.database.history_models import (
    AffiliateLinkGenerationModel,
    AffiliateLinkUseLinkModel,
    AffiliateLinkUseModel,
)
from promo_bot.database.models import (
    AliExpressCoinShadowEvidenceModel,
    AliExpressCoinShadowPreviewModel,
)
from tests.unit.test_coin_shadow_delivery import make_stack


async def test_preview_and_send_are_distinct_and_started_before_fake_send(tmp_path):
    database, _client, _preview_service, preview, transport, delivery_service = await make_stack(
        tmp_path
    )
    try:

        async def send(chat, text):
            async with database.session() as session:
                uses = list(await session.scalars(select(AffiliateLinkUseModel)))
                assert {use.state for use in uses} == {"PREVIEW_READY", "SEND_IN_FLIGHT"}
            transport.sends.append((chat, text))
            return "77"

        transport.send_text = send
        outcome = await delivery_service.deliver(preview.preview_id, "private-test")
        assert outcome.status == "sent"
        assert (
            await delivery_service.deliver(preview.preview_id, "private-test")
        ).send_message_attempts == 0
        async with database.session() as session:
            uses = list(await session.scalars(select(AffiliateLinkUseModel)))
            assert {use.state for use in uses} == {"PREVIEW_READY", "SEND_CONFIRMED"}
            associations = list(await session.scalars(select(AffiliateLinkUseLinkModel)))
            assert len(associations) == 2
            assert len({link.generation_id for link in associations}) == 1
    finally:
        await database.dispose()


async def test_history_reservation_failure_prevents_any_telegram_call(tmp_path, monkeypatch):
    from promo_bot.database.history_repository import AffiliateLinkHistoryRepository

    database, _client, _preview_service, preview, transport, delivery_service = await make_stack(
        tmp_path
    )
    try:

        async def fail(*args, **kwargs):
            raise RuntimeError("synthetic persistence unavailable")

        monkeypatch.setattr(AffiliateLinkHistoryRepository, "record_use", fail)
        with pytest.raises(RuntimeError):
            await delivery_service.deliver(preview.preview_id, "private-test")
        assert transport.inspections == transport.sends == []
        async with database.session() as session:
            from promo_bot.database.models import AliExpressCoinShadowDeliveryModel

            assert await session.scalar(select(AliExpressCoinShadowDeliveryModel)) is None
    finally:
        await database.dispose()


async def test_cache_hit_has_new_use_but_no_new_generation_and_send_timeout_is_uncertain(tmp_path):
    from tests.unit.test_coin_shadow_delivery import incoming

    database, client, preview_service, preview, transport, delivery_service = await make_stack(
        tmp_path
    )
    try:
        cached = await preview_service.prepare(incoming())
        assert cached.cache_hit and client.calls == 1
        transport.send_error = TimeoutError()
        result = await delivery_service.deliver(preview.preview_id, "private-test")
        assert result.status == "uncertain"
        async with database.session() as session:
            uses = list(await session.scalars(select(AffiliateLinkUseModel)))
            assert len(uses) == 3
            assert [row.state for row in uses].count("SEND_UNCERTAIN") == 1
            assert len(list(await session.scalars(select(AffiliateLinkGenerationModel)))) == 1
            assert any(
                row.cache_hit for row in await session.scalars(select(AffiliateLinkUseLinkModel))
            )
    finally:
        await database.dispose()


async def test_legacy_preview_without_history_is_blocked_before_telegram(tmp_path):
    database, _client, _preview_service, preview, transport, delivery_service = await make_stack(
        tmp_path
    )
    try:
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowPreviewModel, preview.preview_id)
            row.history_use_id = None
        with pytest.raises(ValueError, match="AFFILIATE_HISTORY_GENERATION_LINK_MISSING"):
            await delivery_service.deliver(preview.preview_id, "private-test")
        assert transport.inspections == [] and transport.sends == []
    finally:
        await database.dispose()


async def test_operational_purge_preserves_generation_and_all_uses(tmp_path):
    database, _client, _preview_service, preview, _transport, delivery_service = await make_stack(
        tmp_path
    )
    try:
        await delivery_service.deliver(preview.preview_id, "private-test")
        async with database.session() as session:
            await session.execute(delete(AliExpressCoinShadowEvidenceModel))
        async with database.session() as session:
            assert await session.scalar(select(AliExpressCoinShadowPreviewModel)) is None
            assert (await session.scalar(select(AffiliateLinkGenerationModel))).state == "CONFIRMED"
            assert len(list(await session.scalars(select(AffiliateLinkUseModel)))) == 2
    finally:
        await database.dispose()


async def test_explicit_coin_replacement_preserves_reservation_and_never_rebinds_old_preview(
    tmp_path,
):
    from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
    from promo_bot.database.models import AliExpressCoinShadowDeliveryModel
    from tests.unit.test_coin_shadow_delivery import NOW, PROMOTION, incoming

    database, client, preview_service, old, transport, delivery = await make_stack(tmp_path)
    try:
        assert (await delivery.deliver(old.preview_id, "private-test")).status == "sent"
        async with database.session() as session:
            evidence = await session.get(AliExpressCoinShadowEvidenceModel, old.evidence_id)
            evidence.generation_id = None  # Synthetic pre-history operational record.
            preview = await session.get(AliExpressCoinShadowPreviewModel, old.preview_id)
            preview.history_use_id = None
        async with database.session() as session:
            request = await AffiliateLinkHistoryRepository(session).request_legacy_generation(
                scope="shadow", legacy_kind="coin-evidence", legacy_id=old.evidence_id, now=NOW
            )
            request_id = request.id
        new = await preview_service.prepare(incoming(), generation_request=request_id)
        assert new.preview_id > old.preview_id and client.calls == 2
        async with database.session() as session:
            assert await session.get(AliExpressCoinShadowPreviewModel, old.preview_id) is None
            reservation = await session.scalar(select(AliExpressCoinShadowDeliveryModel))
            assert reservation.preview_id is None and reservation.state == "sent"
            generation = await session.get(AffiliateLinkGenerationModel, request_id)
            assert generation.state == "CONFIRMED" and generation.generated_url == PROMOTION
            assert generation.legacy_record_snapshot["label"] == "LEGACY_NOT_REVALIDATED"
            assert generation.legacy_record_snapshot["record"]["promotion_link"] == PROMOTION
        duplicate = await delivery.deliver(new.preview_id, "private-test")
        assert duplicate.send_message_attempts == 0 and len(transport.sends) == 1
    finally:
        await database.dispose()


async def test_canonical_send_uses_original_validated_generation(tmp_path):
    from promo_bot.database.session import create_affiliate_shadow_database
    from tests.unit.test_shadow_delivery import FakeTransport as CanonicalTransport
    from tests.unit.test_shadow_delivery import deliver, seed

    path = tmp_path / "canonical-uses.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    try:
        transport = CanonicalTransport(database)
        result = await deliver(database, transport, preview_id)
        assert result["status"] == "sent"
        async with database.session() as session:
            uses = list(await session.scalars(select(AffiliateLinkUseModel)))
            assert {row.state for row in uses} == {"PREVIEW_READY", "SEND_CONFIRMED"}
            assert len(list(await session.scalars(select(AffiliateLinkGenerationModel)))) == 1
    finally:
        await database.dispose()
