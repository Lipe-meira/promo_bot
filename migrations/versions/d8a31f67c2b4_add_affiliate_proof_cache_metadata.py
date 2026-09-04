"""add affiliate proof cache metadata

Revision ID: d8a31f67c2b4
Revises: b3f26d8a914c
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "d8a31f67c2b4"
down_revision: str | None = "b3f26d8a914c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "affiliate_link_proofs",
        sa.Column("promotion_link_type", sa.Integer(), nullable=True),
    )
    op.add_column(
        "affiliate_link_proofs",
        sa.Column("tracking_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "affiliate_link_proofs",
        sa.Column("expires_at", UTCDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("affiliate_link_proofs", "expires_at")
    op.drop_column("affiliate_link_proofs", "tracking_fingerprint")
    op.drop_column("affiliate_link_proofs", "promotion_link_type")
