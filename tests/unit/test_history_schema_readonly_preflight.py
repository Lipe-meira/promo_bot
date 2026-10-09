"""Schema refusal must not checkpoint committed WAL frames into the database."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from alembic import command

from promo_bot.cli import main
from tests.unit.test_affiliate_history_schema import config_for
from tests.unit.test_coin_listener_pilot import _argv, _config_file, _settings


@pytest.fixture(autouse=True)
def isolate_database_override(monkeypatch):
    # Alembic's env.py must never redirect a synthetic fixture to an operator DB.
    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)


def physical_files(path: Path) -> tuple[bytes | None, ...]:
    # No SQLite connection: inspect before fixture cleanup can checkpoint anything.
    return tuple(
        file.read_bytes() if file.is_file() else None for file in (path, Path(str(path) + "-wal"))
    )


def leave_committed_wal(path: Path, *, migrate: bool = False) -> None:
    # An ordinary connection close would checkpoint and erase the reproduction.
    # This owned child exits after commit, without closing the SQLite connection.
    script = """
import os
import sqlite3
import sys
c = sqlite3.connect(sys.argv[1])
assert c.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
c.execute('PRAGMA wal_autocheckpoint=0')
c.execute('CREATE TABLE offline_wal_marker (id INTEGER PRIMARY KEY)')
c.execute('INSERT INTO offline_wal_marker VALUES (1)')
c.commit()
if sys.argv[2] == 'migrate':
    from alembic import command
    from tests.unit.test_affiliate_history_schema import config_for
    from pathlib import Path
    command.upgrade(config_for(Path(sys.argv[1])), 'head')
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(path), "migrate" if migrate else ""], check=True
    )
    assert Path(str(path) + "-wal").stat().st_size > 32
    assert Path(str(path) + "-shm").is_file()


def allow_test_storage(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    # Only the temporary-path policy is bypassed, never the schema admission logic.
    # Import before patching: the legacy CLI binds the validator at module import.
    from promo_bot.affiliate import history_cli

    monkeypatch.setattr(history_cli, "durable_sqlite_path", lambda _url: path.resolve())
    monkeypatch.setattr(
        "promo_bot.database.history_storage.durable_sqlite_path", lambda _url: path.resolve()
    )
    monkeypatch.setattr(
        "promo_bot.affiliate.history_context.durable_sqlite_path", lambda _url: path.resolve()
    )


@pytest.mark.parametrize("opt_in", [False, True], ids=["singleton", "multi"])
@pytest.mark.parametrize("wal", ["none", "clean-wal", "committed-wal"])
def test_old_schema_refusal_preserves_main_and_committed_wal_before_cleanup(
    tmp_path, monkeypatch, capsys, opt_in, wal
):
    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
    path = tmp_path / "previous.sqlite3"
    command.upgrade(config_for(path), "9b3d5e7f1a20")
    if wal == "clean-wal":
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()
    elif wal == "committed-wal":
        leave_committed_wal(path)
        assert b"offline_wal_marker" not in path.read_bytes()
        assert b"offline_wal_marker" in Path(str(path) + "-wal").read_bytes()
    before = physical_files(path)
    allow_test_storage(monkeypatch, path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=True))
    transports = []

    def forbidden(*_args, **_kwargs):
        transports.append(True)
        pytest.fail("transport constructed for rejected schema")

    monkeypatch.setattr("promo_bot.cli.build_telegram_user_client", forbidden)
    monkeypatch.setattr("promo_bot.cli.build_offline_safe_http_client", forbidden)
    argv = _argv(_config_file(tmp_path), path)
    if opt_in:
        argv.append("--allow-multiple-coin-shorts")
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert "AFFILIATE_HISTORY_SCHEMA_REQUIRED" in captured.err
    assert str(path) not in captured.err
    assert transports == []
    after = physical_files(path)
    assert after == before, (
        f"main_identical={after[0] == before[0]}, wal_identical={after[1] == before[1]}"
    )


@pytest.mark.parametrize("kind", ["missing", "empty", "no-schema", "invalid", "corrupt"])
async def test_unverifiable_schema_refuses_before_writable_connect_without_creating_database(
    tmp_path, monkeypatch, kind
):
    from sqlalchemy import event

    from promo_bot.affiliate.history_context import validate_history_storage
    from promo_bot.database.session import create_affiliate_shadow_database

    path = tmp_path / "unverifiable.sqlite3"
    if kind == "empty":
        path.touch()
    elif kind == "corrupt":
        path.write_bytes(b"not a sqlite database")
    elif kind in {"no-schema", "invalid"}:
        with closing(sqlite3.connect(path)) as conn:
            if kind == "invalid":
                conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
                conn.execute("INSERT INTO alembic_version VALUES ('not-a-revision')")
                conn.commit()
            else:
                conn.execute("CREATE TABLE unrelated (id INTEGER)")
                conn.commit()
    before = physical_files(path)
    allow_test_storage(monkeypatch, path)
    database = create_affiliate_shadow_database(path)
    connected = []
    event.listen(database.engine.sync_engine, "connect", lambda *_a: connected.append(True))
    try:
        code = {
            "missing": "AFFILIATE_HISTORY_DATABASE_REQUIRED",
            "corrupt": "AFFILIATE_HISTORY_STORAGE_UNAVAILABLE",
        }.get(kind, "AFFILIATE_HISTORY_SCHEMA_REQUIRED")
        with pytest.raises(ValueError, match=code):
            await validate_history_storage(database, real=True)
        assert connected == []
        assert physical_files(path) == before
    finally:
        await database.dispose()
    assert physical_files(path) == before


def test_clean_current_wal_without_sidecars_checks_private_copy_without_opening_original(
    tmp_path, monkeypatch
):
    from promo_bot.database import history_storage

    path = tmp_path / "current-clean-wal.sqlite3"
    command.upgrade(config_for(path), "head")
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    before = physical_files(path)
    native = sqlite3.connect
    copies = []

    def private_only(database_uri, **kwargs):
        uri_path = unquote(urlparse(database_uri).path)
        copied = Path(uri_path.lstrip("/") if os.name == "nt" else uri_path)
        assert copied.resolve() != path.resolve()
        assert database_uri.endswith("?mode=ro&immutable=1")
        copies.append(copied)
        return native(database_uri, **kwargs)

    monkeypatch.setattr(history_storage.sqlite3, "connect", private_only)
    history_storage.validate_history_schema_readonly(path)
    assert physical_files(path) == before
    assert not Path(str(path) + "-shm").exists()
    assert len(copies) == 1 and not copies[0].exists()


@pytest.mark.parametrize("wal", [False, True], ids=["main-revision", "wal-revision"])
async def test_current_revision_including_wal_is_read_before_full_writable_validation(
    tmp_path, monkeypatch, wal
):
    import asyncio

    from sqlalchemy import text

    from promo_bot.cli import _open_durable_shadow_database
    from promo_bot.database.history_storage import validate_history_schema_readonly

    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
    path = tmp_path / "current.sqlite3"
    await asyncio.to_thread(command.upgrade, config_for(path), "9b3d5e7f1a20" if wal else "head")
    if wal:
        # Real additive migration + revision committed only in WAL, not the main file.
        leave_committed_wal(path, migrate=True)
        assert b"9b3d5e7f1a20" in path.read_bytes()
        assert b"b8c2e4f6a901" not in path.read_bytes()
        assert b"aliexpress_coin_shadow_multi_previews" not in path.read_bytes()
    before = physical_files(path)
    validate_history_schema_readonly(path)
    assert physical_files(path) == before
    if wal:
        assert Path(str(path) + "-shm").is_file()
    allow_test_storage(monkeypatch, path)
    database = await _open_durable_shadow_database(path)
    try:
        async with database.session() as session:
            assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "b8c2e4f6a901"
            )
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            assert await session.scalar(text("PRAGMA synchronous")) == 2
            assert await session.scalar(text("PRAGMA quick_check")) == "ok"
            assert (await session.execute(text("PRAGMA foreign_key_check"))).first() is None
    finally:
        await database.dispose()


async def test_missing_shm_with_committed_wal_fails_closed_without_recreating_it(
    tmp_path, monkeypatch
):
    import asyncio

    from promo_bot.cli import _open_durable_shadow_database

    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
    path = tmp_path / "no-shm.sqlite3"
    await asyncio.to_thread(command.upgrade, config_for(path), "head")
    leave_committed_wal(path)
    shm = Path(str(path) + "-shm")
    shm.unlink()  # Owned, closed fixture only; never done by the production preflight.
    before = physical_files(path)
    allow_test_storage(monkeypatch, path)
    with pytest.raises(ValueError, match="AFFILIATE_HISTORY_WAL_UNVERIFIABLE"):
        await _open_durable_shadow_database(path)
    assert physical_files(path) == before
    assert not shm.exists()


async def test_explicit_temporary_migration_allows_a_previously_refused_database(
    tmp_path, monkeypatch
):
    import asyncio

    from promo_bot.cli import _open_durable_shadow_database

    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
    path = tmp_path / "migrated.sqlite3"
    await asyncio.to_thread(command.upgrade, config_for(path), "9b3d5e7f1a20")
    allow_test_storage(monkeypatch, path)
    with pytest.raises(ValueError, match="AFFILIATE_HISTORY_SCHEMA_REQUIRED"):
        await _open_durable_shadow_database(path)
    await asyncio.to_thread(command.upgrade, config_for(path), "head")
    database = await _open_durable_shadow_database(path)
    await database.dispose()


def test_readonly_preflight_can_rebuild_existing_shm_without_changing_database_or_wal(tmp_path):
    from promo_bot.database.history_storage import validate_history_schema_readonly

    path = tmp_path / "rebuild-shm.sqlite3"
    command.upgrade(config_for(path), "9b3d5e7f1a20")
    leave_committed_wal(path)
    shm = Path(str(path) + "-shm")
    # Simulate stale coordination state, only after the fixture's writer has exited.
    shm.write_bytes(bytes(shm.stat().st_size))
    before = physical_files(path)
    shm_before = shm.read_bytes()
    with pytest.raises(ValueError, match="AFFILIATE_HISTORY_SCHEMA_REQUIRED"):
        validate_history_schema_readonly(path)
    assert physical_files(path) == before
    assert shm.read_bytes() != shm_before


def test_unreadable_sqlite_view_fails_sanitized_without_writable_fallback(tmp_path, monkeypatch):
    from promo_bot.database import history_storage

    path = tmp_path / "locked.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.commit()
    before = physical_files(path)
    calls = []

    def unavailable(database_uri, **kwargs):
        calls.append((database_uri, kwargs))
        raise sqlite3.OperationalError("synthetic private error text")

    monkeypatch.setattr(history_storage.sqlite3, "connect", unavailable)
    with pytest.raises(ValueError, match=r"^AFFILIATE_HISTORY_STORAGE_UNAVAILABLE$"):
        history_storage.validate_history_schema_readonly(path)
    assert len(calls) == 1 and calls[0][0].endswith("?mode=ro")
    assert calls[0][1]["uri"] is True
    assert physical_files(path) == before


async def test_canonical_preview_also_refuses_old_schema_before_transport_and_checkpoint(
    tmp_path, monkeypatch
):
    import asyncio

    from promo_bot.cli import run_aliexpress_conversion_preview

    monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
    path = tmp_path / "canonical-old.sqlite3"
    await asyncio.to_thread(command.upgrade, config_for(path), "9b3d5e7f1a20")
    leave_committed_wal(path)
    before = physical_files(path)
    allow_test_storage(monkeypatch, path)
    monkeypatch.setattr(
        "promo_bot.cli.build_offline_safe_http_client",
        lambda: pytest.fail("HTTP built before read-only schema admission"),
    )
    with pytest.raises(ValueError, match="AFFILIATE_HISTORY_SCHEMA_REQUIRED"):
        await run_aliexpress_conversion_preview(
            _settings(coin_gate=True), 1, database_path=path, scope="shadow"
        )
    assert physical_files(path) == before


@pytest.mark.parametrize("corrupt", [False, True], ids=["old-schema", "unreadable"])
def test_legacy_request_retains_specific_sanitized_storage_refusal(
    tmp_path, monkeypatch, capsys, corrupt
):
    path = tmp_path / "legacy-command.sqlite3"
    if corrupt:
        path.write_bytes(b"not sqlite")
    else:
        command.upgrade(config_for(path), "9b3d5e7f1a20")
        leave_committed_wal(path)
    before = physical_files(path)
    allow_test_storage(monkeypatch, path)
    monkeypatch.setattr(
        "promo_bot.cli.load_settings", lambda: pytest.fail("history command loaded settings")
    )
    assert (
        main(
            [
                "affiliate",
                "link-history",
                "request-legacy-generation",
                "--database",
                str(path),
                "--scope",
                "shadow",
                "--legacy-kind",
                "coin-evidence",
                "--legacy-id",
                "1",
                "--confirm-new-generation",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "status": "failed_safe",
        "error_code": (
            "AFFILIATE_HISTORY_STORAGE_UNAVAILABLE"
            if corrupt
            else "AFFILIATE_HISTORY_SCHEMA_REQUIRED"
        ),
    }
    assert physical_files(path) == before
