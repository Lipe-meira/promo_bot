"""separate internal review delivery from external disclosure

Revision ID: 9c7e2a4f1b63
Revises: 7a4d1e9c82f6
Create Date: 2026-09-01
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "9c7e2a4f1b63"
down_revision: str | None = "7a4d1e9c82f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MERCADO_LIVRE_ID = re.compile(r"^MLB[0-9]{5,}$")


def upgrade() -> None:
    with op.batch_alter_table("deals") as batch_op:
        batch_op.add_column(
            sa.Column(
                "review_state",
                sa.String(length=40),
                server_default="AWAITING_INTERNAL_REVIEW",
                nullable=False,
            )
        )
    with op.batch_alter_table("deliveries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "purpose",
                sa.String(length=40),
                server_default="INTERNAL_REVIEW",
                nullable=False,
            )
        )
        batch_op.drop_constraint("uq_delivery_deal", type_="unique")
        batch_op.create_unique_constraint("uq_delivery_deal_purpose", ["deal_id", "purpose"])
    _backfill_mercado_livre_candidates()


def _backfill_mercado_livre_candidates() -> None:
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
    rows = list(
        connection.execute(
            sa.select(links).where(
                links.c.store == "mercadolivre",
                links.c.external_product_id.is_not(None),
                links.c.canonical_url.is_not(None),
                links.c.affiliate_candidate_id.is_(None),
                sa.or_(
                    links.c.state == "PENDING_AFFILIATE",
                    links.c.reason_code == "DUPLICATE_CANONICAL",
                ),
            )
        ).mappings()
    )
    for row in rows:
        external_id = row["external_product_id"]
        canonical_url = row["canonical_url"]
        if not isinstance(external_id, str) or not _MERCADO_LIVRE_ID.fullmatch(external_id):
            continue
        expected_url = f"https://produto.mercadolivre.com.br/MLB-{external_id[3:]}"
        if canonical_url != expected_url:
            continue
        candidate_id = connection.execute(
            sa.select(candidates.c.id).where(
                candidates.c.store == "mercadolivre",
                candidates.c.external_product_id == external_id,
                candidates.c.variation_key == "",
            )
        ).scalar_one_or_none()
        if candidate_id is None:
            connection.execute(
                sa.insert(candidates).values(
                    store="mercadolivre",
                    external_product_id=external_id,
                    variation_key="",
                    canonical_url=canonical_url,
                    state="PENDING_AFFILIATE",
                    attempt_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            candidate_id = connection.execute(
                sa.select(candidates.c.id).where(
                    candidates.c.store == "mercadolivre",
                    candidates.c.external_product_id == external_id,
                    candidates.c.variation_key == "",
                )
            ).scalar_one()
        connection.execute(
            sa.update(links)
            .where(links.c.id == row["id"])
            .values(affiliate_candidate_id=candidate_id)
        )


def downgrade() -> None:
    with op.batch_alter_table("deliveries") as batch_op:
        batch_op.drop_constraint("uq_delivery_deal_purpose", type_="unique")
        batch_op.create_unique_constraint("uq_delivery_deal", ["deal_id"])
        batch_op.drop_column("purpose")
    with op.batch_alter_table("deals") as batch_op:
        batch_op.drop_column("review_state")
