"""Record the operation of each product-level discovery observation.

Revision ID: 7e2b9c4d5a10
Revises: 6d1f4a8c2e90
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7e2b9c4d5a10"
down_revision = "6d1f4a8c2e90"
branch_labels = None
depends_on = None

QUERY = "aliexpress.affiliate.product.query"
HOT = "aliexpress.affiliate.hotproduct.query"
ALLOWED = f"source_operation IN ('{QUERY}','{HOT}')"


def upgrade() -> None:
    op.add_column(
        "aliexpress_discovery_runs",
        sa.Column("source_operation", sa.String(120), nullable=True),
    )
    op.execute(
        f"UPDATE aliexpress_discovery_runs SET source_operation = '{QUERY}' "
        "WHERE source_operation IS NULL"
    )
    with op.batch_alter_table("aliexpress_discovery_runs") as batch:
        batch.alter_column("source_operation", existing_type=sa.String(120), nullable=False)
        batch.create_check_constraint("ck_discovery_run_source", ALLOWED)
    op.add_column(
        "aliexpress_discovery_query_cache",
        sa.Column("source_operation", sa.String(120), nullable=True),
    )
    op.execute(
        f"UPDATE aliexpress_discovery_query_cache SET source_operation = '{QUERY}' "
        "WHERE source_operation IS NULL"
    )
    with op.batch_alter_table("aliexpress_discovery_query_cache") as batch:
        batch.alter_column("source_operation", existing_type=sa.String(120), nullable=False)
        batch.create_check_constraint("ck_discovery_cache_source", ALLOWED)
    with op.batch_alter_table("aliexpress_discovery_price_snapshots") as batch:
        batch.drop_constraint("ck_discovery_snapshot_source", type_="check")
        batch.create_check_constraint("ck_discovery_snapshot_source", ALLOWED)

    # SQLite CHECK constraints cannot compare two tables. Validate the persisted
    # source at the boundary even when a caller bypasses the application layer.
    op.execute(
        "CREATE TRIGGER discovery_snapshot_source_insert "
        "BEFORE INSERT ON aliexpress_discovery_price_snapshots "
        "FOR EACH ROW WHEN NOT EXISTS ("
        "SELECT 1 FROM aliexpress_discovery_runs r "
        "WHERE r.id = NEW.run_id AND r.source_operation = NEW.source_operation) "
        "BEGIN SELECT RAISE(ABORT, 'DISCOVERY_SNAPSHOT_SOURCE_MISMATCH'); END"
    )
    op.execute(
        "CREATE TRIGGER discovery_snapshot_source_update "
        "BEFORE UPDATE OF run_id, source_operation ON aliexpress_discovery_price_snapshots "
        "FOR EACH ROW WHEN NOT EXISTS ("
        "SELECT 1 FROM aliexpress_discovery_runs r "
        "WHERE r.id = NEW.run_id AND r.source_operation = NEW.source_operation) "
        "BEGIN SELECT RAISE(ABORT, 'DISCOVERY_SNAPSHOT_SOURCE_MISMATCH'); END"
    )
    op.execute(
        "CREATE TRIGGER discovery_run_source_update "
        "BEFORE UPDATE OF source_operation ON aliexpress_discovery_runs "
        "FOR EACH ROW WHEN EXISTS ("
        "SELECT 1 FROM aliexpress_discovery_price_snapshots s "
        "WHERE s.run_id = OLD.id AND s.source_operation != NEW.source_operation) "
        "BEGIN SELECT RAISE(ABORT, 'DISCOVERY_SNAPSHOT_SOURCE_MISMATCH'); END"
    )


def downgrade() -> None:
    hot_runs = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM aliexpress_discovery_runs WHERE source_operation = :operation"
        ),
        {"operation": HOT},
    )
    hot_cache = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM aliexpress_discovery_query_cache "
            "WHERE source_operation = :operation"
        ),
        {"operation": HOT},
    )
    if hot_runs or hot_cache:
        raise RuntimeError("DISCOVERY_HOT_DATA_REQUIRE_EXPLICIT_RETENTION_DECISION")
    op.execute("DROP TRIGGER discovery_run_source_update")
    op.execute("DROP TRIGGER discovery_snapshot_source_update")
    op.execute("DROP TRIGGER discovery_snapshot_source_insert")
    with op.batch_alter_table("aliexpress_discovery_price_snapshots") as batch:
        batch.drop_constraint("ck_discovery_snapshot_source", type_="check")
        batch.create_check_constraint(
            "ck_discovery_snapshot_source", f"source_operation = '{QUERY}'"
        )
    with op.batch_alter_table("aliexpress_discovery_query_cache") as batch:
        batch.drop_constraint("ck_discovery_cache_source", type_="check")
        batch.drop_column("source_operation")
    with op.batch_alter_table("aliexpress_discovery_runs") as batch:
        batch.drop_constraint("ck_discovery_run_source", type_="check")
        batch.drop_column("source_operation")
