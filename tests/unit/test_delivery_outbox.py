from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.delivery_repository import (
    DeliveryRepository,
    DeliveryTransitionConflict,
)
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    Base,
    DealModel,
    DeliveryModel,
    ProductModel,
)
from promo_bot.database.session import Database
from promo_bot.delivery.service import (
    AmbiguousDelivery,
    DealDeliveryService,
    PublicationContext,
    RateLimitedDelivery,
    assert_publication_allowed,
)
from promo_bot.domain.enums import DeliveryState
from promo_bot.providers.shopee.policy import PriceDisplayMode
from promo_bot.relay.formatter import RenderedMessage, render_ready_shopee_deal

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


class SuccessfulTransport:
    calls = 0

    async def send(self, rendered: RenderedMessage) -> str:
        assert rendered.button_label == "Abrir oferta"
        self.calls += 1
        return "321"


class AmbiguousTransport:
    calls = 0

    async def send(self, rendered: RenderedMessage) -> str:
        del rendered
        self.calls += 1
        raise AmbiguousDelivery("TELEGRAM_READ_TIMEOUT_AMBIGUOUS")


class RateLimitedTransport:
    def __init__(self, retry_after_seconds: float = 30) -> None:
        self.calls = 0
        self.retry_after_seconds = retry_after_seconds

    async def send(self, rendered: RenderedMessage) -> str:
        del rendered
        self.calls += 1
        raise RateLimitedDelivery(self.retry_after_seconds)


async def database_with_delivery(tmp_path: Path, name: str) -> tuple[Database, int]:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        product = ProductModel(
            store="shopee",
            external_id="10:20",
            title="Produto",
            canonical_url="https://shopee.com.br/product/10/20",
            currency="BRL",
        )
        session.add(product)
        await session.flush()
        candidate = AffiliateCandidateModel(
            store="shopee",
            external_product_id="10:20",
            variation_key="",
            canonical_url="https://shopee.com.br/product/10/20",
            state="ENRICHED",
        )
        session.add(candidate)
        await session.flush()
        proof = AffiliateLinkProofModel(
            candidate_id=candidate.id,
            provider="shopee_official",
            operation="fixture",
            requested_at=NOW,
            responded_at=NOW,
            source_external_product_id="10:20",
            canonical_url="https://shopee.com.br/product/10/20",
            short_link="https://short.example.test/fixture",
            official_endpoint_host="official.example.test",
            credential_profile_id="default",
            contract_version="fixture",
            sub_ids=[],
            generation_state="CONFIRMED",
            official_response_validated=True,
        )
        session.add(proof)
        await session.flush()
        deal = DealModel(
            product_id=product.id,
            current_price=Decimal("90"),
            final_price=Decimal("90"),
            currency="BRL",
            payment_method="UNKNOWN",
            installments=1,
            confidence="HIGH",
            score=0,
            source="fixture",
            discovery_origin="relay",
            discovered_at=NOW,
            affiliate_link="https://short.example.test/fixture",
            affiliate_proof_id=proof.id,
            status="READY",
            available=True,
        )
        session.add(deal)
        await session.flush()
        delivery = await DeliveryRepository(session).ensure_pending(
            deal_id=deal.id,
            idempotency_key="deal:1",
            target_chat_id="123",
        )
        return database, delivery.id


def rendered() -> RenderedMessage:
    return render_ready_shopee_deal(
        title="Produto",
        price=Decimal("90"),
        price_mode=PriceDisplayMode.EXACT,
        affiliate_link="https://short.example.test/fixture",
        verified_at=NOW,
    )


def enabled_settings() -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        dry_run=False,
        publish_real_deals=True,
        publish_without_affiliate=False,
        telegram_target_chat_id="123",
    )


def publication_context() -> PublicationContext:
    return PublicationContext(True, "official_api", True, True)


@pytest.mark.asyncio
async def test_success_requires_message_id_and_becomes_sent(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "sent.sqlite3")
    transport = SuccessfulTransport()

    assert await DealDeliveryService(
        database,
        transport,
        enabled_settings(),
        publication_context(),
        clock=lambda: NOW,
    ).send(delivery_id, rendered())

    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        assert delivery.state == DeliveryState.SENT.value
        assert delivery.telegram_message_id == "321"
    await database.dispose()


@pytest.mark.asyncio
async def test_ambiguous_timeout_is_never_resent_automatically(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "ambiguous.sqlite3")
    transport = AmbiguousTransport()
    service = DealDeliveryService(
        database,
        transport,
        enabled_settings(),
        publication_context(),
        clock=lambda: NOW,
    )

    assert not await service.send(delivery_id, rendered())
    assert not await service.send(delivery_id, rendered())
    assert transport.calls == 1

    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        assert delivery.state == DeliveryState.DELIVERY_AMBIGUOUS.value
        assert delivery.telegram_message_id is None
    await database.dispose()


@pytest.mark.asyncio
async def test_expired_sending_lease_becomes_ambiguous_not_pending(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "lease.sqlite3")
    async with database.session() as session:
        claimed = await DeliveryRepository(session).claim(
            delivery_id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
            max_attempts=3,
        )
        assert claimed is not None
    async with database.session() as session:
        count = await DeliveryRepository(session).mark_stale_sending_ambiguous(
            now=NOW + timedelta(minutes=2)
        )
        delivery = await session.get(DeliveryModel, delivery_id)
        assert count == 1
        assert delivery is not None
        assert delivery.state == DeliveryState.DELIVERY_AMBIGUOUS.value
    await database.dispose()


@pytest.mark.asyncio
async def test_rate_limit_uses_retry_after_without_immediate_resend(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "rate-limit.sqlite3")
    transport = RateLimitedTransport()
    service = DealDeliveryService(
        database,
        transport,
        enabled_settings(),
        publication_context(),
        clock=lambda: NOW,
    )

    assert not await service.send(delivery_id, rendered())
    assert not await service.send(delivery_id, rendered())
    assert transport.calls == 1

    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        assert delivery.state == DeliveryState.FAILED_RETRYABLE.value
        assert delivery.next_attempt_at == NOW + timedelta(seconds=30)
    await database.dispose()


@pytest.mark.asyncio
async def test_excessive_retry_after_is_capped_by_configuration(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "rate-limit-cap.sqlite3")
    transport = RateLimitedTransport(86_400)
    settings = enabled_settings().model_copy(update={"telegram_retry_after_max_seconds": 45})

    assert not await DealDeliveryService(
        database,
        transport,
        settings,
        publication_context(),
        clock=lambda: NOW,
    ).send(delivery_id, rendered())

    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        assert delivery.next_attempt_at == NOW + timedelta(seconds=45)
    await database.dispose()


@pytest.mark.asyncio
async def test_publication_rejects_link_that_differs_from_affiliate_proof(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "link-mismatch.sqlite3")
    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        deal = await session.get(DealModel, delivery.deal_id)
        assert deal is not None
        deal.affiliate_link = "https://short.example.test/different"
    transport = SuccessfulTransport()

    with pytest.raises(ValueError, match="blocked"):
        await DealDeliveryService(
            database,
            transport,
            enabled_settings(),
            publication_context(),
            clock=lambda: NOW,
        ).send(delivery_id, rendered())

    assert transport.calls == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_publication_rejects_delivery_for_another_target_chat(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "chat-mismatch.sqlite3")
    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        delivery.target_chat_id = "999"
    transport = SuccessfulTransport()

    with pytest.raises(ValueError, match="blocked"):
        await DealDeliveryService(
            database,
            transport,
            enabled_settings(),
            publication_context(),
            clock=lambda: NOW,
        ).send(delivery_id, rendered())

    assert transport.calls == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_finish_rejects_expired_delivery_lease(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "finish-expired.sqlite3")
    async with database.session() as session:
        repository = DeliveryRepository(session)
        claimed = await repository.claim(
            delivery_id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
            max_attempts=3,
        )
        assert claimed is not None
        with pytest.raises(DeliveryTransitionConflict, match="lease expired"):
            await repository.mark_sent(
                delivery_id,
                message_id="321",
                sent_at=NOW + timedelta(minutes=2),
            )

    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None
        assert delivery.state == DeliveryState.SENDING.value
    await database.dispose()


@pytest.mark.asyncio
async def test_delivery_service_rechecks_default_publication_gate(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "blocked.sqlite3")
    transport = SuccessfulTransport()
    service = DealDeliveryService(
        database,
        transport,
        EnvironmentSettings(_env_file=None),
        publication_context(),
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="blocked"):
        await service.send(delivery_id, rendered())

    assert transport.calls == 0
    async with database.session() as session:
        delivery = await session.get(DeliveryModel, delivery_id)
        assert delivery is not None and delivery.state == DeliveryState.PENDING.value
    await database.dispose()


def test_real_publication_is_blocked_by_default() -> None:
    settings = EnvironmentSettings(_env_file=None)
    deal = DealModel(
        id=1,
        product_id=1,
        current_price=Decimal("90"),
        final_price=Decimal("90"),
        currency="BRL",
        payment_method="UNKNOWN",
        installments=1,
        confidence="HIGH",
        score=0,
        source="fixture",
        discovery_origin="relay",
        discovered_at=NOW,
        affiliate_link="https://short.example.test/fixture",
        affiliate_proof_id=1,
        status="READY",
        available=True,
    )
    proof = AffiliateLinkProofModel(
        id=1,
        candidate_id=1,
        provider="shopee_official",
        operation="fixture",
        requested_at=NOW,
        responded_at=NOW,
        source_external_product_id="10:20",
        canonical_url="https://shopee.com.br/product/10/20",
        short_link="https://short.example.test/fixture",
        official_endpoint_host="official.example.test",
        credential_profile_id="default",
        contract_version="fixture",
        sub_ids=[],
        generation_state="CONFIRMED",
        official_response_validated=True,
    )
    context = PublicationContext(True, "official_api", True, True)
    delivery = DeliveryModel(
        id=1,
        deal_id=1,
        idempotency_key="deal:1",
        target_chat_id="123",
        state=DeliveryState.PENDING.value,
    )

    with pytest.raises(ValueError, match="blocked"):
        assert_publication_allowed(settings, context, deal, proof, delivery)


def test_formatter_distinguishes_exact_and_starting_at_prices() -> None:
    exact = render_ready_shopee_deal(
        title="Produto",
        price=Decimal("90"),
        price_mode=PriceDisplayMode.EXACT,
        affiliate_link="https://short.example.test/fixture",
        verified_at=NOW,
    )
    range_price = render_ready_shopee_deal(
        title="Produto",
        price=Decimal("90"),
        price_mode=PriceDisplayMode.STARTING_AT,
        affiliate_link="https://short.example.test/fixture",
        verified_at=NOW,
    )

    assert "Preço: R$ 90,00" in exact.text
    assert "A partir de: R$ 90,00" in range_price.text
    assert exact.button_label == "Abrir oferta"
    assert "canal" not in exact.text.casefold()


def test_formatter_rejects_zero_price() -> None:
    with pytest.raises(ValueError, match="positive"):
        render_ready_shopee_deal(
            title="Produto",
            price=Decimal("0"),
            price_mode=PriceDisplayMode.EXACT,
            affiliate_link="https://short.example.test/fixture",
            verified_at=NOW,
        )
