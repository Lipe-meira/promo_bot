"""add generic affiliate shadow previews

Revision ID: e4c19a7b52d0
Revises: d8a31f67c2b4
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "e4c19a7b52d0"
down_revision: str | None = "d8a31f67c2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "affiliate_shadow_previews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("affiliate_proof_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("store", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("replacement_count", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("affiliate_host", sa.String(length=253), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=True),
        sa.Column("affiliate_link", sa.Text(), nullable=True),
        sa.Column("content_expires_at", UTCDateTime(), nullable=False),
        sa.Column("purged_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["affiliate_proof_id"], ["affiliate_link_proofs.id"]),
        sa.ForeignKeyConstraint(["source_message_id"], ["source_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_message_id",
            "provider",
            "store",
            name="uq_affiliate_shadow_preview_source_provider_store",
        ),
    )
    op.create_index(
        "ix_affiliate_shadow_previews_created",
        "affiliate_shadow_previews",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_affiliate_shadow_previews_content_expiry",
        "affiliate_shadow_previews",
        ["content_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_affiliate_shadow_previews_content_expiry",
        table_name="affiliate_shadow_previews",
    )
    op.drop_index(
        "ix_affiliate_shadow_previews_created",
        table_name="affiliate_shadow_previews",
    )
    op.drop_table("affiliate_shadow_previews")
