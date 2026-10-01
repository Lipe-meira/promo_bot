"""Durable audit facts; deliberately no FK to expirable operational records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from promo_bot.database.models import Base, TimestampMixin
from promo_bot.database.types import UTCDateTime


class AffiliateLinkGenerationModel(TimestampMixin, Base):
    __tablename__ = "affiliate_link_generations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('REQUESTED','PREPARED','CALL_STARTED','CONFIRMED',"
            "'REJECTED','FAILED','UNCERTAIN')",
            name="ck_history_generation_state",
        ),
        CheckConstraint("scope IN ('runtime','shadow')", name="ck_history_generation_scope"),
        CheckConstraint("attribution_unverified = 1", name="ck_history_no_financial_proof"),
        CheckConstraint(
            "state != 'CONFIRMED' OR (generated_url IS NOT NULL AND generated_at IS NOT NULL "
            "AND tracking_confirmed = 1 AND contract_version IS NOT NULL "
            "AND correlation_mode IS NOT NULL AND validation_facts IS NOT NULL "
            "AND call_started_at IS NOT NULL)",
            name="ck_history_confirmation",
        ),
        CheckConstraint(
            "state = 'CONFIRMED' OR (generated_url IS NULL AND generated_at IS NULL "
            "AND tracking_confirmed = 0)",
            name="ck_history_unconfirmed_no_link",
        ),
        CheckConstraint(
            "input_fingerprint IS NULL OR length(input_fingerprint) = 64",
            name="ck_history_input_fingerprint",
        ),
        CheckConstraint(
            "tracking_fingerprint IS NULL OR length(tracking_fingerprint) = 64",
            name="ck_history_tracking_fingerprint",
        ),
        Index("ix_history_generation_identity", "scope", "platform", "identity_key"),
        Index("ix_history_generation_time", "generated_at"),
        Index("ix_history_generation_call", "call_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(200), nullable=False)
    key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    tracking_fingerprint: Mapped[str | None] = mapped_column(String(64))
    promotion_link_type: Mapped[int | None]
    call_id: Mapped[str | None] = mapped_column(String(36))
    call_ordinal: Mapped[int | None]
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    prepared_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    call_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    generated_url: Mapped[str | None] = mapped_column(Text)
    contract_version: Mapped[str | None] = mapped_column(String(80))
    correlation_mode: Mapped[str | None] = mapped_column(String(48))
    tracking_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attribution_unverified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validation_facts: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    origin_missing_reason: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    request_reason: Mapped[str | None] = mapped_column(String(40))
    operator_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    legacy_kind: Mapped[str | None] = mapped_column(String(32))
    legacy_id: Mapped[int | None]
    legacy_record_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AffiliateLinkUseModel(TimestampMixin, Base):
    __tablename__ = "affiliate_link_uses"
    __table_args__ = (
        CheckConstraint("scope IN ('runtime','shadow')", name="ck_history_use_scope"),
        CheckConstraint(
            "kind IN ('PREVIEW','EXPLICIT_OUTPUT','SEND')",
            name="ck_history_use_kind",
        ),
        CheckConstraint(
            "state IN ('PREVIEW_READY','OUTPUT_RECORDED','SEND_RESERVED','SEND_IN_FLIGHT',"
            "'SEND_CONFIRMED','SEND_FAILED','SEND_UNCERTAIN')",
            name="ck_history_use_state",
        ),
        Index("ix_history_use_time", "occurred_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    origin_missing_reason: Mapped[str | None] = mapped_column(String(80))
    destination_key: Mapped[str | None] = mapped_column(String(64))
    operational_kind: Mapped[str | None] = mapped_column(String(40))
    operational_id: Mapped[int | None]
    telegram_message_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(80))


class AffiliateLinkUseLinkModel(Base):
    __tablename__ = "affiliate_link_use_links"
    __table_args__ = (CheckConstraint("ordinal >= 0", name="ck_history_use_link_ordinal"),)
    use_id: Mapped[str] = mapped_column(
        ForeignKey("affiliate_link_uses.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("affiliate_link_generations.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
