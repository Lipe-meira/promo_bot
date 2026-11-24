"""Manual registry of Telegram source channels and the test destination of a workspace.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_CHAT_ID = "chat_id ~ '^-100[1-9][0-9]{0,14}$'"


def upgrade() -> None:
    op.create_table(
        "telegram_source_channels",
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("chat_id", sa.String(19), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("workspace_id", "chat_id", name="pk_telegram_source_channels"),
        sa.CheckConstraint(_CHAT_ID, name="ck_telegram_source_channels_chat_id_format"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_telegram_source_channels_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "telegram_destinations",
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("chat_id", sa.String(19), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_telegram_destinations"),
        sa.CheckConstraint(_CHAT_ID, name="ck_telegram_destinations_chat_id_format"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_telegram_destinations_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("telegram_destinations")
    op.drop_table("telegram_source_channels")
