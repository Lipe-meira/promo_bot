"""add Shopee offer snapshots and affiliate evidence

Revision ID: 5f2c8a1d740e
Revises: 3d5a7c9e214b
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "5f2c8a1d740e"
down_revision: str | None = "3d5a7c9e214b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "affiliate_link_proofs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("requested_at", UTCDateTime(), nullable=False),
        sa.Column("responded_at", UTCDateTime(), nullable=False),
        sa.Column("source_external_product_id", sa.String(length=160), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("short_link", sa.Text(), nullable=False),
        sa.Column("official_endpoint_host", sa.String(length=253), nullable=False),
        sa.Column("credential_profile_id", sa.String(length=80), nullable=False),
        sa.Column("contract_version", sa.String(length=80), nullable=False),
        sa.Column("sub_ids", sa.JSON(), nullable=False),
        sa.Column("generation_state", sa.String(length=32), nullable=False),
        sa.Column("official_response_validated", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["affiliate_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_affiliate_link_proof_candidate"),
    )
    op.create_index(
        "ix_affiliate_link_proofs_product",
        "affiliate_link_proofs",
        ["provider", "source_external_product_id"],
        unique=False,
    )
    op.create_table(
        "shopee_product_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.String(length=80), nullable=False),
        sa.Column("item_id", sa.String(length=80), nullable=False),
        sa.Column("selected_variation_id", sa.String(length=160), nullable=True),
        sa.Column("price_min", sa.Numeric(18, 2), nullable=False),
        sa.Column("price_max", sa.Numeric(18, 2), nullable=False),
        sa.Column("selected_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("selected_variation_available", sa.Boolean(), nullable=True),
        sa.Column("range_semantics_confirmed", sa.Boolean(), nullable=False),
        sa.Column("official_image_url", sa.Text(), nullable=True),
        sa.Column("queried_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["affiliate_candidates.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_shopee_snapshot_candidate"),
    )
    op.create_index(
        "ix_shopee_snapshots_product_queried",
        "shopee_product_snapshots",
        ["product_id", "queried_at"],
        unique=False,
    )
    with op.batch_alter_table("deals") as batch_op:
        batch_op.add_column(sa.Column("price_min", sa.Numeric(18, 2), nullable=True))
        batch_op.add_column(sa.Column("price_max", sa.Numeric(18, 2), nullable=True))
        batch_op.add_column(sa.Column("selected_price", sa.Numeric(18, 2), nullable=True))
        batch_op.add_column(sa.Column("price_display_mode", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("variation_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("available", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("affiliate_proof_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_deals_affiliate_proof",
            "affiliate_link_proofs",
            ["affiliate_proof_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("deals") as batch_op:
        batch_op.drop_constraint("fk_deals_affiliate_proof", type_="foreignkey")
        batch_op.drop_column("affiliate_proof_id")
        batch_op.drop_column("available")
        batch_op.drop_column("variation_id")
        batch_op.drop_column("price_display_mode")
        batch_op.drop_column("selected_price")
        batch_op.drop_column("price_max")
        batch_op.drop_column("price_min")
    op.drop_index("ix_shopee_snapshots_product_queried", table_name="shopee_product_snapshots")
    op.drop_table("shopee_product_snapshots")
    op.drop_index("ix_affiliate_link_proofs_product", table_name="affiliate_link_proofs")
    op.drop_table("affiliate_link_proofs")
