"""Durable outbox operations with conservative Telegram recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import DeliveryModel
from promo_bot.domain.enums import DeliveryState


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, internal_id: int) -> DeliveryModel | None:
        return await self.session.get(DeliveryModel, internal_id)

    async def ensure_pending(
        self,
        *,
        deal_id: int,
        idempotency_key: str,
        target_chat_id: str,
    ) -> DeliveryModel:
        await self.session.execute(
            sqlite_insert(DeliveryModel)
            .values(
                deal_id=deal_id,
                idempotency_key=idempotency_key,
                target_chat_id=target_chat_id,
                state=DeliveryState.PENDING.value,
                attempt_count=0,
            )
            .on_conflict_do_nothing(index_elements=["deal_id"])
        )
        result = await self.session.execute(
            select(DeliveryModel).where(DeliveryModel.deal_id == deal_id)
        )
        delivery = result.scalar_one()
        if delivery.idempotency_key != idempotency_key or delivery.target_chat_id != target_chat_id:
            raise ValueError("deal already has a delivery with different identity")
        return delivery

    async def claim(
        self,
        internal_id: int,
        *,
        now: datetime,
        lease_until: datetime,
        max_attempts: int,
    ) -> DeliveryModel | None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(DeliveryModel)
                .where(
                    DeliveryModel.id == internal_id,
                    DeliveryModel.attempt_count < max_attempts,
                    or_(
                        DeliveryModel.state == DeliveryState.PENDING.value,
                        and_(
                            DeliveryModel.state == DeliveryState.FAILED_RETRYABLE.value,
                            or_(
                                DeliveryModel.next_attempt_at.is_(None),
                                DeliveryModel.next_attempt_at <= now,
                            ),
                        ),
                    ),
                )
                .values(
                    state=DeliveryState.SENDING.value,
                    attempt_count=DeliveryModel.attempt_count + 1,
                    last_attempt_at=now,
                    next_attempt_at=None,
                    lease_until=lease_until,
                    error_code=None,
                    error_summary=None,
                )
            ),
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.get(internal_id)

    async def mark_sent(self, internal_id: int, *, message_id: str, sent_at: datetime) -> None:
        if not message_id.strip():
            raise ValueError("Telegram message_id is required for SENT")
        await self._finish(
            internal_id,
            state=DeliveryState.SENT,
            telegram_message_id=message_id,
            sent_at=sent_at,
        )

    async def mark_retryable(
        self,
        internal_id: int,
        *,
        next_attempt_at: datetime,
        error_code: str,
    ) -> None:
        await self._finish(
            internal_id,
            state=DeliveryState.FAILED_RETRYABLE,
            next_attempt_at=next_attempt_at,
            error_code=error_code,
        )

    async def mark_ambiguous(self, internal_id: int, *, error_code: str) -> None:
        await self._finish(
            internal_id,
            state=DeliveryState.DELIVERY_AMBIGUOUS,
            error_code=error_code,
        )

    async def mark_permanent(self, internal_id: int, *, error_code: str) -> None:
        await self._finish(
            internal_id,
            state=DeliveryState.FAILED_PERMANENT,
            error_code=error_code,
        )

    async def mark_stale_sending_ambiguous(self, *, now: datetime) -> int:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(DeliveryModel)
                .where(
                    DeliveryModel.state == DeliveryState.SENDING.value,
                    DeliveryModel.lease_until.is_not(None),
                    DeliveryModel.lease_until <= now,
                )
                .values(
                    state=DeliveryState.DELIVERY_AMBIGUOUS.value,
                    lease_until=None,
                    next_attempt_at=None,
                    error_code="DELIVERY_LEASE_EXPIRED_AMBIGUOUS",
                    error_summary=None,
                )
            ),
        )
        return result.rowcount

    async def _finish(
        self,
        internal_id: int,
        *,
        state: DeliveryState,
        next_attempt_at: datetime | None = None,
        telegram_message_id: str | None = None,
        sent_at: datetime | None = None,
        error_code: str | None = None,
    ) -> None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(DeliveryModel)
                .where(
                    DeliveryModel.id == internal_id,
                    DeliveryModel.state == DeliveryState.SENDING.value,
                )
                .values(
                    state=state.value,
                    lease_until=None,
                    next_attempt_at=next_attempt_at,
                    telegram_message_id=telegram_message_id,
                    sent_at=sent_at,
                    error_code=error_code,
                    error_summary=None,
                )
            ),
        )
        if result.rowcount != 1:
            raise ValueError("delivery is not in SENDING state")
