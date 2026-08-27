"""add durable Telegram relay

Revision ID: 8ea6f1e5c7b2
Revises: c501868f1334
Create Date: 2026-08-27
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "8ea6f1e5c7b2"
down_revision: str | None = "c501868f1334"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_messages") as batch_op:
        batch_op.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("last_attempt_at", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("next_attempt_at", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("processing_started_at", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("processing_lease_until", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("completed_at", UTCDateTime(), nullable=True))

    source_messages = sa.table(
        "source_messages",
        sa.column("id", sa.Integer()),
        sa.column("original_text", sa.Text()),
        sa.column("links", sa.JSON()),
        sa.column("content_hash", sa.String(length=64)),
        sa.column("processing_status", sa.String(length=40)),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(source_messages.c.id, source_messages.c.original_text, source_messages.c.links)
    ).all()
    for row in rows:
        legacy_links = row.links if isinstance(row.links, list) else []
        structured_links = [
            item
            if isinstance(item, dict)
            else {"url": str(item), "source": "TEXT", "ordinal": ordinal}
            for ordinal, item in enumerate(legacy_links)
        ]
        payload = json.dumps(
            {"text": row.original_text, "links": structured_links},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        connection.execute(
            sa.update(source_messages)
            .where(source_messages.c.id == row.id)
            .values(
                links=structured_links,
                content_hash=hashlib.sha256(payload).hexdigest(),
                processing_status="RECEIVED",
            )
        )
    with op.batch_alter_table("source_messages") as batch_op:
        batch_op.alter_column("content_hash", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("attempt_count", server_default=None)
        batch_op.create_index(
            "ix_source_messages_recovery",
            ["processing_status", "next_attempt_at", "processing_lease_until"],
            unique=False,
        )

    op.create_table(
        "source_message_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_url", sa.Text(), nullable=False),
        sa.Column("expanded_url", sa.Text(), nullable=True),
        sa.Column("redirect_count", sa.Integer(), nullable=False),
        sa.Column("store", sa.String(length=32), nullable=True),
        sa.Column("external_product_id", sa.String(length=160), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_message_id"], ["source_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_message_id", "input_hash", name="uq_source_message_link_hash"),
    )
    op.create_index(
        "ix_source_message_links_product",
        "source_message_links",
        ["store", "external_product_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_message_links_state", "source_message_links", ["state"], unique=False
    )
    op.create_table(
        "telegram_channel_checkpoints",
        sa.Column("channel_id", sa.String(length=128), nullable=False),
        sa.Column("last_persisted_message_id", sa.Integer(), nullable=True),
        sa.Column("last_persisted_at", UTCDateTime(), nullable=True),
        sa.Column("last_catch_up_at", UTCDateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("channel_id"),
    )


def downgrade() -> None:
    op.drop_table("telegram_channel_checkpoints")
    op.drop_index("ix_source_message_links_state", table_name="source_message_links")
    op.drop_index("ix_source_message_links_product", table_name="source_message_links")
    op.drop_table("source_message_links")
    with op.batch_alter_table("source_messages") as batch_op:
        batch_op.drop_index("ix_source_messages_recovery")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("processing_lease_until")
        batch_op.drop_column("processing_started_at")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("last_attempt_at")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("content_hash")
