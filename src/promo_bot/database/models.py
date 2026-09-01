"""SQLAlchemy schema for the Phase 1 foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from promo_bot.database.types import UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class SourceMessageModel(TimestampMixin, Base):
    __tablename__ = "source_messages"
    __table_args__ = (
        UniqueConstraint("platform", "message_id", "channel_id", name="uq_source_message"),
        Index("ix_source_messages_status", "processing_status"),
        Index(
            "ix_source_messages_recovery",
            "processing_status",
            "next_attempt_at",
            "processing_lease_until",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    links: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(40), default="RECEIVED", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    processing_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    processing_lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(String(500))

    extracted_links: Mapped[list[SourceMessageLinkModel]] = relationship(
        back_populates="source_message", cascade="all, delete-orphan"
    )


class SourceMessageLinkModel(TimestampMixin, Base):
    __tablename__ = "source_message_links"
    __table_args__ = (
        UniqueConstraint("source_message_id", "input_hash", name="uq_source_message_link_hash"),
        Index("ix_source_message_links_state", "state"),
        Index("ix_source_message_links_product", "store", "external_product_id"),
        Index("ix_source_message_links_candidate", "affiliate_candidate_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("source_messages.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_url: Mapped[str] = mapped_column(Text, nullable=False)
    expanded_url: Mapped[str | None] = mapped_column(Text)
    redirect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    store: Mapped[str | None] = mapped_column(String(32))
    external_product_id: Mapped[str | None] = mapped_column(String(160))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="RECEIVED", nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    affiliate_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("affiliate_candidates.id")
    )

    source_message: Mapped[SourceMessageModel] = relationship(back_populates="extracted_links")
    affiliate_candidate: Mapped[AffiliateCandidateModel | None] = relationship(
        back_populates="source_links"
    )


class AffiliateCandidateModel(TimestampMixin, Base):
    __tablename__ = "affiliate_candidates"
    __table_args__ = (
        UniqueConstraint(
            "store",
            "external_product_id",
            "variation_key",
            name="uq_affiliate_candidate_product_variation",
        ),
        Index(
            "ix_affiliate_candidates_recovery",
            "state",
            "next_attempt_at",
            "processing_lease_until",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    external_product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    variation_key: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="PENDING_AFFILIATE", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    processing_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    processing_lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    enriched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(String(500))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"))

    source_links: Mapped[list[SourceMessageLinkModel]] = relationship(
        back_populates="affiliate_candidate"
    )


class TelegramChannelCheckpointModel(TimestampMixin, Base):
    __tablename__ = "telegram_channel_checkpoints"

    channel_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_persisted_message_id: Mapped[int | None] = mapped_column()
    last_persisted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_catch_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(32), default="READY", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))


class ProductModel(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("store", "external_id", name="uq_products_store_external_id"),
        Index("ix_products_store_external_id", "store", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(200))
    seller: Mapped[str | None] = mapped_column(String(250))
    currency: Mapped[str] = mapped_column(String(3), default="BRL", nullable=False)

    deals: Mapped[list[DealModel]] = relationship(back_populates="product")
    price_history: Mapped[list[PriceHistoryModel]] = relationship(back_populates="product")
    shopee_snapshots: Mapped[list[ShopeeProductSnapshotModel]] = relationship(
        back_populates="product"
    )


class CouponModel(TimestampMixin, Base):
    __tablename__ = "coupons"
    __table_args__ = (
        Index("ix_coupons_status", "status"),
        Index("ix_coupons_store_code", "store", "code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(160))
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    discount_type: Mapped[str | None] = mapped_column(String(20))
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    minimum_purchase: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    maximum_discount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    allowed_categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_products: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    account_restrictions: Mapped[str | None] = mapped_column(Text)
    app_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_restrictions: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(250), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(32), default="DISCOVERED", nullable=False)

    deals: Mapped[list[DealModel]] = relationship(back_populates="coupon")


class DealModel(TimestampMixin, Base):
    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_status", "status"),
        Index("ix_deals_discovered_at", "discovered_at"),
        Index("ix_deals_last_validated_at", "last_validated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    previous_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    current_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    final_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    freight: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BRL", nullable=False)
    coupon_id: Mapped[int | None] = mapped_column(ForeignKey("coupons.id"))
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    installments: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    interest_free: Mapped[bool | None] = mapped_column(Boolean)
    payment_condition: Mapped[str | None] = mapped_column(Text)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    confidence: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(250), nullable=False)
    discovery_origin: Mapped[str] = mapped_column(String(40), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    affiliate_link: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DISCOVERED", nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(40), default="AWAITING_INTERNAL_REVIEW", nullable=False
    )
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    selected_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    price_display_mode: Mapped[str | None] = mapped_column(String(32))
    variation_id: Mapped[str | None] = mapped_column(String(160))
    available: Mapped[bool | None] = mapped_column(Boolean)
    affiliate_proof_id: Mapped[int | None] = mapped_column(ForeignKey("affiliate_link_proofs.id"))

    product: Mapped[ProductModel] = relationship(back_populates="deals")
    coupon: Mapped[CouponModel | None] = relationship(back_populates="deals")
    deliveries: Mapped[list[DeliveryModel]] = relationship(back_populates="deal")


class DeliveryModel(TimestampMixin, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("deal_id", "purpose", name="uq_delivery_deal_purpose"),
        UniqueConstraint("idempotency_key", name="uq_delivery_idempotency_key"),
        Index("ix_deliveries_recovery", "state", "next_attempt_at", "lease_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), default="INTERNAL_REVIEW", nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="PENDING", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    telegram_message_id: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(String(500))

    deal: Mapped[DealModel] = relationship(back_populates="deliveries")


class AffiliateLinkProofModel(TimestampMixin, Base):
    __tablename__ = "affiliate_link_proofs"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_affiliate_link_proof_candidate"),
        Index("ix_affiliate_link_proofs_product", "provider", "source_external_product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("affiliate_candidates.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    responded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_external_product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    short_link: Mapped[str] = mapped_column(Text, nullable=False)
    official_endpoint_host: Mapped[str] = mapped_column(String(253), nullable=False)
    credential_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    generation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    official_response_validated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ShopeeProductSnapshotModel(TimestampMixin, Base):
    __tablename__ = "shopee_product_snapshots"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_shopee_snapshot_candidate"),
        Index("ix_shopee_snapshots_product_queried", "product_id", "queried_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("affiliate_candidates.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(80), nullable=False)
    item_id: Mapped[str] = mapped_column(String(80), nullable=False)
    selected_variation_id: Mapped[str | None] = mapped_column(String(160))
    price_min: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    price_max: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    selected_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selected_variation_available: Mapped[bool | None] = mapped_column(Boolean)
    range_semantics_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    official_image_url: Mapped[str | None] = mapped_column(Text)
    queried_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    product: Mapped[ProductModel] = relationship(back_populates="shopee_snapshots")


class PriceHistoryModel(Base):
    __tablename__ = "price_history"
    __table_args__ = (Index("ix_price_history_product_collected", "product_id", "collected_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    freight: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    installments: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    interest_free: Mapped[bool | None] = mapped_column(Boolean)
    payment_condition: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(250), nullable=False)

    product: Mapped[ProductModel] = relationship(back_populates="price_history")


class ProcessedItemModel(Base):
    __tablename__ = "processed_items"
    __table_args__ = (
        UniqueConstraint(
            "store", "external_product_id", "variation_key", name="uq_processed_product_variation"
        ),
        Index("ix_processed_items_deal_hash", "deal_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    external_product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    variation_key: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    deal_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    last_coupon: Mapped[str | None] = mapped_column(String(160))
    cooldown_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
