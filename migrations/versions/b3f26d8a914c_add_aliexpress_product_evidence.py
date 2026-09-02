"""add sanitized AliExpress product evidence

Revision ID: b3f26d8a914c
Revises: 9c7e2a4f1b63
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "b3f26d8a914c"
down_revision: str | None = "9c7e2a4f1b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aliexpress_product_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("external_product_id", sa.String(length=160), nullable=False),
        sa.Column("selected_sku_id", sa.String(length=160), nullable=True),
        sa.Column("price_min", sa.Numeric(18, 2), nullable=False),
        sa.Column("price_max", sa.Numeric(18, 2), nullable=False),
        sa.Column("selected_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("price_scope", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=True),
        sa.Column("official_image_url", sa.Text(), nullable=True),
        sa.Column("commission_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("commission_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("shipping_fee", sa.Numeric(18, 2), nullable=True),
        sa.Column("source_operation", sa.String(length=120), nullable=False),
        sa.Column("queried_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["affiliate_candidates.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_aliexpress_snapshot_candidate"),
    )
    op.create_index(
        "ix_aliexpress_snapshots_product_queried",
        "aliexpress_product_snapshots",
        ["product_id", "queried_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aliexpress_snapshots_product_queried",
        table_name="aliexpress_product_snapshots",
    )
    op.drop_table("aliexpress_product_snapshots")
