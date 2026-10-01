"""Add durable affiliate generations and uses without retroactive confirmation.

Revision ID: 9b3d5e7f1a20
Revises: 7e2b9c4d5a10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9b3d5e7f1a20"
down_revision = "7e2b9c4d5a10"
branch_labels = None
depends_on = None

POINTERS = (
    ("affiliate_link_proofs", "generation_id"),
    ("aliexpress_coin_shadow_evidence", "generation_id"),
    ("affiliate_shadow_previews", "history_use_id"),
    ("affiliate_shadow_deliveries", "history_use_id"),
    ("aliexpress_coin_shadow_previews", "history_use_id"),
    ("aliexpress_coin_shadow_deliveries", "history_use_id"),
)


def columns(spec: dict[str, tuple[sa.types.TypeEngine, bool]]) -> list[sa.Column]:
    return [sa.Column(name, kind, nullable=nullable) for name, (kind, nullable) in spec.items()]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("AFFILIATE_HISTORY_FOREIGN_KEY_INVALID")
    text_fields = {
        "platform": (32, False),
        "provider": (40, False),
        "operation": (120, False),
        "scope": (16, False),
        "state": (24, False),
        "identity_key": (200, False),
        "key_fingerprint": (64, True),
        "input_fingerprint": (64, True),
        "tracking_fingerprint": (64, True),
        "call_id": (36, True),
        "lease_token": (36, True),
        "contract_version": (80, True),
        "correlation_mode": (48, True),
        "origin_missing_reason": (80, True),
        "error_code": (80, True),
        "request_reason": (40, True),
        "legacy_kind": (32, True),
    }
    dates = (
        "lease_until",
        "prepared_at",
        "call_started_at",
        "generated_at",
        "finished_at",
        "expires_at",
        "operator_requested_at",
    )
    op.create_table(
        "affiliate_link_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        *columns({k: (sa.String(length), null) for k, (length, null) in text_fields.items()}),
        *columns({k: (sa.DateTime(timezone=True), True) for k in dates}),
        *columns(
            {k: (sa.Integer(), True) for k in ("promotion_link_type", "call_ordinal", "legacy_id")}
        ),
        sa.Column("generated_url", sa.Text()),
        *columns(
            {k: (sa.JSON(), True) for k in ("validation_facts", "origin", "legacy_record_snapshot")}
        ),
        sa.Column("tracking_confirmed", sa.Boolean(), nullable=False),
        sa.Column("attribution_unverified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('REQUESTED','PREPARED','CALL_STARTED','CONFIRMED','REJECTED','FAILED','UNCERTAIN')",
            name="ck_history_generation_state",
        ),
        sa.CheckConstraint("scope IN ('runtime','shadow')", name="ck_history_generation_scope"),
        sa.CheckConstraint("attribution_unverified = 1", name="ck_history_no_financial_proof"),
        sa.CheckConstraint(
            "state != 'CONFIRMED' OR (generated_url IS NOT NULL AND generated_at IS NOT NULL AND tracking_confirmed = 1 AND contract_version IS NOT NULL AND correlation_mode IS NOT NULL AND validation_facts IS NOT NULL AND call_started_at IS NOT NULL)",
            name="ck_history_confirmation",
        ),
        sa.CheckConstraint(
            "state = 'CONFIRMED' OR (generated_url IS NULL AND generated_at IS NULL AND tracking_confirmed = 0)",
            name="ck_history_unconfirmed_no_link",
        ),
        sa.CheckConstraint(
            "input_fingerprint IS NULL OR length(input_fingerprint) = 64",
            name="ck_history_input_fingerprint",
        ),
        sa.CheckConstraint(
            "tracking_fingerprint IS NULL OR length(tracking_fingerprint) = 64",
            name="ck_history_tracking_fingerprint",
        ),
    )
    for name, fields in (
        ("ix_history_generation_identity", ["scope", "platform", "identity_key"]),
        ("ix_history_generation_time", ["generated_at"]),
        ("ix_history_generation_call", ["call_id"]),
    ):
        op.create_index(name, "affiliate_link_generations", fields)
    op.create_table(
        "affiliate_link_uses",
        sa.Column("id", sa.String(36), primary_key=True),
        *columns(
            {
                "scope": (sa.String(16), False),
                "kind": (sa.String(24), False),
                "state": (sa.String(32), False),
                "occurred_at": (sa.DateTime(timezone=True), False),
                "started_at": (sa.DateTime(timezone=True), True),
                "finished_at": (sa.DateTime(timezone=True), True),
                "origin": (sa.JSON(), True),
                "origin_missing_reason": (sa.String(80), True),
                "destination_key": (sa.String(64), True),
                "operational_kind": (sa.String(40), True),
                "operational_id": (sa.Integer(), True),
                "telegram_message_id": (sa.String(128), True),
                "error_code": (sa.String(80), True),
                "created_at": (sa.DateTime(timezone=True), False),
                "updated_at": (sa.DateTime(timezone=True), False),
            }
        ),
        sa.CheckConstraint("scope IN ('runtime','shadow')", name="ck_history_use_scope"),
        sa.CheckConstraint(
            "kind IN ('PREVIEW','EXPLICIT_OUTPUT','SEND')", name="ck_history_use_kind"
        ),
        sa.CheckConstraint(
            "state IN ('PREVIEW_READY','OUTPUT_RECORDED','SEND_RESERVED','SEND_IN_FLIGHT','SEND_CONFIRMED','SEND_FAILED','SEND_UNCERTAIN')",
            name="ck_history_use_state",
        ),
    )
    op.create_index("ix_history_use_time", "affiliate_link_uses", ["occurred_at"])
    op.create_table(
        "affiliate_link_use_links",
        sa.Column(
            "use_id",
            sa.String(36),
            sa.ForeignKey("affiliate_link_uses.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "generation_id",
            sa.String(36),
            sa.ForeignKey("affiliate_link_generations.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_history_use_link_ordinal"),
    )
    # Nullable operational pointers are intentionally not FKs: deleting/purging an
    # operational row must never delete durable history or imply current validity.
    for table, field in POINTERS:
        op.add_column(table, sa.Column(field, sa.String(36), nullable=True))
    op.execute(
        "CREATE TRIGGER history_legacy_snapshot_immutable BEFORE UPDATE OF legacy_record_snapshot "
        "ON affiliate_link_generations WHEN OLD.legacy_record_snapshot IS NOT NULL "
        "AND NEW.legacy_record_snapshot IS NOT OLD.legacy_record_snapshot "
        "BEGIN SELECT RAISE(ABORT,'AFFILIATE_HISTORY_SNAPSHOT_IMMUTABLE'); END"
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("affiliate_link_use_links", "affiliate_link_uses", "affiliate_link_generations"):
        if bind.scalar(sa.text(f"SELECT COUNT(*) FROM {table}")):
            raise RuntimeError("AFFILIATE_HISTORY_DOWNGRADE_BLOCKED_NONEMPTY")
    op.execute("DROP TRIGGER history_legacy_snapshot_immutable")
    for table, field in reversed(POINTERS):
        op.execute(f"ALTER TABLE {table} DROP COLUMN {field}")
    op.drop_table("affiliate_link_use_links")
    op.drop_table("affiliate_link_uses")
    op.drop_table("affiliate_link_generations")
