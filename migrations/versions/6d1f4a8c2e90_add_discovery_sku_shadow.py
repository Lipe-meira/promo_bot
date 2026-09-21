"""Add isolated SKU refinement evidence to the discovery shadow.

Revision ID: 6d1f4a8c2e90
Revises: c4e7a1d92b60
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "6d1f4a8c2e90"
down_revision: str | None = "c4e7a1d92b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aliexpress_discovery_sku_refinement_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_run_id", sa.Integer(), nullable=False),
        sa.Column("profile_name", sa.String(64), nullable=False),
        sa.Column("requirements_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime()),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", UTCDateTime()),
        sa.Column("max_refined_products", sa.Integer(), nullable=False),
        sa.Column("max_sku_api_calls", sa.Integer(), nullable=False),
        sa.Column("minimum_drop_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("api_call_count", sa.Integer(), nullable=False),
        sa.Column("refined_count", sa.Integer(), nullable=False),
        sa.Column("cache_hit_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False),
        sa.Column("stop_reason", sa.String(80)),
        sa.Column("error_code", sa.String(80)),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["aliexpress_discovery_runs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "length(requirements_fingerprint) = 64", name="ck_sku_run_requirements_fp"
        ),
        sa.CheckConstraint(
            "state IN ('RUNNING','COMPLETED','STOPPED','REVIEW_REQUIRED','UNCERTAIN')",
            name="ck_sku_run_state",
        ),
        sa.CheckConstraint("max_refined_products BETWEEN 1 AND 20", name="ck_sku_run_max_products"),
        sa.CheckConstraint(
            "max_sku_api_calls BETWEEN 1 AND max_refined_products", name="ck_sku_run_max_calls"
        ),
        sa.CheckConstraint(
            "api_call_count BETWEEN 0 AND max_sku_api_calls AND "
            "refined_count BETWEEN 0 AND max_refined_products AND "
            "cache_hit_count >= 0 AND snapshot_count >= 0",
            name="ck_sku_run_counters",
        ),
        sa.CheckConstraint("minimum_drop_percent > 0", name="ck_sku_run_min_drop"),
        sa.CheckConstraint(
            "(state = 'RUNNING' AND lease_token IS NOT NULL AND lease_until IS NOT NULL "
            "AND finished_at IS NULL) OR (state != 'RUNNING' AND lease_token IS NULL "
            "AND lease_until IS NULL AND finished_at IS NOT NULL)",
            name="ck_sku_run_lease",
        ),
    )
    op.create_index(
        "ix_sku_runs_state_lease",
        "aliexpress_discovery_sku_refinement_runs",
        ["state", "lease_until"],
    )
    op.create_table(
        "aliexpress_discovery_sku_claims",
        sa.Column("sku_query_fingerprint", sa.String(64), primary_key=True),
        sa.Column("owner_run_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("lease_token", sa.String(32), nullable=False),
        sa.Column("claimed_at", UTCDateTime(), nullable=False),
        sa.Column("lease_until", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_run_id"], ["aliexpress_discovery_sku_refinement_runs.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("length(sku_query_fingerprint) = 64", name="ck_sku_claim_fp"),
    )
    op.create_index("ix_sku_claims_lease", "aliexpress_discovery_sku_claims", ["lease_until"])
    op.create_table(
        "aliexpress_discovery_sku_cache",
        sa.Column("sku_query_fingerprint", sa.String(64), primary_key=True),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("sku_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(sku_query_fingerprint) = 64", name="ck_sku_cache_fp"),
        sa.CheckConstraint("sku_count BETWEEN 1 AND 19", name="ck_sku_cache_count"),
    )
    op.create_index("ix_sku_cache_expiry", "aliexpress_discovery_sku_cache", ["expires_at"])
    op.create_table(
        "aliexpress_discovery_sku_cache_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku_query_fingerprint", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("sku_id", sa.String(160), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_with_tax", sa.Numeric(18, 2)),
        sa.Column("sale_price_with_tax", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_percent", sa.Numeric(8, 4)),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sku_query_fingerprint"],
            ["aliexpress_discovery_sku_cache.sku_query_fingerprint"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("sku_query_fingerprint", "ordinal", name="uq_sku_cache_ordinal"),
        sa.UniqueConstraint("sku_query_fingerprint", "sku_id", name="uq_sku_cache_sku"),
        sa.CheckConstraint("ordinal >= 0", name="ck_sku_cache_item_ordinal"),
        sa.CheckConstraint("sale_price_with_tax > 0", name="ck_sku_cache_item_price"),
        sa.CheckConstraint("currency = 'BRL'", name="ck_sku_cache_item_currency"),
    )
    op.create_table(
        "aliexpress_discovery_sku_refinement_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("refinement_run_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("sku_query_fingerprint", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("source_product_score", sa.Integer(), nullable=False),
        sa.Column("selected_sku_id", sa.String(160)),
        sa.Column("sale_price_with_tax", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("history_median", sa.Numeric(18, 2)),
        sa.Column("history_snapshot_count", sa.Integer(), nullable=False),
        sa.Column("price_drop_percent", sa.Numeric(8, 4)),
        sa.Column("classification", sa.String(48)),
        sa.Column("rank_position", sa.Integer()),
        sa.Column("error_code", sa.String(80)),
        sa.ForeignKeyConstraint(
            ["refinement_run_id"],
            ["aliexpress_discovery_sku_refinement_runs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("refinement_run_id", "product_id", name="uq_sku_item_run_product"),
        sa.CheckConstraint("origin IN ('LIVE','CACHE')", name="ck_sku_item_origin"),
        sa.CheckConstraint(
            "state IN ('MATCHED','NO_MATCH','AMBIGUOUS','REVIEW_REQUIRED')",
            name="ck_sku_item_state",
        ),
        sa.CheckConstraint(
            "(state = 'MATCHED' AND selected_sku_id IS NOT NULL "
            "AND sale_price_with_tax IS NOT NULL AND sale_price_with_tax > 0 "
            "AND currency IS NOT NULL AND currency = 'BRL') "
            "OR (state != 'MATCHED' AND selected_sku_id IS NULL "
            "AND sale_price_with_tax IS NULL AND currency IS NULL)",
            name="ck_sku_item_selection",
        ),
        sa.CheckConstraint("source_product_score BETWEEN 0 AND 100", name="ck_sku_item_score"),
        sa.CheckConstraint("history_snapshot_count >= 0", name="ck_sku_item_history_count"),
    )
    op.create_table(
        "aliexpress_discovery_sku_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.String(160), nullable=False),
        sa.Column("sale_price_with_tax", sa.Numeric(18, 2), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["aliexpress_discovery_sku_refinement_items.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("item_id", "sku_id", name="uq_sku_match_item_sku"),
        sa.CheckConstraint("sale_price_with_tax > 0", name="ck_sku_match_price"),
    )
    op.create_table(
        "aliexpress_discovery_sku_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("refinement_run_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("sku_id", sa.String(160), nullable=False),
        sa.Column("price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("price_basis", sa.String(32), nullable=False),
        sa.Column("source_operation", sa.String(120), nullable=False),
        sa.ForeignKeyConstraint(
            ["refinement_run_id"],
            ["aliexpress_discovery_sku_refinement_runs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "refinement_run_id", "product_id", "sku_id", name="uq_sku_snapshot_run_product_sku"
        ),
        sa.CheckConstraint("price > 0", name="ck_sku_snapshot_price"),
        sa.CheckConstraint("currency = 'BRL'", name="ck_sku_snapshot_currency"),
        sa.CheckConstraint("price_basis = 'SALE_PRICE_WITH_TAX'", name="ck_sku_snapshot_basis"),
        sa.CheckConstraint(
            "source_operation = 'aliexpress.affiliate.product.sku.detail.get'",
            name="ck_sku_snapshot_source",
        ),
    )
    op.create_index(
        "ix_sku_snapshot_identity_time",
        "aliexpress_discovery_sku_price_snapshots",
        ["product_id", "sku_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sku_snapshot_identity_time", "aliexpress_discovery_sku_price_snapshots")
    op.drop_table("aliexpress_discovery_sku_price_snapshots")
    op.drop_table("aliexpress_discovery_sku_matches")
    op.drop_table("aliexpress_discovery_sku_refinement_items")
    op.drop_table("aliexpress_discovery_sku_cache_items")
    op.drop_index("ix_sku_cache_expiry", "aliexpress_discovery_sku_cache")
    op.drop_table("aliexpress_discovery_sku_cache")
    op.drop_index("ix_sku_claims_lease", "aliexpress_discovery_sku_claims")
    op.drop_table("aliexpress_discovery_sku_claims")
    op.drop_index("ix_sku_runs_state_lease", "aliexpress_discovery_sku_refinement_runs")
    op.drop_table("aliexpress_discovery_sku_refinement_runs")
