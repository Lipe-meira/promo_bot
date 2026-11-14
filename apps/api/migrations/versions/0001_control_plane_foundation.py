"""Control-plane foundation: users, invitations, workspaces and memberships.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else _NOW,
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("clerk_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        _timestamp("created_at"),
        _timestamp("disabled_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
    )
    op.create_table(
        "invitations",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        _timestamp("created_at"),
        _timestamp("revoked_at", nullable=True),
        _timestamp("accepted_at", nullable=True),
        sa.Column("accepted_by_user_id", _UUID, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("email", name="uq_invitations_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_invitations_email_lowercase"),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_invitations_accepted_by_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("owner_user_id", name="uq_workspaces_owner_user_id"),
        sa.CheckConstraint("version >= 1", name="ck_workspaces_version_positive"),
        sa.CheckConstraint("char_length(name) BETWEEN 2 AND 60", name="ck_workspaces_name_length"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_workspaces_owner_user_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name="pk_workspace_memberships"),
        sa.CheckConstraint("role IN ('owner')", name="ck_workspace_memberships_role_known"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_memberships_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspace_memberships_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_memberships_user_id", table_name="workspace_memberships")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
    op.drop_table("invitations")
    op.drop_table("users")
