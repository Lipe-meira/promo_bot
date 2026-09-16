"""Add isolated AliExpress coin-short shadow evidence.

Revision ID: b72e4c9d1a30
Revises: a91c2d4e6f80
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "b72e4c9d1a30"
down_revision: str | None = "a91c2d4e6f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aliexpress_coin_shadow_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("tracking_fingerprint", sa.String(64), nullable=False),
        sa.Column("promotion_link_type", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("generation_count", sa.Integer(), nullable=False),
        sa.Column("generation_started_at", UTCDateTime()),
        sa.Column("lease_until", UTCDateTime()),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("generated_at", UTCDateTime()),
        sa.Column("expires_at", UTCDateTime()),
        sa.Column("correlation_mode", sa.String(32)),
        sa.Column("tracking_confirmed", sa.Boolean(), nullable=False),
        sa.Column("attribution_unverified", sa.Boolean(), nullable=False),
        sa.Column("route_preservation_manually_observed", sa.Boolean(), nullable=False),
        sa.Column("promotion_link", sa.Text()),
        sa.Column("affiliate_host", sa.String(253)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint(
            "input_fingerprint",
            "tracking_fingerprint",
            "promotion_link_type",
            name="uq_coin_shadow_evidence_identity",
        ),
        sa.UniqueConstraint("id", "state", name="uq_coin_shadow_evidence_id_state"),
        sa.CheckConstraint("length(input_fingerprint) = 64", name="ck_coin_input_fingerprint"),
        sa.CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_coin_tracking_fingerprint"
        ),
        sa.CheckConstraint("generation_count BETWEEN 0 AND 1", name="ck_coin_generation_count"),
        sa.CheckConstraint(
            "state IN ('GENERATING','READY','REVIEW_REQUIRED','UNCERTAIN')",
            name="ck_coin_evidence_state",
        ),
        sa.CheckConstraint("attribution_unverified = 1", name="ck_coin_attribution_unverified"),
        sa.CheckConstraint(
            "state != 'GENERATING' OR (generation_count = 1 AND lease_until IS NOT NULL "
            "AND lease_token IS NOT NULL AND generation_started_at IS NOT NULL)",
            name="ck_coin_generating_lease",
        ),
        sa.CheckConstraint(
            "state = 'GENERATING' OR (lease_until IS NULL AND lease_token IS NULL)",
            name="ck_coin_terminal_without_lease",
        ),
        sa.CheckConstraint(
            "state != 'READY' OR (promotion_link IS NOT NULL AND affiliate_host IS NOT NULL "
            "AND tracking_confirmed = 1 AND correlation_mode IN "
            "('SOURCE_VALUE_EXACT','POSITIONAL_SINGLETON') AND generated_at IS NOT NULL "
            "AND expires_at IS NOT NULL)",
            name="ck_coin_ready_complete",
        ),
        sa.CheckConstraint(
            "state = 'READY' OR (promotion_link IS NULL AND affiliate_host IS NULL "
            "AND correlation_mode IS NULL AND generated_at IS NULL AND expires_at IS NULL)",
            name="ck_coin_non_ready_without_link",
        ),
    )
    op.create_index(
        "ix_coin_shadow_evidence_state_expiry",
        "aliexpress_coin_shadow_evidence",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_coin_shadow_evidence_lease",
        "aliexpress_coin_shadow_evidence",
        ["state", "lease_until"],
    )

    op.create_table(
        "aliexpress_coin_shadow_previews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("evidence_state", sa.String(24), nullable=False),
        sa.Column("source_message_fingerprint", sa.String(64), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("content_expires_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_state"],
            ["aliexpress_coin_shadow_evidence.id", "aliexpress_coin_shadow_evidence.state"],
            ondelete="CASCADE",
            name="fk_coin_preview_ready_evidence",
        ),
        sa.UniqueConstraint("source_message_fingerprint", name="uq_coin_preview_source_message"),
        sa.CheckConstraint("evidence_state = 'READY'", name="ck_coin_preview_ready"),
        sa.CheckConstraint(
            "length(source_message_fingerprint) = 64",
            name="ck_coin_preview_message_fingerprint",
        ),
    )
    op.create_index(
        "ix_coin_shadow_preview_expiry",
        "aliexpress_coin_shadow_previews",
        ["content_expires_at"],
    )

    op.create_table(
        "aliexpress_coin_shadow_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("preview_id", sa.Integer()),
        sa.Column("source_message_fingerprint", sa.String(64), nullable=False),
        sa.Column("destination_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", UTCDateTime()),
        sa.Column("finished_at", UTCDateTime()),
        sa.Column("telegram_message_id", sa.String(128)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["preview_id"],
            ["aliexpress_coin_shadow_previews.id"],
            ondelete="SET NULL",
            name="fk_coin_delivery_preview",
        ),
        sa.UniqueConstraint(
            "source_message_fingerprint",
            "destination_fingerprint",
            name="uq_coin_delivery_source_destination",
        ),
        sa.CheckConstraint(
            "length(source_message_fingerprint) = 64",
            name="ck_coin_delivery_message_fingerprint",
        ),
        sa.CheckConstraint(
            "length(destination_fingerprint) = 64",
            name="ck_coin_delivery_destination_fingerprint",
        ),
        sa.CheckConstraint("attempt_count IN (0, 1)", name="ck_coin_delivery_one_attempt"),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','failed_safe','uncertain')",
            name="ck_coin_delivery_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("aliexpress_coin_shadow_deliveries")
    op.drop_index("ix_coin_shadow_preview_expiry", table_name="aliexpress_coin_shadow_previews")
    op.drop_table("aliexpress_coin_shadow_previews")
    op.drop_index("ix_coin_shadow_evidence_lease", table_name="aliexpress_coin_shadow_evidence")
    op.drop_index(
        "ix_coin_shadow_evidence_state_expiry",
        table_name="aliexpress_coin_shadow_evidence",
    )
    op.drop_table("aliexpress_coin_shadow_evidence")
