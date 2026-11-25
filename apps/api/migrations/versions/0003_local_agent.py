"""Local agent: one paired agent per workspace, its pairing code and the pending command.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("paired_at", _TIMESTAMP, nullable=False, server_default=_NOW),
        sa.Column("last_seen_at", _TIMESTAMP, nullable=True),
        sa.Column("process_state", sa.String(10), nullable=False, server_default="stopped"),
        sa.Column("last_event", sa.String(20), nullable=True),
        sa.Column("command_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("command_action", sa.String(5), nullable=True),
        sa.Column("command_issued_at", _TIMESTAMP, nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_agents"),
        sa.UniqueConstraint("token_hash", name="uq_agents_token_hash"),
        sa.CheckConstraint(
            "process_state IN ('stopped', 'running')", name="ck_agents_process_state_known"
        ),
        sa.CheckConstraint(
            "last_event IN ('start_failed', 'exited')", name="ck_agents_last_event_known"
        ),
        sa.CheckConstraint(
            "command_action IN ('start', 'stop')", name="ck_agents_command_action_known"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_agents_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "agent_pairing_codes",
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False, server_default=_NOW),
        sa.PrimaryKeyConstraint("workspace_id", name="pk_agent_pairing_codes"),
        sa.UniqueConstraint("code_hash", name="uq_agent_pairing_codes_code_hash"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_agent_pairing_codes_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_pairing_codes")
    op.drop_table("agents")
