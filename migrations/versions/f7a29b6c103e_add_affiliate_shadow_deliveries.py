"""Add isolated, single-attempt shadow delivery metadata.

Revision ID: f7a29b6c103e
Revises: e4c19a7b52d0
"""

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision = "f7a29b6c103e"
down_revision = "e4c19a7b52d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "affiliate_shadow_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("preview_id", sa.Integer(), nullable=False),
        sa.Column("destination_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", UTCDateTime()),
        sa.Column("finished_at", UTCDateTime()),
        sa.Column("telegram_message_id", sa.String(128)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["preview_id"], ["affiliate_shadow_previews.id"]),
        sa.UniqueConstraint("preview_id", "destination_key", name="uq_shadow_delivery_identity"),
        sa.CheckConstraint("attempt_count IN (0, 1)", name="ck_shadow_delivery_one_attempt"),
        sa.CheckConstraint(
            "state IN ('pending','sending','sent','failed_safe','uncertain')",
            name="ck_shadow_delivery_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("affiliate_shadow_deliveries")
