"""Regression tests for the final audit-boundary review; fake wire only."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from promo_bot.affiliate.history_context import AuditedGenerationCall, audited_generation_call
from promo_bot.database.history_models import AffiliateLinkGenerationModel, AffiliateLinkUseModel
from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
from promo_bot.database.models import (
    AliExpressCoinShadowDeliveryModel,
    AliExpressCoinShadowPreviewModel,
)
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from tests.unit.test_affiliate_history_generation import NOW, SHORT, make_database
from tests.unit.test_coin_shadow_delivery import incoming, make_stack


@pytest.mark.parametrize("mismatch", [False, True])
async def test_direct_generation_transport_checks_payload_and_durable_store_before_wire(
    tmp_path, mismatch
):
    database = await make_database(tmp_path)
    payload = {"source_values": SHORT, "tracking_id": "fixture", "promotion_link_type": "0"}
    token = str(uuid4())
    async with database.session() as session:
        row = await AffiliateLinkHistoryRepository(session).prepare(
            scope="shadow",
            identity_key="direct-fixture",
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            lease_token=token,
            call_id=str(uuid4()),
            call_ordinal=0,
            input_fingerprint="a" * 64,
            tracking_fingerprint="b" * 64,
            key_fingerprint="c" * 64,
        )
    sent = []
    prepared = AliExpressTopRequestBuilder("fixture", "fixture").prepare(
        LINK_GENERATE, {**payload, "source_values": "other"} if mismatch else payload
    )
    call = AuditedGenerationCall(database, (row.id,), (token,), payload, lambda: NOW)
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: sent.append(request.method) or httpx.Response(200, json={})
            )
        ) as http:
            with audited_generation_call(call):
                code = "CONTEXT_INVALID" if mismatch else "STORAGE_NOT_DURABLE"
                with pytest.raises(ValueError, match=code):
                    await AliExpressHttpTransport(http, max_attempts=1).execute(prepared)
        assert sent == []
        async with database.session() as session:
            assert (await session.get(AffiliateLinkGenerationModel, row.id)).state == "PREPARED"
    finally:
        await database.dispose()


async def test_shorter_canonical_ttl_does_not_reconcile_stale_cache_or_leave_prepared(tmp_path):
    from tests.unit.test_aliexpress_conversion import (
        CANONICAL,
        conversion_service,
        link_response,
        persist_and_process,
    )
    from tests.unit.test_aliexpress_conversion import make_database as canonical_database

    database = await canonical_database(tmp_path, "ttl.sqlite3")
    message_id = await persist_and_process(database, 1, CANONICAL)
    sent = []
    service, http = conversion_service(
        database,
        httpx.MockTransport(
            lambda request: sent.append(request.method) or httpx.Response(200, json=link_response())
        ),
        clock=lambda: NOW,
    )
    try:
        await service.convert(message_id)
        service.clock = lambda: NOW + timedelta(hours=2)
        service.proof_ttl = timedelta(hours=1)
        result = await service.convert(message_id)
        assert not result.cache_hit and sent == ["POST", "POST"]
        async with database.session() as session:
            assert {
                row.state for row in await session.scalars(select(AffiliateLinkGenerationModel))
            } == {"CONFIRMED"}
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "EXPLICIT_OUTPUT"),
        ("operational_kind", "other-preview"),
        ("operational_id", 999),
        ("origin", {"source_message_fingerprint": "wrong"}),
    ],
)
async def test_preview_pointer_cannot_borrow_a_different_use(tmp_path, field, value):
    database, _client, _service, preview, _transport, _delivery = await make_stack(tmp_path)
    try:
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowPreviewModel, preview.preview_id)
            use = await session.get(AffiliateLinkUseModel, row.history_use_id)
            setattr(use, field, value)
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowPreviewModel, preview.preview_id)
            with pytest.raises(ValueError, match="GENERATION_LINK_INVALID"):
                await AffiliateLinkHistoryRepository(session).validate_preview(row)
    finally:
        await database.dispose()


async def test_cached_coin_send_inherits_cache_fact(tmp_path):
    from dataclasses import replace

    from promo_bot.database.history_models import AffiliateLinkUseLinkModel

    database, client, service, _preview, _transport, delivery = await make_stack(tmp_path)
    try:
        cached = await service.prepare(replace(incoming(), message_id=78))
        assert cached.cache_hit and client.calls == 1
        assert (await delivery.deliver(cached.preview_id, "private-test")).status == "sent"
        async with database.session() as session:
            row = await session.scalar(select(AliExpressCoinShadowDeliveryModel))
            link = await session.scalar(
                select(AffiliateLinkUseLinkModel).where(
                    AffiliateLinkUseLinkModel.use_id == row.history_use_id
                )
            )
            assert link.cache_hit is True
    finally:
        await database.dispose()


async def test_send_pointer_cannot_mutate_another_reservation(tmp_path):
    from promo_bot.database.coin_shadow_repository import CoinShadowDeliveryRepository

    database, _client, _service, preview, _transport, _delivery = await make_stack(tmp_path)
    try:
        async with database.session() as session:
            repository = CoinShadowDeliveryRepository(session)
            first, _ = await repository.reserve(
                preview_id=preview.preview_id, destination_fingerprint="a" * 64, now=NOW
            )
            second, _ = await repository.reserve(
                preview_id=preview.preview_id, destination_fingerprint="b" * 64, now=NOW
            )
            first.history_use_id = second.history_use_id
            first_id, second_use_id = first.id, second.history_use_id
        with pytest.raises(ValueError, match="USE_OWNER_INVALID"):
            async with database.session() as session:
                await CoinShadowDeliveryRepository(session).mark_sending(first_id, now=NOW)
        async with database.session() as session:
            assert (
                await session.get(AffiliateLinkUseModel, second_use_id)
            ).state == "SEND_RESERVED"
            assert (
                await session.get(AliExpressCoinShadowDeliveryModel, first_id)
            ).state == "pending"
    finally:
        await database.dispose()


async def test_global_coin_purge_never_erases_invalid_expired_pointer(tmp_path):
    from promo_bot.database.coin_shadow_repository import CoinShadowEvidenceRepository
    from promo_bot.database.models import AliExpressCoinShadowEvidenceModel
    from tests.unit.test_coin_shadow_delivery import NOW as COIN_NOW
    from tests.unit.test_coin_shadow_delivery import PROMOTION

    database, _client, _service, preview, _transport, _delivery = await make_stack(tmp_path)
    try:
        async with database.session() as session:
            history = AffiliateLinkHistoryRepository(session)
            token = str(uuid4())
            generation = await history.prepare(
                scope="shadow",
                identity_key="coin:other-identity",
                now=COIN_NOW,
                lease_until=COIN_NOW + timedelta(minutes=5),
                lease_token=token,
                call_id=str(uuid4()),
                call_ordinal=0,
                input_fingerprint="d" * 64,
                tracking_fingerprint="e" * 64,
                key_fingerprint="f" * 64,
            )
            await history.start_call((generation.id,), now=COIN_NOW, lease_tokens=(token,))
            await session.refresh(generation)
            await history.confirm(
                generation.id,
                now=COIN_NOW,
                generated_url=PROMOTION,
                expires_at=COIN_NOW + timedelta(hours=24),
                contract_version="coin-short-v1",
                correlation_mode="EXACT_SOURCE",
                validation_facts={"tracking_exact": True},
            )
            evidence = await session.get(AliExpressCoinShadowEvidenceModel, preview.evidence_id)
            evidence.generation_id = generation.id
        with pytest.raises(ValueError, match="GENERATION_LINK_INVALID"):
            async with database.session() as session:
                await CoinShadowEvidenceRepository(session).claim(
                    input_fingerprint="c" * 64,
                    tracking_fingerprint="b" * 64,
                    promotion_link_type=0,
                    now=COIN_NOW + timedelta(days=2),
                    lease_until=COIN_NOW + timedelta(days=3),
                )
        async with database.session() as session:
            assert (
                await session.get(AliExpressCoinShadowEvidenceModel, preview.evidence_id)
                is not None
            )
            assert (
                await session.get(AliExpressCoinShadowPreviewModel, preview.preview_id) is not None
            )
    finally:
        await database.dispose()
