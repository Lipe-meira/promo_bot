"""Complete-message coin previews without changing singleton evidence or history.

Revision ID: b8c2e4f6a901
Revises: 9b3d5e7f1a20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c2e4f6a901"
down_revision = "9b3d5e7f1a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("COIN_MULTI_FOREIGN_KEY_INVALID")
    op.create_table(
        "aliexpress_coin_shadow_multi_previews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("history_use_id", sa.String(36)),
        sa.Column("source_message_fingerprint", sa.String(64), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("content_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_message_fingerprint", name="uq_coin_multi_preview_message"),
        sa.CheckConstraint(
            "length(source_message_fingerprint) = 64", name="ck_coin_multi_message_fp"
        ),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_coin_multi_preview_expiry",
        "aliexpress_coin_shadow_multi_previews",
        ["content_expires_at"],
    )
    op.create_table(
        "aliexpress_coin_shadow_multi_preview_occurrences",
        sa.Column(
            "preview_id",
            sa.Integer(),
            sa.ForeignKey("aliexpress_coin_shadow_multi_previews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("span_start", sa.Integer(), nullable=False),
        sa.Column("span_end", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("evidence_state", sa.String(24), nullable=False),
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("affiliate_link_generations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "evidence_state"],
            ["aliexpress_coin_shadow_evidence.id", "aliexpress_coin_shadow_evidence.state"],
            ondelete="RESTRICT",
            name="fk_coin_multi_ready_evidence",
        ),
        sa.CheckConstraint("evidence_state = 'READY'", name="ck_coin_multi_ready"),
        sa.CheckConstraint("ordinal BETWEEN 0 AND 2", name="ck_coin_multi_ordinal"),
        sa.CheckConstraint("span_start >= 0 AND span_end > span_start", name="ck_coin_multi_span"),
    )
    with op.batch_alter_table("aliexpress_coin_shadow_deliveries") as batch:
        batch.add_column(sa.Column("multi_preview_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_coin_delivery_multi_preview",
            "aliexpress_coin_shadow_multi_previews",
            ["multi_preview_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_coin_delivery_preview_exclusive", "preview_id IS NULL OR multi_preview_id IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Preflight BEFORE any DDL, including durable uses whose operational parent expired.
    for table in (
        "aliexpress_coin_shadow_multi_previews",
        "aliexpress_coin_shadow_multi_preview_occurrences",
    ):
        if bind.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first():
            raise RuntimeError("COIN_MULTI_DOWNGRADE_BLOCKED_NONEMPTY")
    if bind.exec_driver_sql(
        "SELECT 1 FROM affiliate_link_uses WHERE operational_kind IN ('coin-multi-preview','coin-multi-delivery') LIMIT 1"
    ).first():
        raise RuntimeError("COIN_MULTI_DOWNGRADE_BLOCKED_NONEMPTY")
    if bind.exec_driver_sql(
        "SELECT 1 FROM aliexpress_coin_shadow_deliveries WHERE multi_preview_id IS NOT NULL LIMIT 1"
    ).first():
        raise RuntimeError("COIN_MULTI_DOWNGRADE_BLOCKED_NONEMPTY")
    with op.batch_alter_table("aliexpress_coin_shadow_deliveries") as batch:
        batch.drop_constraint("ck_coin_delivery_preview_exclusive", type_="check")
        batch.drop_constraint("fk_coin_delivery_multi_preview", type_="foreignkey")
        batch.drop_column("multi_preview_id")
    op.drop_table("aliexpress_coin_shadow_multi_preview_occurrences")
    op.drop_index(
        "ix_coin_multi_preview_expiry", table_name="aliexpress_coin_shadow_multi_previews"
    )
    op.drop_table("aliexpress_coin_shadow_multi_previews")
