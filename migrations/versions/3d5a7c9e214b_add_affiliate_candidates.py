"""add independent affiliate candidates

Revision ID: 3d5a7c9e214b
Revises: 8ea6f1e5c7b2
Create Date: 2026-08-31
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "3d5a7c9e214b"
down_revision: str | None = "8ea6f1e5c7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "affiliate_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store", sa.String(length=32), nullable=False),
        sa.Column("external_product_id", sa.String(length=160), nullable=False),
        sa.Column("variation_key", sa.String(length=160), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("next_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("processing_started_at", UTCDateTime(), nullable=True),
        sa.Column("processing_lease_until", UTCDateTime(), nullable=True),
        sa.Column("enriched_at", UTCDateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "store",
            "external_product_id",
            "variation_key",
            name="uq_affiliate_candidate_product_variation",
        ),
    )
    op.create_index(
        "ix_affiliate_candidates_recovery",
        "affiliate_candidates",
        ["state", "next_attempt_at", "processing_lease_until"],
        unique=False,
    )
    with op.batch_alter_table("source_message_links") as batch_op:
        batch_op.add_column(sa.Column("affiliate_candidate_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_source_message_links_affiliate_candidate",
            "affiliate_candidates",
            ["affiliate_candidate_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_source_message_links_candidate", ["affiliate_candidate_id"], unique=False
        )

    _backfill_existing_shopee_links()


def _backfill_existing_shopee_links() -> None:
    connection = op.get_bind()
    now = datetime.now(UTC)
    links = sa.table(
        "source_message_links",
        sa.column("id", sa.Integer()),
        sa.column("store", sa.String()),
        sa.column("external_product_id", sa.String()),
        sa.column("canonical_url", sa.Text()),
        sa.column("state", sa.String()),
        sa.column("reason_code", sa.String()),
        sa.column("affiliate_candidate_id", sa.Integer()),
    )
    candidates = sa.table(
        "affiliate_candidates",
        sa.column("id", sa.Integer()),
        sa.column("store", sa.String()),
        sa.column("external_product_id", sa.String()),
        sa.column("variation_key", sa.String()),
        sa.column("canonical_url", sa.Text()),
        sa.column("state", sa.String()),
        sa.column("attempt_count", sa.Integer()),
        sa.column("created_at", UTCDateTime()),
        sa.column("updated_at", UTCDateTime()),
    )
    rows = connection.execute(
        sa.select(
            links.c.id,
            links.c.external_product_id,
            links.c.canonical_url,
        ).where(
            links.c.store == "shopee",
            links.c.external_product_id.is_not(None),
            links.c.canonical_url.is_not(None),
            sa.or_(
                links.c.state == "PENDING_AFFILIATE",
                links.c.reason_code == "DUPLICATE_CANONICAL",
            ),
        )
    ).all()
    for row in rows:
        existing = connection.execute(
            sa.select(candidates.c.id).where(
                candidates.c.store == "shopee",
                candidates.c.external_product_id == row.external_product_id,
                candidates.c.variation_key == "",
            )
        ).scalar_one_or_none()
        if existing is None:
            connection.execute(
                sa.insert(candidates).values(
                    store="shopee",
                    external_product_id=row.external_product_id,
                    variation_key="",
                    canonical_url=row.canonical_url,
                    state="PENDING_AFFILIATE",
                    attempt_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            existing = connection.execute(
                sa.select(candidates.c.id).where(
                    candidates.c.store == "shopee",
                    candidates.c.external_product_id == row.external_product_id,
                    candidates.c.variation_key == "",
                )
            ).scalar_one()
        connection.execute(
            sa.update(links).where(links.c.id == row.id).values(affiliate_candidate_id=existing)
        )


def downgrade() -> None:
    with op.batch_alter_table("source_message_links") as batch_op:
        batch_op.drop_index("ix_source_message_links_candidate")
        batch_op.drop_constraint("fk_source_message_links_affiliate_candidate", type_="foreignkey")
        batch_op.drop_column("affiliate_candidate_id")
    op.drop_index("ix_affiliate_candidates_recovery", table_name="affiliate_candidates")
    op.drop_table("affiliate_candidates")
