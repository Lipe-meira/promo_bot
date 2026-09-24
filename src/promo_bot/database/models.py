"""SQLAlchemy schema for the Phase 1 foundation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
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
    surface_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
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
    aliexpress_snapshots: Mapped[list[AliExpressProductSnapshotModel]] = relationship(
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
    promotion_link_type: Mapped[int | None] = mapped_column(Integer)
    tracking_fingerprint: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sub_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    generation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    official_response_validated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AffiliateShadowPreviewModel(TimestampMixin, Base):
    """Provider-neutral metadata plus short-lived explicit preview content."""

    __tablename__ = "affiliate_shadow_previews"
    __table_args__ = (
        UniqueConstraint(
            "source_message_id",
            "provider",
            "store",
            name="uq_affiliate_shadow_preview_source_provider_store",
        ),
        Index("ix_affiliate_shadow_previews_created", "created_at"),
        Index("ix_affiliate_shadow_previews_content_expiry", "content_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("source_messages.id", ondelete="CASCADE"), nullable=False
    )
    affiliate_proof_id: Mapped[int] = mapped_column(
        ForeignKey("affiliate_link_proofs.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    store: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="READY", nullable=False)
    replacement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    affiliate_host: Mapped[str] = mapped_column(String(253), nullable=False)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    affiliate_link: Mapped[str | None] = mapped_column(Text)
    content_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AffiliateShadowPreviewLinkModel(TimestampMixin, Base):
    """Provider-neutral correlation between a preview, source URL, and proof."""

    __tablename__ = "affiliate_shadow_preview_links"
    __table_args__ = (
        UniqueConstraint(
            "preview_id",
            "source_message_link_id",
            name="uq_shadow_preview_link_source",
        ),
        CheckConstraint("ordinal >= 0", name="ck_shadow_preview_link_ordinal"),
        CheckConstraint(
            "occurrence_count >= 1",
            name="ck_shadow_preview_link_occurrence_count",
        ),
        Index("ix_shadow_preview_links_preview", "preview_id", "ordinal"),
        Index("ix_shadow_preview_links_proof", "affiliate_proof_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    preview_id: Mapped[int] = mapped_column(
        ForeignKey("affiliate_shadow_previews.id", ondelete="CASCADE"), nullable=False
    )
    source_message_link_id: Mapped[int] = mapped_column(
        ForeignKey("source_message_links.id", ondelete="CASCADE"), nullable=False
    )
    affiliate_proof_id: Mapped[int] = mapped_column(
        ForeignKey("affiliate_link_proofs.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ShadowDeliveryModel(TimestampMixin, Base):
    """Metadata only; repositories require a dedicated shadow session."""

    __tablename__ = "affiliate_shadow_deliveries"
    __table_args__ = (
        UniqueConstraint("preview_id", "destination_key", name="uq_shadow_delivery_identity"),
        UniqueConstraint(
            "source_message_id",
            "destination_key",
            name="uq_shadow_delivery_source_destination",
        ),
        CheckConstraint("attempt_count IN (0, 1)", name="ck_shadow_delivery_one_attempt"),
        CheckConstraint(
            "state IN ('pending','sending','sent','failed_safe','uncertain')",
            name="ck_shadow_delivery_state",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    preview_id: Mapped[int] = mapped_column(
        ForeignKey("affiliate_shadow_previews.id"), nullable=False
    )
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("source_messages.id", ondelete="CASCADE"), nullable=False
    )
    destination_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    telegram_message_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AliExpressCoinShadowEvidenceModel(TimestampMixin, Base):
    """Minimal, fail-closed evidence for one direct coin-short generation."""

    __tablename__ = "aliexpress_coin_shadow_evidence"
    __table_args__ = (
        UniqueConstraint(
            "input_fingerprint",
            "tracking_fingerprint",
            "promotion_link_type",
            name="uq_coin_shadow_evidence_identity",
        ),
        UniqueConstraint("id", "state", name="uq_coin_shadow_evidence_id_state"),
        CheckConstraint("length(input_fingerprint) = 64", name="ck_coin_input_fingerprint"),
        CheckConstraint("length(tracking_fingerprint) = 64", name="ck_coin_tracking_fingerprint"),
        CheckConstraint("generation_count BETWEEN 0 AND 1", name="ck_coin_generation_count"),
        CheckConstraint(
            "state IN ('GENERATING','READY','REVIEW_REQUIRED','UNCERTAIN')",
            name="ck_coin_evidence_state",
        ),
        CheckConstraint("attribution_unverified = 1", name="ck_coin_attribution_unverified"),
        CheckConstraint(
            "state != 'GENERATING' OR (generation_count = 1 AND lease_until IS NOT NULL "
            "AND lease_token IS NOT NULL AND generation_started_at IS NOT NULL)",
            name="ck_coin_generating_lease",
        ),
        CheckConstraint(
            "state = 'GENERATING' OR (lease_until IS NULL AND lease_token IS NULL)",
            name="ck_coin_terminal_without_lease",
        ),
        CheckConstraint(
            "state != 'READY' OR (promotion_link IS NOT NULL AND affiliate_host IS NOT NULL "
            "AND tracking_confirmed = 1 AND correlation_mode IN "
            "('SOURCE_VALUE_EXACT','POSITIONAL_SINGLETON') AND generated_at IS NOT NULL "
            "AND expires_at IS NOT NULL)",
            name="ck_coin_ready_complete",
        ),
        CheckConstraint(
            "state = 'READY' OR (promotion_link IS NULL AND affiliate_host IS NULL "
            "AND correlation_mode IS NULL AND generated_at IS NULL AND expires_at IS NULL)",
            name="ck_coin_non_ready_without_link",
        ),
        Index("ix_coin_shadow_evidence_state_expiry", "state", "expires_at"),
        Index("ix_coin_shadow_evidence_lease", "state", "lease_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    promotion_link_type: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    generation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_token: Mapped[str | None] = mapped_column(String(64))
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    correlation_mode: Mapped[str | None] = mapped_column(String(32))
    tracking_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution_unverified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    route_preservation_manually_observed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    promotion_link: Mapped[str | None] = mapped_column(Text)
    affiliate_host: Mapped[str | None] = mapped_column(String(253))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AliExpressCoinShadowPreviewModel(TimestampMixin, Base):
    """Short-lived content derived only from READY coin evidence."""

    __tablename__ = "aliexpress_coin_shadow_previews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evidence_id", "evidence_state"],
            ["aliexpress_coin_shadow_evidence.id", "aliexpress_coin_shadow_evidence.state"],
            ondelete="CASCADE",
            name="fk_coin_preview_ready_evidence",
        ),
        UniqueConstraint("source_message_fingerprint", name="uq_coin_preview_source_message"),
        CheckConstraint("evidence_state = 'READY'", name="ck_coin_preview_ready"),
        CheckConstraint(
            "length(source_message_fingerprint) = 64", name="ck_coin_preview_message_fingerprint"
        ),
        Index("ix_coin_shadow_preview_expiry", "content_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_state: Mapped[str] = mapped_column(String(24), nullable=False, default="READY")
    source_message_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AliExpressCoinShadowDeliveryModel(TimestampMixin, Base):
    """Durable send reservation retained after preview/evidence purge."""

    __tablename__ = "aliexpress_coin_shadow_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "source_message_fingerprint",
            "destination_fingerprint",
            name="uq_coin_delivery_source_destination",
        ),
        CheckConstraint(
            "length(source_message_fingerprint) = 64", name="ck_coin_delivery_message_fingerprint"
        ),
        CheckConstraint(
            "length(destination_fingerprint) = 64", name="ck_coin_delivery_destination_fingerprint"
        ),
        CheckConstraint("attempt_count IN (0, 1)", name="ck_coin_delivery_one_attempt"),
        CheckConstraint(
            "state IN ('pending','sending','sent','failed_safe','uncertain')",
            name="ck_coin_delivery_state",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    preview_id: Mapped[int | None] = mapped_column(
        ForeignKey("aliexpress_coin_shadow_previews.id", ondelete="SET NULL")
    )
    source_message_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    telegram_message_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AliExpressDiscoveryRunModel(TimestampMixin, Base):
    """One bounded, manual discovery execution from one operation."""

    __tablename__ = "aliexpress_discovery_runs"
    __table_args__ = (
        CheckConstraint("length(profile_fingerprint) = 64", name="ck_discovery_profile_fp"),
        CheckConstraint(
            "source_operation IN ('aliexpress.affiliate.product.query',"
            "'aliexpress.affiliate.hotproduct.query')",
            name="ck_discovery_run_source",
        ),
        CheckConstraint(
            "state IN ('RUNNING','COMPLETED','STOPPED','REVIEW_REQUIRED','UNCERTAIN')",
            name="ck_discovery_run_state",
        ),
        CheckConstraint("page_size BETWEEN 1 AND 50", name="ck_discovery_page_size"),
        CheckConstraint("max_pages BETWEEN 1 AND 5", name="ck_discovery_max_pages"),
        CheckConstraint("max_results BETWEEN 1 AND 250", name="ck_discovery_max_results"),
        CheckConstraint("max_api_calls BETWEEN 1 AND 20", name="ck_discovery_max_calls"),
        CheckConstraint("minimum_drop_percent > 0", name="ck_discovery_minimum_drop"),
        CheckConstraint(
            "api_call_count >= 0 AND api_call_count <= max_api_calls",
            name="ck_discovery_api_call_count",
        ),
        CheckConstraint(
            "cache_hit_count >= 0 AND page_count >= 0 AND received_count >= 0 "
            "AND snapshot_count >= 0",
            name="ck_discovery_nonnegative_counters",
        ),
        CheckConstraint(
            "unique_product_count >= 0 AND unique_product_count <= max_results",
            name="ck_discovery_unique_count",
        ),
        CheckConstraint(
            "state != 'RUNNING' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL "
            "AND finished_at IS NULL)",
            name="ck_discovery_running_lease",
        ),
        CheckConstraint(
            "state = 'RUNNING' OR (lease_token IS NULL AND lease_until IS NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_discovery_terminal_without_lease",
        ),
        Index("ix_discovery_runs_state_lease", "state", "lease_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_name: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_operation: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    page_size: Mapped[int] = mapped_column(Integer, nullable=False)
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    max_api_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_drop_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AliExpressDiscoveryQueryClaimModel(Base):
    __tablename__ = "aliexpress_discovery_query_claims"
    __table_args__ = (
        CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_claim_query_fp"),
        CheckConstraint("length(tracking_fingerprint) = 64", name="ck_discovery_claim_tracking_fp"),
        CheckConstraint("query_ordinal >= 0 AND page_no >= 1", name="ck_discovery_claim_position"),
        Index("ix_discovery_claims_lease", "lease_until"),
    )

    query_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    tracking_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_runs.id", ondelete="CASCADE"), nullable=False
    )
    query_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str] = mapped_column(String(32), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AliExpressDiscoveryQueryCacheModel(Base):
    __tablename__ = "aliexpress_discovery_query_cache"
    __table_args__ = (
        CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_cache_query_fp"),
        CheckConstraint("length(tracking_fingerprint) = 64", name="ck_discovery_cache_tracking_fp"),
        CheckConstraint(
            "source_operation IN ('aliexpress.affiliate.product.query',"
            "'aliexpress.affiliate.hotproduct.query')",
            name="ck_discovery_cache_source",
        ),
        CheckConstraint("item_count >= 0", name="ck_discovery_cache_item_count"),
        CheckConstraint(
            "current_record_count IS NULL OR current_record_count >= 0",
            name="ck_discovery_cache_current_count",
        ),
        CheckConstraint(
            "total_record_count IS NULL OR total_record_count >= 0",
            name="ck_discovery_cache_total_count",
        ),
        Index("ix_discovery_cache_expiry", "expires_at"),
    )

    query_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    tracking_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_operation: Mapped[str] = mapped_column(String(120), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    current_record_count: Mapped[int | None] = mapped_column(Integer)
    total_record_count: Mapped[int | None] = mapped_column(Integer)


class AliExpressDiscoveryCacheProductModel(Base):
    __tablename__ = "aliexpress_discovery_cache_products"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_fingerprint", "tracking_fingerprint"],
            [
                "aliexpress_discovery_query_cache.query_fingerprint",
                "aliexpress_discovery_query_cache.tracking_fingerprint",
            ],
            ondelete="CASCADE",
            name="fk_discovery_cache_product_identity",
        ),
        UniqueConstraint(
            "query_fingerprint",
            "tracking_fingerprint",
            "ordinal",
            name="uq_discovery_cache_product_ordinal",
        ),
        CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_product_query_fp"),
        CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_discovery_product_tracking_fp"
        ),
        CheckConstraint("ordinal >= 0", name="ck_discovery_product_ordinal"),
        CheckConstraint(
            "target_brl_price IS NULL OR target_brl_price > 0",
            name="ck_discovery_product_positive_price",
        ),
        CheckConstraint(
            "completeness_score BETWEEN 0 AND 10", name="ck_discovery_product_completeness"
        ),
        Index("ix_discovery_cache_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    first_category_id: Mapped[str | None] = mapped_column(String(160))
    first_category_name: Mapped[str | None] = mapped_column(Text)
    second_category_id: Mapped[str | None] = mapped_column(String(160))
    second_category_name: Mapped[str | None] = mapped_column(Text)
    shop_id: Mapped[str | None] = mapped_column(String(160))
    shop_name: Mapped[str | None] = mapped_column(Text)
    target_brl_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    observed_prices: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    declared_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    hot_product_commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    volume: Mapped[int | None] = mapped_column(Integer)
    completeness_score: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnostics: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class AliExpressDiscoveryPriceSnapshotModel(Base):
    __tablename__ = "aliexpress_discovery_price_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "product_id", name="uq_discovery_snapshot_run_product"),
        CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_snapshot_query_fp"),
        CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_discovery_snapshot_tracking_fp"
        ),
        CheckConstraint("price > 0", name="ck_discovery_snapshot_positive_price"),
        CheckConstraint("currency = 'BRL'", name="ck_discovery_snapshot_brl"),
        CheckConstraint(
            "source_operation IN ('aliexpress.affiliate.product.query',"
            "'aliexpress.affiliate.hotproduct.query')",
            name="ck_discovery_snapshot_source",
        ),
        Index("ix_discovery_snapshot_product_time", "product_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_runs.id", ondelete="CASCADE"), nullable=False
    )
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_operation: Mapped[str] = mapped_column(String(120), nullable=False)


class AliExpressDiscoveryRunResultModel(Base):
    __tablename__ = "aliexpress_discovery_run_results"
    __table_args__ = (
        UniqueConstraint("run_id", "product_id", name="uq_discovery_result_run_product"),
        CheckConstraint("origin IN ('LIVE','CACHE')", name="ck_discovery_result_origin"),
        CheckConstraint(
            "(target_brl_price IS NULL AND currency IS NULL) OR "
            "(target_brl_price > 0 AND currency = 'BRL')",
            name="ck_discovery_result_price_currency",
        ),
        CheckConstraint("history_score BETWEEN 0 AND 60", name="ck_discovery_history_score"),
        CheckConstraint("discount_score BETWEEN 0 AND 15", name="ck_discovery_discount_score"),
        CheckConstraint("volume_score BETWEEN 0 AND 10", name="ck_discovery_volume_score"),
        CheckConstraint("commission_score BETWEEN 0 AND 5", name="ck_discovery_commission_score"),
        CheckConstraint(
            "completeness_score BETWEEN 0 AND 10", name="ck_discovery_result_completeness"
        ),
        CheckConstraint(
            "total_score = history_score + discount_score + volume_score + commission_score "
            "+ completeness_score AND total_score BETWEEN 0 AND 100",
            name="ck_discovery_total_score",
        ),
        CheckConstraint(
            "classification IN ('BASELINE_ONLY','PROVIDER_DISCOUNT_ONLY',"
            "'HISTORY_BACKED_PRICE_DROP','INSUFFICIENT_DATA')",
            name="ck_discovery_classification",
        ),
        CheckConstraint(
            "classification != 'HISTORY_BACKED_PRICE_DROP' OR "
            "(target_brl_price IS NOT NULL AND currency = 'BRL' AND history_snapshot_count >= 2 "
            "AND history_median IS NOT NULL AND price_drop_percent >= minimum_drop_percent)",
            name="ck_discovery_history_classification",
        ),
        CheckConstraint(
            "classification != 'INSUFFICIENT_DATA' OR target_brl_price IS NULL",
            name="ck_discovery_insufficient_without_price",
        ),
        CheckConstraint("matched_query_count >= 1", name="ck_discovery_matched_queries"),
        Index("ix_discovery_results_run_score", "run_id", "total_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_runs.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("aliexpress_discovery_price_snapshots.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    target_brl_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    observed_prices: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    declared_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    volume: Mapped[int | None] = mapped_column(Integer)
    history_median: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    history_snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_drop_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    minimum_drop_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    history_score: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_score: Mapped[int] = mapped_column(Integer, nullable=False)
    volume_score: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_score: Mapped[int] = mapped_column(Integer, nullable=False)
    completeness_score: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    matched_query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AliExpressDiscoverySkuRefinementRunModel(Base):
    """One manual, bounded refinement of a completed product discovery run."""

    __tablename__ = "aliexpress_discovery_sku_refinement_runs"
    __table_args__ = (
        CheckConstraint("length(requirements_fingerprint) = 64", name="ck_sku_run_requirements_fp"),
        CheckConstraint(
            "state IN ('RUNNING','COMPLETED','STOPPED','REVIEW_REQUIRED','UNCERTAIN')",
            name="ck_sku_run_state",
        ),
        CheckConstraint("max_refined_products BETWEEN 1 AND 20", name="ck_sku_run_max_products"),
        CheckConstraint(
            "max_sku_api_calls BETWEEN 1 AND max_refined_products", name="ck_sku_run_max_calls"
        ),
        CheckConstraint(
            "api_call_count BETWEEN 0 AND max_sku_api_calls AND "
            "refined_count BETWEEN 0 AND max_refined_products AND "
            "cache_hit_count >= 0 AND snapshot_count >= 0",
            name="ck_sku_run_counters",
        ),
        CheckConstraint("minimum_drop_percent > 0", name="ck_sku_run_min_drop"),
        CheckConstraint(
            "(state = 'RUNNING' AND lease_token IS NOT NULL AND lease_until IS NOT NULL "
            "AND finished_at IS NULL) OR (state != 'RUNNING' AND lease_token IS NULL "
            "AND lease_until IS NULL AND finished_at IS NOT NULL)",
            name="ck_sku_run_lease",
        ),
        Index("ix_sku_runs_state_lease", "state", "lease_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_runs.id", ondelete="RESTRICT"), nullable=False
    )
    profile_name: Mapped[str] = mapped_column(String(64), nullable=False)
    requirements_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    max_refined_products: Mapped[int] = mapped_column(Integer, nullable=False)
    max_sku_api_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_drop_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    api_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AliExpressDiscoverySkuClaimModel(Base):
    __tablename__ = "aliexpress_discovery_sku_claims"
    __table_args__ = (
        CheckConstraint("length(sku_query_fingerprint) = 64", name="ck_sku_claim_fp"),
        Index("ix_sku_claims_lease", "lease_until"),
    )

    sku_query_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_sku_refinement_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_token: Mapped[str] = mapped_column(String(32), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AliExpressDiscoverySkuCacheModel(Base):
    __tablename__ = "aliexpress_discovery_sku_cache"
    __table_args__ = (
        CheckConstraint("length(sku_query_fingerprint) = 64", name="ck_sku_cache_fp"),
        CheckConstraint("sku_count BETWEEN 1 AND 19", name="ck_sku_cache_count"),
        Index("ix_sku_cache_expiry", "expires_at"),
    )

    sku_query_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sku_count: Mapped[int] = mapped_column(Integer, nullable=False)


class AliExpressDiscoverySkuCacheItemModel(Base):
    __tablename__ = "aliexpress_discovery_sku_cache_items"
    __table_args__ = (
        UniqueConstraint("sku_query_fingerprint", "ordinal", name="uq_sku_cache_ordinal"),
        UniqueConstraint("sku_query_fingerprint", "sku_id", name="uq_sku_cache_sku"),
        CheckConstraint("ordinal >= 0", name="ck_sku_cache_item_ordinal"),
        CheckConstraint("sale_price_with_tax > 0", name="ck_sku_cache_item_price"),
        CheckConstraint("currency = 'BRL'", name="ck_sku_cache_item_currency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_query_fingerprint: Mapped[str] = mapped_column(
        ForeignKey("aliexpress_discovery_sku_cache.sku_query_fingerprint", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sku_id: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_with_tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    sale_price_with_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    attributes: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)


class AliExpressDiscoverySkuRefinementItemModel(Base):
    __tablename__ = "aliexpress_discovery_sku_refinement_items"
    __table_args__ = (
        UniqueConstraint("refinement_run_id", "product_id", name="uq_sku_item_run_product"),
        CheckConstraint("origin IN ('LIVE','CACHE')", name="ck_sku_item_origin"),
        CheckConstraint(
            "state IN ('MATCHED','NO_MATCH','AMBIGUOUS','REVIEW_REQUIRED')",
            name="ck_sku_item_state",
        ),
        CheckConstraint(
            "(state = 'MATCHED' AND selected_sku_id IS NOT NULL "
            "AND sale_price_with_tax IS NOT NULL AND sale_price_with_tax > 0 "
            "AND currency IS NOT NULL AND currency = 'BRL') "
            "OR (state != 'MATCHED' AND selected_sku_id IS NULL "
            "AND sale_price_with_tax IS NULL AND currency IS NULL)",
            name="ck_sku_item_selection",
        ),
        CheckConstraint("source_product_score BETWEEN 0 AND 100", name="ck_sku_item_score"),
        CheckConstraint("history_snapshot_count >= 0", name="ck_sku_item_history_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    refinement_run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_sku_refinement_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sku_query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_product_score: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_sku_id: Mapped[str | None] = mapped_column(String(160))
    sale_price_with_tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    history_median: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    history_snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_drop_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    classification: Mapped[str | None] = mapped_column(String(48))
    rank_position: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))


class AliExpressDiscoverySkuMatchModel(Base):
    __tablename__ = "aliexpress_discovery_sku_matches"
    __table_args__ = (
        UniqueConstraint("item_id", "sku_id", name="uq_sku_match_item_sku"),
        CheckConstraint("sale_price_with_tax > 0", name="ck_sku_match_price"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_sku_refinement_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sale_price_with_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    attributes: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)


class AliExpressDiscoverySkuPriceSnapshotModel(Base):
    __tablename__ = "aliexpress_discovery_sku_price_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "refinement_run_id", "product_id", "sku_id", name="uq_sku_snapshot_run_product_sku"
        ),
        CheckConstraint("price > 0", name="ck_sku_snapshot_price"),
        CheckConstraint("currency = 'BRL'", name="ck_sku_snapshot_currency"),
        CheckConstraint("price_basis = 'SALE_PRICE_WITH_TAX'", name="ck_sku_snapshot_basis"),
        CheckConstraint(
            "source_operation = 'aliexpress.affiliate.product.sku.detail.get'",
            name="ck_sku_snapshot_source",
        ),
        Index("ix_sku_snapshot_identity_time", "product_id", "sku_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    refinement_run_id: Mapped[int] = mapped_column(
        ForeignKey("aliexpress_discovery_sku_refinement_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sku_id: Mapped[str] = mapped_column(String(160), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    price_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    source_operation: Mapped[str] = mapped_column(String(120), nullable=False)


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


class AliExpressProductSnapshotModel(TimestampMixin, Base):
    __tablename__ = "aliexpress_product_snapshots"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_aliexpress_snapshot_candidate"),
        Index("ix_aliexpress_snapshots_product_queried", "product_id", "queried_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("affiliate_candidates.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    external_product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    selected_sku_id: Mapped[str | None] = mapped_column(String(160))
    price_min: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    price_max: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    selected_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    price_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    available: Mapped[bool | None] = mapped_column(Boolean)
    official_image_url: Mapped[str | None] = mapped_column(Text)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    commission_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    source_operation: Mapped[str] = mapped_column(String(120), nullable=False)
    queried_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    product: Mapped[ProductModel] = relationship(back_populates="aliexpress_snapshots")


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
