"""Resilience against a real (ephemeral) PostgreSQL: what a Neon compute that suspends looks like
from the API, and what a saturated pool looks like to a caller."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from promo_api.app import create_app
from promo_api.db.engine import create_engine
from tests.conftest import StaticKeys, TokenFactory, settings_for

pytestmark = pytest.mark.postgres

# Only the server backends of the engine under test: the suite shares other engines (fixtures)
# on the same database, and their connections must survive.
_DROP_BACKENDS = text("SELECT pg_terminate_backend(pid) FROM unnest(CAST(:pids AS int[])) AS pid")


async def test_the_pool_recovers_after_the_server_drops_its_connections(
    migrated: str, key_source: StaticKeys
) -> None:
    # A suspended Neon compute closes every server-side connection: the pool then holds dead ones.
    settings = settings_for(migrated)
    engine = create_engine(settings)
    admin = create_async_engine(migrated)
    app = create_app(settings, engine=engine, key_source=key_source)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test"
        ) as client:
            assert (await client.get("/readyz")).status_code == 200  # a connection is now pooled

            async with engine.connect() as pooled:  # the idle pooled connection, checked out
                pid = await pooled.scalar(text("SELECT pg_backend_pid()"))
            async with admin.begin() as connection:
                dropped = (await connection.execute(_DROP_BACKENDS, {"pids": [pid]})).all()
            assert dropped == [(True,)], "the test must actually have dropped the pooled connection"

            assert (await client.get("/readyz")).status_code == 200  # pre-ping replaced it
    finally:
        await admin.dispose()
        await engine.dispose()


async def test_a_saturated_pool_gives_a_retryable_503_instead_of_hanging(
    migrated: str, key_source: StaticKeys, make_token: TokenFactory
) -> None:
    settings = settings_for(migrated).model_copy(
        update={"db_pool_size": 1, "db_max_overflow": 0, "db_pool_timeout_seconds": 1.0}
    )
    engine = create_engine(settings)
    app = create_app(settings, engine=engine, key_source=key_source)
    try:
        async with (
            engine.connect(),  # takes the only connection of the pool
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://api.test"
            ) as client,
        ):
            response = await client.get(
                "/v1/me", headers={"Authorization": f"Bearer {make_token()}"}
            )
    finally:
        await engine.dispose()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert response.headers["Retry-After"] == "5"
