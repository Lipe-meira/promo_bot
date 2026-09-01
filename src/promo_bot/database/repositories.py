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
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    DealModel,
    PriceHistoryModel,
    ProcessedItemModel,
    ProductModel,
    ShopeeProductSnapshotModel,
    SourceMessageLinkModel,
    SourceMessageModel,
    TelegramChannelCheckpointModel,
    utc_now,
)
from promo_bot.domain.enums import (
    AffiliateCandidateState,
    RelayLinkState,
    SourceMessageState,
    Store,
)
from promo_bot.stores.urls import canonicalize_store_url


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    message: SourceMessageModel
    created: bool
    content_matches: bool


class AffiliateCandidateTransitionConflict(RuntimeError):
    """A conditional candidate transition lost ownership or its lease expired."""


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

    async def upsert_shopee(
        self,
        *,
        external_id: str,
        title: str,
        canonical_url: str,
        currency: str,
        image_url: str | None,
        seller: str | None,
    ) -> ProductModel:
        product = await self.get_by_external_id("shopee", external_id)
        if product is None:
            product = ProductModel(
                store="shopee",
                external_id=external_id,
                title=title,
                canonical_url=canonical_url,
                currency=currency,
            )
            self.session.add(product)
        product.title = title
        product.canonical_url = canonical_url
        product.currency = currency
        product.image_url = image_url
        product.seller = seller
        await self.session.flush()
        return product


class ShopeeOfferRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_proof(
        self,
        *,
        candidate_id: int,
        provider: str,
        operation: str,
        requested_at: datetime,
        responded_at: datetime,
        source_external_product_id: str,
        canonical_url: str,
        short_link: str,
        official_endpoint_host: str,
        credential_profile_id: str,
        contract_version: str,
        sub_ids: list[str],
    ) -> AffiliateLinkProofModel:
        proof = AffiliateLinkProofModel(
            candidate_id=candidate_id,
            provider=provider,
            operation=operation,
            requested_at=requested_at,
            responded_at=responded_at,
            source_external_product_id=source_external_product_id,
            canonical_url=canonical_url,
            short_link=short_link,
            official_endpoint_host=official_endpoint_host,
            credential_profile_id=credential_profile_id,
            contract_version=contract_version,
            sub_ids=sub_ids,
            generation_state="CONFIRMED",
            official_response_validated=True,
        )
        self.session.add(proof)
        await self.session.flush()
        return proof

    async def add_snapshot(
        self,
        *,
        candidate_id: int,
        product_id: int,
        shop_id: str,
        item_id: str,
        selected_variation_id: str | None,
        price_min: Decimal,
        price_max: Decimal,
        selected_price: Decimal | None,
        currency: str,
        available: bool,
        selected_variation_available: bool | None,
        range_semantics_confirmed: bool,
        official_image_url: str | None,
        queried_at: datetime,
    ) -> ShopeeProductSnapshotModel:
        snapshot = ShopeeProductSnapshotModel(
            candidate_id=candidate_id,
            product_id=product_id,
            shop_id=shop_id,
            item_id=item_id,
            selected_variation_id=selected_variation_id,
            price_min=price_min,
            price_max=price_max,
            selected_price=selected_price,
            currency=currency,
            available=available,
            selected_variation_available=selected_variation_available,
            range_semantics_confirmed=range_semantics_confirmed,
            official_image_url=official_image_url,
            queried_at=queried_at,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def add_ready_deal(
        self,
        *,
        product_id: int,
        proof_id: int,
        display_price: Decimal,
        price_min: Decimal,
        price_max: Decimal,
        selected_price: Decimal | None,
        price_display_mode: str,
        variation_id: str | None,
        currency: str,
        affiliate_link: str,
        discovered_at: datetime,
    ) -> DealModel:
        deal = DealModel(
            product_id=product_id,
            current_price=display_price,
            final_price=display_price,
            currency=currency,
            payment_method="UNKNOWN",
            installments=1,
            # No verified coupon or sufficient price history exists at this stage.
            confidence="MEDIUM",
            score=0,
            source="shopee_affiliate_api",
            discovery_origin="relay",
            discovered_at=discovered_at,
            last_validated_at=discovered_at,
            affiliate_link=affiliate_link,
            status="READY",
            price_min=price_min,
            price_max=price_max,
            selected_price=selected_price,
            price_display_mode=price_display_mode,
            variation_id=variation_id,
            available=True,
            affiliate_proof_id=proof_id,
        )
        self.session.add(deal)
        await self.session.flush()
        return deal

    async def add_price_history(
        self,
        *,
        product_id: int,
        price: Decimal,
        currency: str,
        collected_at: datetime,
    ) -> PriceHistoryModel:
        item = PriceHistoryModel(
            product_id=product_id,
            price=price,
            currency=currency,
            payment_method="UNKNOWN",
            installments=1,
            collected_at=collected_at,
            source="shopee_affiliate_api",
        )
        self.session.add(item)
        await self.session.flush()
        return item


class AffiliateCandidateRepository:
    """Persistence and atomic recovery for provider enrichment work."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, internal_id: int) -> AffiliateCandidateModel | None:
        return await self.session.get(AffiliateCandidateModel, internal_id)

    async def find(
        self, store: str, external_product_id: str, variation_key: str = ""
    ) -> AffiliateCandidateModel | None:
        result = await self.session.execute(
            select(AffiliateCandidateModel).where(
                AffiliateCandidateModel.store == store,
                AffiliateCandidateModel.external_product_id == external_product_id,
                AffiliateCandidateModel.variation_key == variation_key,
            )
        )
        return result.scalar_one_or_none()

    async def ensure_for_link(
        self, source_link_id: int, *, variation_key: str = ""
    ) -> AffiliateCandidateModel:
        link = await self.session.get(SourceMessageLinkModel, source_link_id)
        if (
            link is None
            or link.store != "shopee"
            or not link.external_product_id
            or not link.canonical_url
        ):
            raise ValueError("source link is not an eligible Shopee affiliate candidate")
        canonical = canonicalize_store_url(link.canonical_url)
        if (
            canonical.store is not Store.SHOPEE
            or canonical.external_product_id != link.external_product_id
        ):
            raise ValueError("Shopee source link requires a shop_id:item_id identity")
        await self.session.execute(
            sqlite_insert(AffiliateCandidateModel)
            .values(
                store=link.store,
                external_product_id=link.external_product_id,
                variation_key=variation_key,
                canonical_url=link.canonical_url,
                state=AffiliateCandidateState.PENDING_AFFILIATE.value,
                attempt_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=["store", "external_product_id", "variation_key"]
            )
        )
        candidate = await self.find(link.store, link.external_product_id, variation_key)
        if candidate is None:
            raise RuntimeError("affiliate candidate insert could not be read back")
        if link.affiliate_candidate_id is None:
            link.affiliate_candidate_id = candidate.id
            await self.session.flush()
        elif link.affiliate_candidate_id != candidate.id:
            raise RuntimeError("source link is already attached to another candidate")
        return candidate

    async def backfill_shopee(self, *, limit: int) -> int:
        result = await self.session.execute(
            select(SourceMessageLinkModel)
            .where(
                SourceMessageLinkModel.affiliate_candidate_id.is_(None),
                SourceMessageLinkModel.store == "shopee",
                SourceMessageLinkModel.external_product_id.is_not(None),
                SourceMessageLinkModel.external_product_id.like("%:%"),
                SourceMessageLinkModel.canonical_url.is_not(None),
                or_(
                    SourceMessageLinkModel.state == RelayLinkState.PENDING_AFFILIATE.value,
                    SourceMessageLinkModel.reason_code == "DUPLICATE_CANONICAL",
                ),
            )
            .order_by(SourceMessageLinkModel.id)
            .limit(limit)
        )
        links = list(result.scalars())
        for link in links:
            await self.ensure_for_link(link.id)
        return len(links)

    async def claim(
        self,
        internal_id: int,
        *,
        now: datetime,
        lease_until: datetime,
        max_attempts: int,
    ) -> AffiliateCandidateModel | None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AffiliateCandidateModel)
                .where(
                    AffiliateCandidateModel.id == internal_id,
                    AffiliateCandidateModel.attempt_count < max_attempts,
                    self._recoverable_expression(now),
                )
                .values(
                    state=AffiliateCandidateState.VALIDATING.value,
                    attempt_count=AffiliateCandidateModel.attempt_count + 1,
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
        return await self.get(internal_id)

    async def mark_enriched(
        self,
        internal_id: int,
        *,
        now: datetime,
        product_id: int,
        deal_id: int,
    ) -> None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AffiliateCandidateModel)
                .where(
                    AffiliateCandidateModel.id == internal_id,
                    AffiliateCandidateModel.state == AffiliateCandidateState.VALIDATING.value,
                    AffiliateCandidateModel.processing_lease_until.is_not(None),
                    AffiliateCandidateModel.processing_lease_until > now,
                )
                .values(
                    state=AffiliateCandidateState.ENRICHED.value,
                    enriched_at=now,
                    processing_lease_until=None,
                    next_attempt_at=None,
                    error_code=None,
                    error_summary=None,
                    product_id=product_id,
                    deal_id=deal_id,
                )
            ),
        )
        if result.rowcount != 1:
            raise AffiliateCandidateTransitionConflict(
                "candidate enrichment transition lost or lease expired"
            )

    async def fail(
        self,
        internal_id: int,
        *,
        now: datetime,
        target_state: AffiliateCandidateState,
        next_attempt_at: datetime | None,
        error_code: str,
        error_summary: str | None = None,
    ) -> None:
        if target_state not in {
            AffiliateCandidateState.FAILED_RETRYABLE,
            AffiliateCandidateState.FAILED_PERMANENT,
            AffiliateCandidateState.MANUAL_REVIEW,
        }:
            raise ValueError("invalid affiliate candidate failure state")
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AffiliateCandidateModel)
                .where(
                    AffiliateCandidateModel.id == internal_id,
                    AffiliateCandidateModel.state == AffiliateCandidateState.VALIDATING.value,
                    AffiliateCandidateModel.processing_lease_until.is_not(None),
                    AffiliateCandidateModel.processing_lease_until > now,
                )
                .values(
                    state=target_state.value,
                    processing_lease_until=None,
                    next_attempt_at=(
                        next_attempt_at
                        if target_state is AffiliateCandidateState.FAILED_RETRYABLE
                        else None
                    ),
                    error_code=error_code,
                    error_summary=error_summary[:500] if error_summary else None,
                )
            ),
        )
        if result.rowcount != 1:
            raise AffiliateCandidateTransitionConflict(
                "candidate failure transition lost or lease expired"
            )

    async def recoverable(
        self, *, now: datetime, max_attempts: int, limit: int
    ) -> list[AffiliateCandidateModel]:
        result = await self.session.execute(
            select(AffiliateCandidateModel)
            .where(
                AffiliateCandidateModel.attempt_count < max_attempts,
                self._recoverable_expression(now),
            )
            .order_by(AffiliateCandidateModel.created_at, AffiliateCandidateModel.id)
            .limit(limit)
        )
        return list(result.scalars())

    @staticmethod
    def _recoverable_expression(now: datetime) -> Any:
        return or_(
            AffiliateCandidateModel.state == AffiliateCandidateState.PENDING_AFFILIATE.value,
            and_(
                AffiliateCandidateModel.state == AffiliateCandidateState.FAILED_RETRYABLE.value,
                or_(
                    AffiliateCandidateModel.next_attempt_at.is_(None),
                    AffiliateCandidateModel.next_attempt_at <= now,
                ),
            ),
            and_(
                AffiliateCandidateModel.state == AffiliateCandidateState.VALIDATING.value,
                AffiliateCandidateModel.processing_lease_until.is_not(None),
                AffiliateCandidateModel.processing_lease_until <= now,
            ),
        )


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
