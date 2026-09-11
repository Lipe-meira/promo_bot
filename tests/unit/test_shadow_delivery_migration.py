import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from promo_bot.database.migrations import project_root, upgrade_database


def test_shadow_delivery_migration_constraints_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "shadow.sqlite3"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    upgrade_database(url)
    config = Config(str(project_root() / "alembic.ini"))
    config.set_main_option("script_location", str(project_root() / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (heads[0],)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(affiliate_shadow_deliveries)")}
        assert {
            "preview_id",
            "destination_key",
            "state",
            "attempt_count",
            "started_at",
            "finished_at",
            "telegram_message_id",
            "error_code",
            "created_at",
            "updated_at",
        } <= columns
        insert = (
            "INSERT INTO affiliate_shadow_deliveries "
            "(preview_id,destination_key,state,attempt_count,created_at,updated_at) "
            "VALUES (1,? ,?,?,'2026-09-10','2026-09-10')"
        )
        conn.execute(insert, ("fixture", "pending", 0))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("fixture", "pending", 0))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("other", "sending", 2))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("other", "retryable", 0))
    command.downgrade(config, "e4c19a7b52d0")
    with sqlite3.connect(path) as conn:
        assert not conn.execute("PRAGMA table_info(affiliate_shadow_deliveries)").fetchall()
    command.upgrade(config, "head")
    command.check(config)
