"""Single-attempt reservations, independent of the production outbox."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import ShadowDeliveryModel

__all__ = ["ShadowDeliveryModel", "ShadowDeliveryRepository"]


class ShadowDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def reserve(
        self, preview_id: int, destination_key: str, now: datetime
    ) -> tuple[ShadowDeliveryModel, bool]:
        inserted = await self.session.scalar(
            insert(ShadowDeliveryModel)
            .values(
                preview_id=preview_id,
                destination_key=destination_key,
                state="pending",
                attempt_count=0,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["preview_id", "destination_key"])
            .returning(ShadowDeliveryModel.id)
        )
        row = (
            await self.session.execute(
                select(ShadowDeliveryModel).where(
                    ShadowDeliveryModel.preview_id == preview_id,
                    ShadowDeliveryModel.destination_key == destination_key,
                )
            )
        ).scalar_one()
        return row, inserted is not None

    async def mark_sending(self, internal_id: int, now: datetime) -> None:
        result = await self.session.scalar(
            update(ShadowDeliveryModel)
            .where(
                ShadowDeliveryModel.id == internal_id,
                ShadowDeliveryModel.state == "pending",
                ShadowDeliveryModel.attempt_count == 0,
            )
            .values(state="sending", attempt_count=1, updated_at=now)
            .returning(ShadowDeliveryModel.id)
        )
        if result is None:
            raise ValueError("SHADOW_DELIVERY_TRANSITION_CONFLICT")

    async def finish(
        self,
        internal_id: int,
        state: str,
        now: datetime,
        *,
        error_code: str | None = None,
        message_id: str | None = None,
    ) -> None:
        if state not in {"sent", "failed_safe", "uncertain"}:
            raise ValueError("SHADOW_DELIVERY_STATE_INVALID")
        allowed = ("pending", "sending") if state == "failed_safe" else ("sending",)
        result = await self.session.scalar(
            update(ShadowDeliveryModel)
            .where(
                ShadowDeliveryModel.id == internal_id,
                ShadowDeliveryModel.state.in_(allowed),
            )
            .values(
                state=state,
                finished_at=now,
                updated_at=now,
                error_code=error_code,
                telegram_message_id=message_id,
            )
            .returning(ShadowDeliveryModel.id)
        )
        if result is None:
            raise ValueError("SHADOW_DELIVERY_TRANSITION_CONFLICT")
