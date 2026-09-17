"""Add isolated AliExpress product discovery shadow storage.

Revision ID: c4e7a1d92b60
Revises: b72e4c9d1a30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from promo_bot.database.types import UTCDateTime

revision: str = "c4e7a1d92b60"
down_revision: str | None = "b72e4c9d1a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aliexpress_discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_name", sa.String(64), nullable=False),
        sa.Column("profile_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime()),
        sa.Column("lease_token", sa.String(32)),
        sa.Column("lease_until", UTCDateTime()),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column("max_api_calls", sa.Integer(), nullable=False),
        sa.Column("minimum_drop_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("api_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_product_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_reason", sa.String(80)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint("length(profile_fingerprint) = 64", name="ck_discovery_profile_fp"),
        sa.CheckConstraint(
            "state IN ('RUNNING','COMPLETED','STOPPED','REVIEW_REQUIRED','UNCERTAIN')",
            name="ck_discovery_run_state",
        ),
        sa.CheckConstraint("page_size BETWEEN 1 AND 50", name="ck_discovery_page_size"),
        sa.CheckConstraint("max_pages BETWEEN 1 AND 5", name="ck_discovery_max_pages"),
        sa.CheckConstraint("max_results BETWEEN 1 AND 250", name="ck_discovery_max_results"),
        sa.CheckConstraint("max_api_calls BETWEEN 1 AND 20", name="ck_discovery_max_calls"),
        sa.CheckConstraint("minimum_drop_percent > 0", name="ck_discovery_minimum_drop"),
        sa.CheckConstraint(
            "api_call_count >= 0 AND api_call_count <= max_api_calls",
            name="ck_discovery_api_call_count",
        ),
        sa.CheckConstraint(
            "cache_hit_count >= 0 AND page_count >= 0 AND received_count >= 0 "
            "AND snapshot_count >= 0",
            name="ck_discovery_nonnegative_counters",
        ),
        sa.CheckConstraint(
            "unique_product_count >= 0 AND unique_product_count <= max_results",
            name="ck_discovery_unique_count",
        ),
        sa.CheckConstraint(
            "state != 'RUNNING' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL "
            "AND finished_at IS NULL)",
            name="ck_discovery_running_lease",
        ),
        sa.CheckConstraint(
            "state = 'RUNNING' OR (lease_token IS NULL AND lease_until IS NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_discovery_terminal_without_lease",
        ),
    )
    op.create_index(
        "ix_discovery_runs_state_lease", "aliexpress_discovery_runs", ["state", "lease_until"]
    )
    op.create_table(
        "aliexpress_discovery_query_claims",
        sa.Column("query_fingerprint", sa.String(64), primary_key=True),
        sa.Column("tracking_fingerprint", sa.String(64), primary_key=True),
        sa.Column("owner_run_id", sa.Integer(), nullable=False),
        sa.Column("query_ordinal", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(32), nullable=False),
        sa.Column("claimed_at", UTCDateTime(), nullable=False),
        sa.Column("lease_until", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_run_id"], ["aliexpress_discovery_runs.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_claim_query_fp"),
        sa.CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_discovery_claim_tracking_fp"
        ),
        sa.CheckConstraint(
            "query_ordinal >= 0 AND page_no >= 1", name="ck_discovery_claim_position"
        ),
    )
    op.create_index(
        "ix_discovery_claims_lease", "aliexpress_discovery_query_claims", ["lease_until"]
    )
    op.create_table(
        "aliexpress_discovery_query_cache",
        sa.Column("query_fingerprint", sa.String(64), primary_key=True),
        sa.Column("tracking_fingerprint", sa.String(64), primary_key=True),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("current_record_count", sa.Integer()),
        sa.Column("total_record_count", sa.Integer()),
        sa.CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_cache_query_fp"),
        sa.CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_discovery_cache_tracking_fp"
        ),
        sa.CheckConstraint("item_count >= 0", name="ck_discovery_cache_item_count"),
        sa.CheckConstraint(
            "current_record_count IS NULL OR current_record_count >= 0",
            name="ck_discovery_cache_current_count",
        ),
        sa.CheckConstraint(
            "total_record_count IS NULL OR total_record_count >= 0",
            name="ck_discovery_cache_total_count",
        ),
    )
    op.create_index("ix_discovery_cache_expiry", "aliexpress_discovery_query_cache", ["expires_at"])
    op.create_table(
        "aliexpress_discovery_cache_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.Column("tracking_fingerprint", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("first_category_id", sa.String(160)),
        sa.Column("first_category_name", sa.Text()),
        sa.Column("second_category_id", sa.String(160)),
        sa.Column("second_category_name", sa.Text()),
        sa.Column("shop_id", sa.String(160)),
        sa.Column("shop_name", sa.Text()),
        sa.Column("target_brl_price", sa.Numeric(18, 2)),
        sa.Column("observed_prices", sa.JSON(), nullable=False),
        sa.Column("declared_discount_percent", sa.Numeric(8, 4)),
        sa.Column("commission_rate", sa.Numeric(8, 4)),
        sa.Column("hot_product_commission_rate", sa.Numeric(8, 4)),
        sa.Column("volume", sa.Integer()),
        sa.Column("completeness_score", sa.Integer(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_fingerprint", "tracking_fingerprint"],
            [
                "aliexpress_discovery_query_cache.query_fingerprint",
                "aliexpress_discovery_query_cache.tracking_fingerprint",
            ],
            ondelete="CASCADE",
            name="fk_discovery_cache_product_identity",
        ),
        sa.UniqueConstraint(
            "query_fingerprint",
            "tracking_fingerprint",
            "ordinal",
            name="uq_discovery_cache_product_ordinal",
        ),
        sa.CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_product_query_fp"),
        sa.CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_discovery_product_tracking_fp"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_discovery_product_ordinal"),
        sa.CheckConstraint(
            "target_brl_price IS NULL OR target_brl_price > 0",
            name="ck_discovery_product_positive_price",
        ),
        sa.CheckConstraint(
            "completeness_score BETWEEN 0 AND 10", name="ck_discovery_product_completeness"
        ),
    )
    op.create_index(
        "ix_discovery_cache_product_id", "aliexpress_discovery_cache_products", ["product_id"]
    )
    op.create_table(
        "aliexpress_discovery_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.Column("tracking_fingerprint", sa.String(64), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("price", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("source_operation", sa.String(120), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["aliexpress_discovery_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "product_id", name="uq_discovery_snapshot_run_product"),
        sa.CheckConstraint("length(query_fingerprint) = 64", name="ck_discovery_snapshot_query_fp"),
        sa.CheckConstraint(
            "length(tracking_fingerprint) = 64", name="ck_discovery_snapshot_tracking_fp"
        ),
        sa.CheckConstraint("price > 0", name="ck_discovery_snapshot_positive_price"),
        sa.CheckConstraint("currency = 'BRL'", name="ck_discovery_snapshot_brl"),
        sa.CheckConstraint(
            "source_operation = 'aliexpress.affiliate.product.query'",
            name="ck_discovery_snapshot_source",
        ),
    )
    op.create_index(
        "ix_discovery_snapshot_product_time",
        "aliexpress_discovery_price_snapshots",
        ["product_id", "observed_at"],
    )
    op.create_table(
        "aliexpress_discovery_run_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(160), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("snapshot_id", sa.Integer()),
        sa.Column("title", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("target_brl_price", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("observed_prices", sa.JSON(), nullable=False),
        sa.Column("declared_discount_percent", sa.Numeric(8, 4)),
        sa.Column("commission_rate", sa.Numeric(8, 4)),
        sa.Column("volume", sa.Integer()),
        sa.Column("history_median", sa.Numeric(18, 2)),
        sa.Column("history_snapshot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_drop_percent", sa.Numeric(8, 4)),
        sa.Column("minimum_drop_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("history_score", sa.Integer(), nullable=False),
        sa.Column("discount_score", sa.Integer(), nullable=False),
        sa.Column("volume_score", sa.Integer(), nullable=False),
        sa.Column("commission_score", sa.Integer(), nullable=False),
        sa.Column("completeness_score", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("matched_query_count", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["run_id"], ["aliexpress_discovery_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["aliexpress_discovery_price_snapshots.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("run_id", "product_id", name="uq_discovery_result_run_product"),
        sa.CheckConstraint("origin IN ('LIVE','CACHE')", name="ck_discovery_result_origin"),
        sa.CheckConstraint(
            "(target_brl_price IS NULL AND currency IS NULL) OR "
            "(target_brl_price > 0 AND currency = 'BRL')",
            name="ck_discovery_result_price_currency",
        ),
        sa.CheckConstraint("history_score BETWEEN 0 AND 60", name="ck_discovery_history_score"),
        sa.CheckConstraint("discount_score BETWEEN 0 AND 15", name="ck_discovery_discount_score"),
        sa.CheckConstraint("volume_score BETWEEN 0 AND 10", name="ck_discovery_volume_score"),
        sa.CheckConstraint(
            "commission_score BETWEEN 0 AND 5", name="ck_discovery_commission_score"
        ),
        sa.CheckConstraint(
            "completeness_score BETWEEN 0 AND 10", name="ck_discovery_result_completeness"
        ),
        sa.CheckConstraint(
            "total_score = history_score + discount_score + volume_score + commission_score "
            "+ completeness_score AND total_score BETWEEN 0 AND 100",
            name="ck_discovery_total_score",
        ),
        sa.CheckConstraint(
            "classification IN ('BASELINE_ONLY','PROVIDER_DISCOUNT_ONLY',"
            "'HISTORY_BACKED_PRICE_DROP','INSUFFICIENT_DATA')",
            name="ck_discovery_classification",
        ),
        sa.CheckConstraint(
            "classification != 'HISTORY_BACKED_PRICE_DROP' OR "
            "(target_brl_price IS NOT NULL AND currency = 'BRL' AND history_snapshot_count >= 2 "
            "AND history_median IS NOT NULL AND price_drop_percent >= minimum_drop_percent)",
            name="ck_discovery_history_classification",
        ),
        sa.CheckConstraint(
            "classification != 'INSUFFICIENT_DATA' OR target_brl_price IS NULL",
            name="ck_discovery_insufficient_without_price",
        ),
        sa.CheckConstraint("matched_query_count >= 1", name="ck_discovery_matched_queries"),
    )
    op.create_index(
        "ix_discovery_results_run_score",
        "aliexpress_discovery_run_results",
        ["run_id", "total_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_results_run_score", table_name="aliexpress_discovery_run_results")
    op.drop_table("aliexpress_discovery_run_results")
    op.drop_index(
        "ix_discovery_snapshot_product_time",
        table_name="aliexpress_discovery_price_snapshots",
    )
    op.drop_table("aliexpress_discovery_price_snapshots")
    op.drop_index("ix_discovery_cache_product_id", table_name="aliexpress_discovery_cache_products")
    op.drop_table("aliexpress_discovery_cache_products")
    op.drop_index("ix_discovery_cache_expiry", table_name="aliexpress_discovery_query_cache")
    op.drop_table("aliexpress_discovery_query_cache")
    op.drop_index("ix_discovery_claims_lease", table_name="aliexpress_discovery_query_claims")
    op.drop_table("aliexpress_discovery_query_claims")
    op.drop_index("ix_discovery_runs_state_lease", table_name="aliexpress_discovery_runs")
    op.drop_table("aliexpress_discovery_runs")
