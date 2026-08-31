"""Outbox service that never retries an ambiguous Telegram delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.delivery_repository import DeliveryRepository
from promo_bot.database.models import AffiliateLinkProofModel, DealModel, DeliveryModel
from promo_bot.database.session import Database
from promo_bot.relay.formatter import RenderedMessage


class DealMessageTransport(Protocol):
    async def send(self, rendered: RenderedMessage) -> str: ...


class DeliveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RetryableBeforeSend(DeliveryError):
    pass


class RateLimitedDelivery(DeliveryError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("TELEGRAM_RATE_LIMITED")
        self.retry_after_seconds = retry_after_seconds


class AmbiguousDelivery(DeliveryError):
    pass


class PermanentDelivery(DeliveryError):
    pass


@dataclass(frozen=True, slots=True)
class PublicationContext:
    provider_enabled: bool
    affiliate_mode: str
    credentials_valid: bool
    hourly_capacity_available: bool


def assert_publication_allowed(
    settings: EnvironmentSettings,
    context: PublicationContext,
    deal: DealModel,
    proof: AffiliateLinkProofModel,
) -> None:
    allowed = (
        context.provider_enabled
        and context.affiliate_mode == "official_api"
        and context.credentials_valid
        and context.hourly_capacity_available
        and not settings.dry_run
        and settings.publish_real_deals
        and not settings.publish_without_affiliate
        and deal.status == "READY"
        and deal.currency == "BRL"
        and deal.available is True
        and bool(deal.affiliate_link)
        and deal.affiliate_proof_id == proof.id
        and proof.provider == "shopee_official"
        and proof.official_response_validated
        and proof.generation_state == "CONFIRMED"
    )
    if not allowed:
        raise ValueError("real deal publication is blocked by safety policy")


class DealDeliveryService:
    def __init__(
        self,
        database: Database,
        transport: DealMessageTransport,
        settings: EnvironmentSettings,
        publication_context: PublicationContext,
        *,
        max_attempts: int = 3,
        lease_minutes: int = 5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.transport = transport
        self.settings = settings
        self.publication_context = publication_context
        self.max_attempts = max_attempts
        self.lease_minutes = lease_minutes
        self.clock = clock or (lambda: datetime.now(UTC))

    async def send(self, delivery_id: int, rendered: RenderedMessage) -> bool:
        now = self.clock()
        async with self.database.session() as session:
            delivery = await session.get(DeliveryModel, delivery_id)
            if delivery is None:
                raise LookupError(f"delivery {delivery_id} not found")
            deal = await session.get(DealModel, delivery.deal_id)
            if deal is None or deal.affiliate_proof_id is None:
                raise ValueError("delivery requires a deal with affiliate proof")
            proof = await session.get(AffiliateLinkProofModel, deal.affiliate_proof_id)
            if proof is None:
                raise ValueError("delivery affiliate proof not found")
            assert_publication_allowed(
                self.settings,
                self.publication_context,
                deal,
                proof,
            )
            claimed = await DeliveryRepository(session).claim(
                delivery_id,
                now=now,
                lease_until=now + timedelta(minutes=self.lease_minutes),
                max_attempts=self.max_attempts,
            )
        if claimed is None:
            return False

        try:
            message_id = await self.transport.send(rendered)
            if not message_id.strip():
                raise AmbiguousDelivery("TELEGRAM_RESPONSE_WITHOUT_MESSAGE_ID")
        except RateLimitedDelivery as exc:
            async with self.database.session() as session:
                await DeliveryRepository(session).mark_retryable(
                    delivery_id,
                    next_attempt_at=self.clock()
                    + timedelta(seconds=max(0.0, exc.retry_after_seconds)),
                    error_code=exc.code,
                )
            return False
        except RetryableBeforeSend as exc:
            async with self.database.session() as session:
                await DeliveryRepository(session).mark_retryable(
                    delivery_id,
                    next_attempt_at=self.clock() + timedelta(seconds=2),
                    error_code=exc.code,
                )
            return False
        except AmbiguousDelivery as exc:
            async with self.database.session() as session:
                await DeliveryRepository(session).mark_ambiguous(
                    delivery_id,
                    error_code=exc.code,
                )
            return False
        except PermanentDelivery as exc:
            async with self.database.session() as session:
                await DeliveryRepository(session).mark_permanent(
                    delivery_id,
                    error_code=exc.code,
                )
            return False

        async with self.database.session() as session:
            await DeliveryRepository(session).mark_sent(
                delivery_id,
                message_id=message_id,
                sent_at=self.clock(),
            )
        return True
