"""Small, explicit repository boundaries for local persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import (
    ProcessedItemModel,
    ProductModel,
    SourceMessageLinkModel,
    SourceMessageModel,
    TelegramChannelCheckpointModel,
    utc_now,
)
from promo_bot.domain.enums import RelayLinkState, SourceMessageState


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    message: SourceMessageModel
    created: bool
    content_matches: bool


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_external_id(self, store: str, external_id: str) -> ProductModel | None:
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store == store,
                ProductModel.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, product: ProductModel) -> ProductModel:
        self.session.add(product)
        await self.session.flush()
        return product


class SourceMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def receive(
        self,
        *,
        platform: str,
        message_id: str,
        channel_id: str,
        occurred_at: datetime,
        original_text: str,
        links: list[dict[str, Any]],
        content_hash: str,
    ) -> ReceiveResult:
        statement = (
            sqlite_insert(SourceMessageModel)
            .values(
                platform=platform,
                message_id=message_id,
                channel_id=channel_id,
                occurred_at=occurred_at,
                original_text=original_text,
                links=links,
                content_hash=content_hash,
                processing_status=SourceMessageState.RECEIVED.value,
                attempt_count=0,
            )
            .on_conflict_do_nothing(index_elements=["platform", "message_id", "channel_id"])
        )
        result = cast(CursorResult[Any], await self.session.execute(statement))
        query = await self.session.execute(
            select(SourceMessageModel).where(
                SourceMessageModel.platform == platform,
                SourceMessageModel.message_id == message_id,
                SourceMessageModel.channel_id == channel_id,
            )
        )
        message = query.scalar_one()
        return ReceiveResult(
            message=message,
            created=result.rowcount == 1,
            content_matches=message.content_hash == content_hash,
        )

    async def get(self, internal_id: int) -> SourceMessageModel | None:
        return await self.session.get(SourceMessageModel, internal_id)

    async def claim(
        self,
        internal_id: int,
        *,
        now: datetime,
        lease_until: datetime,
        max_attempts: int,
    ) -> SourceMessageModel | None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(SourceMessageModel)
                .where(
                    SourceMessageModel.id == internal_id,
                    SourceMessageModel.attempt_count < max_attempts,
                    self._recoverable_expression(now),
                )
                .values(
                    processing_status=SourceMessageState.PROCESSING.value,
                    attempt_count=SourceMessageModel.attempt_count + 1,
                    last_attempt_at=now,
                    processing_started_at=now,
                    processing_lease_until=lease_until,
                    next_attempt_at=None,
                    error_code=None,
                    error_summary=None,
                )
            ),
        )
        if result.rowcount != 1:
            return None
        await self.session.flush()
        return await self.session.get(SourceMessageModel, internal_id)

    async def complete(self, internal_id: int, *, now: datetime) -> None:
        await self.session.execute(
            update(SourceMessageModel)
            .where(
                SourceMessageModel.id == internal_id,
                SourceMessageModel.processing_status == SourceMessageState.PROCESSING.value,
            )
            .values(
                processing_status=SourceMessageState.COMPLETED.value,
                completed_at=now,
                processing_lease_until=None,
                next_attempt_at=None,
                error_code=None,
                error_summary=None,
            )
        )

    async def renew_lease(self, internal_id: int, *, lease_until: datetime) -> None:
        await self.session.execute(
            update(SourceMessageModel)
            .where(
                SourceMessageModel.id == internal_id,
                SourceMessageModel.processing_status == SourceMessageState.PROCESSING.value,
            )
            .values(processing_lease_until=lease_until)
        )

    async def fail(
        self,
        internal_id: int,
        *,
        retryable: bool,
        max_attempts: int,
        next_attempt_at: datetime | None,
        error_code: str,
        error_summary: str | None = None,
    ) -> SourceMessageState:
        message = await self.session.get(SourceMessageModel, internal_id)
        if message is None:
            raise LookupError(f"source message {internal_id} not found")
        exhausted = message.attempt_count >= max_attempts
        state = (
            SourceMessageState.FAILED_RETRYABLE
            if retryable and not exhausted
            else SourceMessageState.FAILED_PERMANENT
        )
        await self.session.execute(
            update(SourceMessageModel)
            .where(SourceMessageModel.id == internal_id)
            .values(
                processing_status=state.value,
                processing_lease_until=None,
                next_attempt_at=(
                    next_attempt_at if state is SourceMessageState.FAILED_RETRYABLE else None
                ),
                error_code="RETRY_EXHAUSTED" if retryable and exhausted else error_code,
                error_summary=(error_summary[:500] if error_summary else None),
            )
        )
        return state

    async def mark_content_mismatch(self, internal_id: int) -> None:
        await self.session.execute(
            update(SourceMessageModel)
            .where(SourceMessageModel.id == internal_id)
            .values(
                processing_status=SourceMessageState.FAILED_PERMANENT.value,
                processing_lease_until=None,
                next_attempt_at=None,
                completed_at=None,
                error_code="CONTENT_HASH_MISMATCH",
                error_summary=None,
            )
        )

    async def defer_queue_full(self, internal_id: int) -> None:
        await self.session.execute(
            update(SourceMessageModel)
            .where(
                SourceMessageModel.id == internal_id,
                SourceMessageModel.processing_status == SourceMessageState.RECEIVED.value,
            )
            .values(error_code="QUEUE_CAPACITY_DEFERRED", error_summary=None)
        )

    async def recoverable(
        self, *, now: datetime, max_attempts: int, limit: int
    ) -> list[SourceMessageModel]:
        result = await self.session.execute(
            select(SourceMessageModel)
            .where(
                SourceMessageModel.attempt_count < max_attempts,
                self._recoverable_expression(now),
            )
            .order_by(SourceMessageModel.occurred_at, SourceMessageModel.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def mark_exhausted(self, *, max_attempts: int) -> int:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(SourceMessageModel)
                .where(
                    SourceMessageModel.attempt_count >= max_attempts,
                    SourceMessageModel.processing_status.in_(
                        [
                            SourceMessageState.RECEIVED.value,
                            SourceMessageState.PROCESSING.value,
                            SourceMessageState.FAILED_RETRYABLE.value,
                        ]
                    ),
                )
                .values(
                    processing_status=SourceMessageState.FAILED_PERMANENT.value,
                    processing_lease_until=None,
                    next_attempt_at=None,
                    error_code="RETRY_EXHAUSTED",
                    error_summary=None,
                )
            ),
        )
        return result.rowcount

    @staticmethod
    def _recoverable_expression(now: datetime) -> Any:
        return or_(
            SourceMessageModel.processing_status == SourceMessageState.RECEIVED.value,
            and_(
                SourceMessageModel.processing_status == SourceMessageState.FAILED_RETRYABLE.value,
                or_(
                    SourceMessageModel.next_attempt_at.is_(None),
                    SourceMessageModel.next_attempt_at <= now,
                ),
            ),
            and_(
                SourceMessageModel.processing_status == SourceMessageState.PROCESSING.value,
                SourceMessageModel.processing_lease_until.is_not(None),
                SourceMessageModel.processing_lease_until <= now,
            ),
        )


class SourceMessageLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_received(
        self,
        *,
        source_message_id: int,
        ordinal: int,
        source_kind: str,
        input_hash: str,
        input_url: str,
    ) -> SourceMessageLinkModel:
        await self.session.execute(
            sqlite_insert(SourceMessageLinkModel)
            .values(
                source_message_id=source_message_id,
                ordinal=ordinal,
                source_kind=source_kind,
                input_hash=input_hash,
                input_url=input_url,
                state=RelayLinkState.RECEIVED.value,
            )
            .on_conflict_do_nothing(index_elements=["source_message_id", "input_hash"])
        )
        result = await self.session.execute(
            select(SourceMessageLinkModel).where(
                SourceMessageLinkModel.source_message_id == source_message_id,
                SourceMessageLinkModel.input_hash == input_hash,
            )
        )
        return result.scalar_one()

    async def set_outcome(
        self,
        internal_id: int,
        *,
        state: RelayLinkState,
        reason_code: str,
        expanded_url: str | None = None,
        redirect_count: int = 0,
        store: str | None = None,
        external_product_id: str | None = None,
        canonical_url: str | None = None,
    ) -> None:
        await self.session.execute(
            update(SourceMessageLinkModel)
            .where(SourceMessageLinkModel.id == internal_id)
            .values(
                state=state.value,
                reason_code=reason_code,
                expanded_url=expanded_url,
                redirect_count=redirect_count,
                store=store,
                external_product_id=external_product_id,
                canonical_url=canonical_url,
            )
        )


class TelegramCheckpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, channel_id: str) -> TelegramChannelCheckpointModel | None:
        return await self.session.get(TelegramChannelCheckpointModel, channel_id)

    async def record_persisted(
        self,
        *,
        channel_id: str,
        message_id: int,
        occurred_at: datetime,
        catch_up_at: datetime | None = None,
    ) -> TelegramChannelCheckpointModel:
        statement = sqlite_insert(TelegramChannelCheckpointModel).values(
            channel_id=channel_id,
            last_persisted_message_id=message_id,
            last_persisted_at=occurred_at,
            last_catch_up_at=catch_up_at,
            status="READY",
            error_code=None,
        )
        is_newer = or_(
            TelegramChannelCheckpointModel.last_persisted_message_id.is_(None),
            statement.excluded.last_persisted_message_id
            > TelegramChannelCheckpointModel.last_persisted_message_id,
        )
        values: dict[str, Any] = {
            "last_persisted_message_id": case(
                (is_newer, statement.excluded.last_persisted_message_id),
                else_=TelegramChannelCheckpointModel.last_persisted_message_id,
            ),
            "last_persisted_at": case(
                (is_newer, statement.excluded.last_persisted_at),
                else_=TelegramChannelCheckpointModel.last_persisted_at,
            ),
            "status": "READY",
            "error_code": None,
            "updated_at": utc_now(),
        }
        if catch_up_at is not None:
            values["last_catch_up_at"] = catch_up_at
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=["channel_id"], set_=values)
        )
        result = await self.session.execute(
            select(TelegramChannelCheckpointModel).where(
                TelegramChannelCheckpointModel.channel_id == channel_id
            )
        )
        return result.scalar_one()

    async def record_failure(self, channel_id: str, error_code: str) -> None:
        checkpoint = await self.get(channel_id)
        if checkpoint is None:
            checkpoint = TelegramChannelCheckpointModel(channel_id=channel_id)
            self.session.add(checkpoint)
        checkpoint.status = "ERROR"
        checkpoint.error_code = error_code
        await self.session.flush()

    async def mark_catch_up_complete(self, channel_id: str, *, now: datetime) -> None:
        checkpoint = await self.get(channel_id)
        if checkpoint is None:
            checkpoint = TelegramChannelCheckpointModel(channel_id=channel_id)
            self.session.add(checkpoint)
        checkpoint.last_catch_up_at = now
        checkpoint.status = "READY"
        checkpoint.error_code = None
        await self.session.flush()


class ProcessedItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(
        self, store: str, external_product_id: str, variation_key: str = ""
    ) -> ProcessedItemModel | None:
        result = await self.session.execute(
            select(ProcessedItemModel).where(
                ProcessedItemModel.store == store,
                ProcessedItemModel.external_product_id == external_product_id,
                ProcessedItemModel.variation_key == variation_key,
            )
        )
        return result.scalar_one_or_none()

    async def record(
        self,
        *,
        store: str,
        external_product_id: str,
        deal_hash: str,
        variation_key: str = "",
        last_sent_at: datetime | None = None,
        last_price: Decimal | None = None,
        last_coupon: str | None = None,
        cooldown_until: datetime | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProcessedItemModel:
        item = await self.find(store, external_product_id, variation_key)
        if item is None:
            item = ProcessedItemModel(
                store=store,
                external_product_id=external_product_id,
                variation_key=variation_key,
                deal_hash=deal_hash,
            )
            self.session.add(item)
        item.deal_hash = deal_hash
        item.last_sent_at = last_sent_at
        item.last_price = last_price
        item.last_coupon = last_coupon
        item.cooldown_until = cooldown_until
        if details is not None:
            item.details = details
        await self.session.flush()
        return item
