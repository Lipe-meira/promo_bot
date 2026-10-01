"""Single-attempt reservations, independent of the production outbox."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
from promo_bot.database.models import AffiliateShadowPreviewModel, ShadowDeliveryModel

__all__ = ["ShadowDeliveryModel", "ShadowDeliveryRepository"]


class ShadowDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def reserve(
        self, preview_id: int, destination_key: str, now: datetime
    ) -> tuple[ShadowDeliveryModel, bool]:
        source_message_id = await self.session.scalar(
            select(AffiliateShadowPreviewModel.source_message_id).where(
                AffiliateShadowPreviewModel.id == preview_id
            )
        )
        if source_message_id is None:
            raise ValueError("SHADOW_PREVIEW_NOT_FOUND")
        inserted = await self.session.scalar(
            insert(ShadowDeliveryModel)
            .values(
                preview_id=preview_id,
                source_message_id=source_message_id,
                destination_key=destination_key,
                state="pending",
                attempt_count=0,
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["source_message_id", "destination_key"])
            .returning(ShadowDeliveryModel.id)
        )
        row = (
            await self.session.execute(
                select(ShadowDeliveryModel).where(
                    ShadowDeliveryModel.source_message_id == source_message_id,
                    ShadowDeliveryModel.destination_key == destination_key,
                )
            )
        ).scalar_one()
        if inserted is not None:
            preview = await self.session.get(AffiliateShadowPreviewModel, preview_id)
            assert preview is not None
            if preview.provider == "aliexpress_official":
                history = AffiliateLinkHistoryRepository(self.session)
                ids = await history.use_generation_ids(preview.history_use_id, scope="shadow")
                use = await history.record_use(
                    scope="shadow",
                    kind="SEND",
                    generation_ids=ids,
                    source_use_id=preview.history_use_id,
                    now=now,
                    origin={"source_message_id": source_message_id},
                    destination_key=destination_key,
                    operational_kind="canonical-delivery",
                    operational_id=row.id,
                )
                row.history_use_id = use.id
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
        row = await self.session.get(ShadowDeliveryModel, internal_id)
        assert row is not None
        preview = await self.session.get(AffiliateShadowPreviewModel, row.preview_id)
        assert preview is not None
        if preview.provider == "aliexpress_official":
            await AffiliateLinkHistoryRepository(self.session).transition_send(
                row.history_use_id, now=now, state="SEND_IN_FLIGHT"
            )

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
        row = await self.session.get(ShadowDeliveryModel, internal_id)
        assert row is not None
        preview = await self.session.get(AffiliateShadowPreviewModel, row.preview_id)
        assert preview is not None
        if preview.provider == "aliexpress_official":
            await AffiliateLinkHistoryRepository(self.session).transition_send(
                row.history_use_id,
                now=now,
                state={
                    "sent": "SEND_CONFIRMED",
                    "failed_safe": "SEND_FAILED",
                    "uncertain": "SEND_UNCERTAIN",
                }[state],
                error_code=error_code,
                message_id=message_id,
            )
