"""`/readyz` against an ephemeral PostgreSQL (W-02).

Ready means: the database answers *and* `alembic_version` holds exactly the head(s) of the
versioned migrations shipped with the application. Nothing here runs a migration on behalf of the
endpoint; tests that move the schema do it with the real Alembic commands and restore it.

Run with `scripts/test-postgres.ps1` (these tests are `postgres`-marked).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from alembic import command
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from promo_api.app import create_app
from promo_api.readiness import CachedReadiness
from tests.conftest import StaticKeys, alembic_config, settings_for

pytestmark = pytest.mark.postgres

READY = (200, {"status": "ready", "database": "ok"})
CREATE_VERSION_TABLE = (
    "CREATE TABLE IF NOT EXISTS alembic_version ("
    "version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
)


async def probe(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        return await http.get("/readyz")


def assert_not_ready(response: httpx.Response) -> None:
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "NOT_READY", "message": "The database is not ready."}
    }


@pytest_asyncio.fixture
async def version_table(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Hands the engine to a test that tampers with `alembic_version`; always restores it."""

    async with engine.connect() as connection:
        saved = list(await connection.scalars(text("SELECT version_num FROM alembic_version")))
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(CREATE_VERSION_TABLE))
            await connection.execute(text("DELETE FROM alembic_version"))
            for revision in saved:
                await connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revision}
                )


async def set_versions(engine: AsyncEngine, *revisions: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM alembic_version"))
        for revision in revisions:
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revision}
            )


def app_for(engine: AsyncEngine, key_source: StaticKeys, **kwargs: object) -> FastAPI:
    url = engine.url.render_as_string(hide_password=False)
    # These tests are about *what* readiness reports as the schema changes step by step, so they
    # disable the cache that the real app uses (see test_readiness_cache.py).
    kwargs.setdefault("readiness", CachedReadiness(success_ttl=0, failure_ttl=0))
    return create_app(settings_for(url), engine=engine, key_source=key_source, **kwargs)  # type: ignore[arg-type]


# --- the real migrations -----------------------------------------------------------------------


async def test_ready_when_the_database_is_at_the_current_head(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/readyz")

    assert (response.status_code, response.json()) == READY


async def test_not_ready_when_the_version_table_is_empty(
    version_table: AsyncEngine, key_source: StaticKeys
) -> None:
    await set_versions(version_table)

    assert_not_ready(await probe(app_for(version_table, key_source)))


async def test_not_ready_when_the_recorded_revision_is_unknown(
    version_table: AsyncEngine, key_source: StaticKeys
) -> None:
    await set_versions(version_table, "ffff_not_a_revision_of_this_app")

    assert_not_ready(await probe(app_for(version_table, key_source)))


async def test_not_ready_when_the_current_head_is_recorded_next_to_a_stale_row(
    version_table: AsyncEngine, key_source: StaticKeys
) -> None:
    await set_versions(version_table, "0003", "0000_stale")

    assert_not_ready(await probe(app_for(version_table, key_source)))


async def test_not_ready_when_the_version_table_is_missing(
    version_table: AsyncEngine, key_source: StaticKeys
) -> None:
    async with version_table.begin() as connection:
        await connection.execute(text("DROP TABLE alembic_version"))

    assert_not_ready(await probe(app_for(version_table, key_source)))


async def test_not_ready_when_the_database_is_unavailable(
    engine: AsyncEngine, key_source: StaticKeys
) -> None:
    missing = make_url(engine.url.render_as_string(hide_password=False)).set(
        database="promo_api_test_does_not_exist"
    )
    unavailable = create_async_engine(missing)
    try:
        response = await probe(app_for(unavailable, key_source))
    finally:
        await unavailable.dispose()

    assert_not_ready(response)
    assert "promo_api_test_does_not_exist" not in response.text
    assert "127.0.0.1" not in response.text


async def test_readiness_follows_the_real_schema_through_downgrade_and_upgrade(
    engine: AsyncEngine, key_source: StaticKeys, migrated: str
) -> None:
    app = app_for(engine, key_source)
    try:
        # Alembic's env.py drives its own event loop, so the commands run off this one.
        await asyncio.to_thread(command.downgrade, alembic_config(migrated), "base")
        assert_not_ready(await probe(app))  # the table survives a downgrade, but is empty

        await asyncio.to_thread(command.upgrade, alembic_config(migrated), "head")
        assert (await probe(app)).status_code == 200
    finally:
        await asyncio.to_thread(command.upgrade, alembic_config(migrated), "head")


# --- the head comes from the versioned scripts, whatever they are ------------------------------


def scripts(root: Path, *chain: tuple[str, str | None]) -> Path:
    """A throwaway migrations directory: each item is (revision, down_revision)."""

    versions = root / "versions"
    versions.mkdir(parents=True)
    for revision, parent in chain:
        (versions / f"{revision}.py").write_text(
            f'revision = "{revision}"\ndown_revision = {parent!r}\n'
            "branch_labels = None\ndepends_on = None\n"
            "def upgrade(): pass\ndef downgrade(): pass\n",
            encoding="utf-8",
        )
    return root


async def test_a_database_behind_the_shipped_head_is_not_ready_and_catches_up_when_upgraded(
    version_table: AsyncEngine, key_source: StaticKeys, tmp_path: Path
) -> None:
    shipped = scripts(tmp_path, ("r1", None), ("r2", "r1"))
    app = app_for(version_table, key_source, migrations_dir=shipped)

    await set_versions(version_table, "r1")  # older revision of the same history
    assert_not_ready(await probe(app))

    await set_versions(version_table, "r2")  # what `alembic upgrade head` would record
    assert (await probe(app)).status_code == 200


async def test_a_database_ahead_of_the_shipped_head_is_not_ready(
    version_table: AsyncEngine, key_source: StaticKeys, tmp_path: Path
) -> None:
    shipped = scripts(tmp_path, ("r1", None))
    await set_versions(version_table, "r2")  # a newer deployment migrated it; this build is stale

    assert_not_ready(await probe(app_for(version_table, key_source, migrations_dir=shipped)))


async def test_with_several_heads_the_database_must_record_every_one_of_them(
    version_table: AsyncEngine, key_source: StaticKeys, tmp_path: Path
) -> None:
    shipped = scripts(tmp_path, ("base", None), ("left", "base"), ("right", "base"))
    app = app_for(version_table, key_source, migrations_dir=shipped)

    await set_versions(version_table, "left")
    assert_not_ready(await probe(app))
    await set_versions(version_table, "base")
    assert_not_ready(await probe(app))
    await set_versions(version_table, "left", "right")
    assert (await probe(app)).status_code == 200
    await set_versions(version_table, "left", "right", "base")
    assert_not_ready(await probe(app))


def test_an_application_without_versioned_migrations_refuses_to_start(
    key_source: StaticKeys, tmp_path: Path
) -> None:
    empty = scripts(tmp_path)
    with pytest.raises(RuntimeError, match="migrations"):
        create_app(
            settings_for("postgresql+asyncpg://u:p@127.0.0.1:1/promo_api_test_x"),
            key_source=key_source,
            migrations_dir=empty,
        )  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="migrations"):
        create_app(
            settings_for("postgresql+asyncpg://u:p@127.0.0.1:1/promo_api_test_x"),
            key_source=key_source,
            migrations_dir=tmp_path / "absent",
        )


async def test_the_endpoint_never_migrates_the_database(
    version_table: AsyncEngine, key_source: StaticKeys
) -> None:
    await set_versions(version_table)

    await probe(app_for(version_table, key_source))

    async with version_table.connect() as connection:
        rows = list(await connection.scalars(text("SELECT version_num FROM alembic_version")))
    assert rows == []
