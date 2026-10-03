from dataclasses import replace

import pytest
from sqlalchemy import select, text

from promo_bot.affiliate.aliexpress_shadow_listener import AliExpressShadowMessageProcessor
from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService
from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewService
from promo_bot.affiliate.shadow_delivery import authorize_automatic_shadow_delivery
from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.history_models import AffiliateLinkUseModel
from promo_bot.relay.queue import DurableRelayQueue
from tests.unit.test_coin_shadow_delivery import FakeTransport, config, settings
from tests.unit.test_coin_shadow_multi_contract import NOW, A, B, incoming
from tests.unit.test_coin_shadow_multi_generation import setup
from tests.unit.test_coin_shadow_multi_storage import LINKS, SECRET


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.asyncio
async def test_multi_delivery_sends_once_and_repeated_attempt_never_sends_again(
    tmp_path, uncertain
):
    database, _, _, service, budget = await setup(tmp_path)
    transport = FakeTransport(send_error=TimeoutError() if uncertain else None)
    env = settings(aliexpress_telegram_shadow_auto_delivery_enabled=True)
    cfg = config()
    auth = authorize_automatic_shadow_delivery(env, cfg, destination="private-test")
    delivery = CoinShadowDeliveryService(
        database, transport, env, cfg, app_secret=SECRET, clock=lambda: NOW
    )
    try:
        preview = await service.prepare(incoming(f"{A}\n{B}\n{A}"))
        outcome = await delivery.deliver_multi_automatic(
            preview.preview_id,
            "private-test",
            authorization=auth,
            before_send=budget.before_send_message,
        )
        assert outcome.status == ("uncertain" if uncertain else "sent")
        duplicate = await delivery.deliver_multi_automatic(
            preview.preview_id,
            "private-test",
            authorization=auth,
            before_send=budget.before_send_message,
        )
        assert duplicate.error_code == "COIN_SHADOW_DELIVERY_ALREADY_ATTEMPTED"
        assert (
            len(transport.sends) == 1
            and transport.sends[0][1] == f"{LINKS[A]}\n{LINKS[B]}\n{LINKS[A]}"
        )
        assert budget.send_messages == 1
        async with database.session() as session:
            uses = list(
                await session.scalars(
                    select(AffiliateLinkUseModel).where(AffiliateLinkUseModel.kind == "SEND")
                )
            )
            assert len(uses) == 1 and uses[0].state == (
                "SEND_UNCERTAIN" if uncertain else "SEND_CONFIRMED"
            )
            assert len(uses[0].origin["occurrences"]) == 3
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_listener_opt_in_routes_multi_exclusively_and_never_falls_back(tmp_path):
    database, client, gen, service, budget = await setup(tmp_path)

    class CanonicalForbidden:
        async def process(self, *_a):
            pytest.fail("canonical resolver entered")

        async def convert(self, *_a):
            pytest.fail("canonical converter entered")

    transport = FakeTransport()
    env, cfg = settings(aliexpress_telegram_shadow_auto_delivery_enabled=True), config()
    auth = authorize_automatic_shadow_delivery(env, cfg, destination="private-test")
    delivery = CoinShadowDeliveryService(
        database, transport, env, cfg, app_secret=SECRET, clock=lambda: NOW
    )
    try:
        processor = AliExpressShadowMessageProcessor(
            database,
            CanonicalForbidden(),
            CanonicalForbidden(),
            budget,
            clock=lambda: NOW,
            destination="private-test",
            delivery=CanonicalForbidden(),
            delivery_authorization=auth,
            coin_preview=CoinShadowPreviewService(
                database, gen, app_secret=SECRET, clock=lambda: NOW
            ),
            coin_delivery=delivery,
            coin_multi_preview=service,
        )
        texts = [
            f"{A}\n{B}",
            f"{A}\nhttps://example.com/x",
            "https://pt.aliexpress.com/item/1.html\nhttps://pt.aliexpress.com/item/2.html",
        ]
        for i, content in enumerate(texts):
            row = await DurableRelayQueue(database, TelegramRelayConfig()).persist_without_enqueue(
                replace(incoming(content), message_id=i + 1)
            )
            await processor.process(row.internal_id)
            await processor.process(row.internal_id)
        assert client.calls == [A, B] and len(transport.sends) == 1
        assert budget.previews_created == budget.deliveries_sent == 1
        assert budget.rejected == 2
        assert budget.coin_multi["occurrences_admitted"] == 2
        async with database.session() as session:
            # The production Delivery table is also the current outbox storage.
            for table in ("deals", "deliveries", "affiliate_candidates"):
                assert await session.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_reservation_persistence_failure_prevents_any_send(tmp_path, monkeypatch):
    database, _, _, service, budget = await setup(tmp_path)
    env, cfg = settings(aliexpress_telegram_shadow_auto_delivery_enabled=True), config()
    auth = authorize_automatic_shadow_delivery(env, cfg, destination="private-test")
    transport = FakeTransport()
    delivery = CoinShadowDeliveryService(
        database, transport, env, cfg, app_secret=SECRET, clock=lambda: NOW
    )
    try:
        preview = await service.prepare(incoming(f"{A}\n{B}"))

        async def fail(*_a, **_kw):
            raise OSError("synthetic storage failure")

        monkeypatch.setattr(
            "promo_bot.database.coin_shadow_repository.CoinShadowDeliveryRepository.reserve", fail
        )
        with pytest.raises(OSError):
            await delivery.deliver_multi_automatic(
                preview.preview_id,
                "private-test",
                authorization=auth,
                before_send=budget.before_send_message,
            )
        assert transport.sends == transport.inspections == [] and budget.send_messages == 0
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_opt_in_singleton_with_depleted_budget_never_creates_generation_claim(tmp_path):
    database, client, gen, service, budget = await setup(tmp_path, calls=1)
    env, cfg = settings(aliexpress_telegram_shadow_auto_delivery_enabled=True), config()
    auth = authorize_automatic_shadow_delivery(env, cfg, destination="private-test")
    transport = FakeTransport()

    class CanonicalForbidden:
        async def process(self, *_a):
            pytest.fail("short reached canonical path")

        async def convert(self, *_a):
            pytest.fail("short reached canonical conversion")

    try:
        await gen.generate(A)
        processor = AliExpressShadowMessageProcessor(
            database,
            CanonicalForbidden(),
            CanonicalForbidden(),
            budget,
            clock=lambda: NOW,
            destination="private-test",
            delivery=CanonicalForbidden(),
            delivery_authorization=auth,
            coin_preview=CoinShadowPreviewService(
                database, gen, app_secret=SECRET, clock=lambda: NOW
            ),
            coin_delivery=CoinShadowDeliveryService(
                database, transport, env, cfg, app_secret=SECRET, clock=lambda: NOW
            ),
            coin_multi_preview=service,
        )
        source = await DurableRelayQueue(database, TelegramRelayConfig()).persist_without_enqueue(
            incoming(B)
        )
        await processor.process(source.internal_id)
        assert budget.rejection_codes == ["ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT"]
        assert client.calls == [A] and not transport.sends
        async with database.session() as session:
            assert (
                await session.scalar(text("SELECT COUNT(*) FROM affiliate_link_generations")) == 1
            )
            assert (
                await session.scalar(text("SELECT COUNT(*) FROM aliexpress_coin_shadow_evidence"))
                == 1
            )
    finally:
        await database.dispose()
