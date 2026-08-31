"""add guarded Telegram delivery outbox

Revision ID: 7a4d1e9c82f6
Revises: 5f2c8a1d740e
Create Date: 2026-08-31
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "7a4d1e9c82f6"
down_revision: str | None = "5f2c8a1d740e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("target_chat_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("next_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("lease_until", UTCDateTime(), nullable=True),
        sa.Column("telegram_message_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", UTCDateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_summary", sa.String(length=500), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deal_id", name="uq_delivery_deal"),
        sa.UniqueConstraint("idempotency_key", name="uq_delivery_idempotency_key"),
    )
    op.create_index(
        "ix_deliveries_recovery",
        "deliveries",
        ["state", "next_attempt_at", "lease_until"],
        unique=False,
    )
    _preserve_legacy_delivery_state()
    with op.batch_alter_table("deals") as batch_op:
        batch_op.drop_column("send_status")


def _preserve_legacy_delivery_state() -> None:
    connection = op.get_bind()
    now = datetime.now(UTC)
    deals = sa.table(
        "deals",
        sa.column("id", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("send_status", sa.String()),
    )
    deliveries = sa.table(
        "deliveries",
        sa.column("deal_id", sa.Integer()),
        sa.column("idempotency_key", sa.String()),
        sa.column("target_chat_id", sa.String()),
        sa.column("state", sa.String()),
        sa.column("attempt_count", sa.Integer()),
        sa.column("error_code", sa.String()),
        sa.column("created_at", UTCDateTime()),
        sa.column("updated_at", UTCDateTime()),
    )
    legacy_rows = connection.execute(
        sa.select(deals.c.id, deals.c.status, deals.c.send_status).where(
            sa.or_(deals.c.status == "SENT", deals.c.send_status != "NOT_SENT")
        )
    ).all()
    for row in legacy_rows:
        connection.execute(
            sa.insert(deliveries).values(
                deal_id=row.id,
                idempotency_key=f"legacy-deal:{row.id}",
                target_chat_id="legacy-unknown",
                state="MANUAL_REVIEW",
                attempt_count=0,
                error_code="LEGACY_DELIVERY_WITHOUT_MESSAGE_ID",
                created_at=now,
                updated_at=now,
            )
        )
        if row.status == "SENT":
            connection.execute(sa.update(deals).where(deals.c.id == row.id).values(status="READY"))


def downgrade() -> None:
    with op.batch_alter_table("deals") as batch_op:
        batch_op.add_column(
            sa.Column(
                "send_status", sa.String(length=32), server_default="NOT_SENT", nullable=False
            )
        )
    op.drop_index("ix_deliveries_recovery", table_name="deliveries")
    op.drop_table("deliveries")
