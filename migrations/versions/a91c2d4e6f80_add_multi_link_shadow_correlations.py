"""Add multi-link shadow correlations and direct delivery identity.

Revision ID: a91c2d4e6f80
Revises: f7a29b6c103e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "a91c2d4e6f80"
down_revision: str | None = "f7a29b6c103e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_messages") as batch_op:
        batch_op.add_column(
            sa.Column(
                "surface_metadata",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    op.create_table(
        "affiliate_shadow_preview_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("preview_id", sa.Integer(), nullable=False),
        sa.Column("source_message_link_id", sa.Integer(), nullable=False),
        sa.Column("affiliate_proof_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["preview_id"], ["affiliate_shadow_previews.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_link_id"], ["source_message_links.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["affiliate_proof_id"], ["affiliate_link_proofs.id"]),
        sa.UniqueConstraint(
            "preview_id",
            "source_message_link_id",
            name="uq_shadow_preview_link_source",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_shadow_preview_link_ordinal"),
        sa.CheckConstraint("occurrence_count >= 1", name="ck_shadow_preview_link_occurrence_count"),
    )
    op.create_index(
        "ix_shadow_preview_links_preview",
        "affiliate_shadow_preview_links",
        ["preview_id", "ordinal"],
        unique=False,
    )
    op.create_index(
        "ix_shadow_preview_links_proof",
        "affiliate_shadow_preview_links",
        ["affiliate_proof_id"],
        unique=False,
    )

    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE source_messages SET surface_metadata = :value"),
        {"value": '{"legacy_unknown":true}'},
    )
    connection.execute(
        sa.text(
            "INSERT INTO affiliate_shadow_preview_links "
            "(preview_id,source_message_link_id,affiliate_proof_id,ordinal,occurrence_count,"
            "cache_hit,created_at,updated_at) "
            "SELECT p.id, MIN(l.id), p.affiliate_proof_id, 0, p.replacement_count, p.cache_hit, "
            "p.created_at, p.updated_at "
            "FROM affiliate_shadow_previews AS p "
            "JOIN affiliate_link_proofs AS proof ON proof.id = p.affiliate_proof_id "
            "JOIN source_message_links AS l ON l.source_message_id = p.source_message_id "
            "AND l.affiliate_candidate_id = proof.candidate_id "
            "WHERE p.replacement_count >= 1 "
            "GROUP BY p.id"
        )
    )

    with op.batch_alter_table("affiliate_shadow_deliveries") as batch_op:
        batch_op.add_column(sa.Column("source_message_id", sa.Integer(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE affiliate_shadow_deliveries SET source_message_id = "
            "(SELECT source_message_id FROM affiliate_shadow_previews "
            "WHERE affiliate_shadow_previews.id = affiliate_shadow_deliveries.preview_id)"
        )
    )
    # Legacy schema allowed one delivery per preview, so distinct provider/store previews
    # could reserve the same source/destination. Keep the oldest fail-closed reservation:
    # every historical state already blocks another external attempt.
    connection.execute(
        sa.text(
            "DELETE FROM affiliate_shadow_deliveries "
            "WHERE id NOT IN ("
            "SELECT MIN(id) FROM affiliate_shadow_deliveries "
            "GROUP BY source_message_id, destination_key)"
        )
    )
    with op.batch_alter_table("affiliate_shadow_deliveries") as batch_op:
        batch_op.alter_column("source_message_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            "fk_shadow_delivery_source_message",
            "source_messages",
            ["source_message_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_shadow_delivery_source_destination",
            ["source_message_id", "destination_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("affiliate_shadow_deliveries") as batch_op:
        batch_op.drop_constraint("uq_shadow_delivery_source_destination", type_="unique")
        batch_op.drop_constraint("fk_shadow_delivery_source_message", type_="foreignkey")
        batch_op.drop_column("source_message_id")

    op.drop_index(
        "ix_shadow_preview_links_proof",
        table_name="affiliate_shadow_preview_links",
    )
    op.drop_index(
        "ix_shadow_preview_links_preview",
        table_name="affiliate_shadow_preview_links",
    )
    op.drop_table("affiliate_shadow_preview_links")

    with op.batch_alter_table("source_messages") as batch_op:
        batch_op.drop_column("surface_metadata")
