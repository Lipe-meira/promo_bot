"""Managed-PostgreSQL behaviour that does not need a database: startup validation of the URL, a pool
sized for a free instance, and an outage that is reported as a retryable 503 (not a crash).

A free Neon compute suspends after five idle minutes and takes a moment to wake; a free Render
instance holds very little memory. Both argue for a small pool, bounded waits and a clear signal
to the caller when the database is not reachable yet.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.pool import QueuePool

from promo_api.db.engine import create_engine
from promo_api.settings import ApiSettings
from tests.conftest import (
    ISSUER,
    SYNTHETIC_CLERK_SECRET,
    UNREACHABLE_DATABASE,
    WEB_ORIGIN,
    TokenFactory,
)

NEON_DIRECT = (
    "postgresql+asyncpg://neondb_owner:synthetic-pw@ep-synthetic-1234.us-east-2.aws.neon.tech"
    "/neondb?sslmode=require&channel_binding=require"
)


def settings(**overrides: Any) -> ApiSettings:
    values: dict[str, Any] = {
        "database_url": UNREACHABLE_DATABASE,
        "clerk_issuer": ISSUER,
        "clerk_authorized_parties": (WEB_ORIGIN,),
        "clerk_secret_key": SYNTHETIC_CLERK_SECRET,
    }
    values.update(overrides)
    return ApiSettings(**values)


def test_a_provider_connection_string_is_accepted_at_startup() -> None:
    assert settings(database_url=NEON_DIRECT).database_url.get_secret_value() == NEON_DIRECT


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://u:p@db.example.test/app?sslmode=disable",
        "postgresql+asyncpg://u:p@db.example.test/app?options=x",
        "postgresql+asyncpg://u:p@db.example.test/app?sslmode=bogus",
    ],
)
def test_an_unsafe_database_url_stops_the_process_before_serving(database_url: str) -> None:
    with pytest.raises(ValidationError):
        settings(database_url=database_url)


def test_a_rejected_database_url_is_not_echoed_in_the_startup_error() -> None:
    with pytest.raises(ValidationError) as error:
        settings(database_url="postgresql+asyncpg://u:hunter2@db.example.test/app?sslmode=disable")

    assert "hunter2" not in str(error.value)
    assert "db.example.test" not in str(error.value)


@pytest.mark.parametrize(
    "database_url",
    [
        # Short enough that pydantic would print the whole input (it only elides past ~50 chars).
        "postgresql+asyncpg://u:hunter2@h.io/a?x=1",
        "postgresql://u:hunter2@h.io/a",
    ],
)
def test_a_short_rejected_database_url_is_never_printed_whole(database_url: str) -> None:
    with pytest.raises(ValidationError) as error:
        settings(database_url=database_url)

    assert "hunter2" not in str(error.value)
    assert "input_value" not in str(error.value)


def test_pool_defaults_are_sized_for_a_free_instance() -> None:
    loaded = settings()

    assert (loaded.db_pool_size, loaded.db_max_overflow) == (3, 2)
    assert loaded.db_pool_timeout_seconds == 10
    assert loaded.db_connect_timeout_seconds == 15
    assert loaded.db_pool_recycle_seconds == 240


def test_pool_settings_come_from_prefixed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMO_API_DATABASE_URL", UNREACHABLE_DATABASE)
    monkeypatch.setenv("PROMO_API_CLERK_ISSUER", ISSUER)
    monkeypatch.setenv("PROMO_API_CLERK_AUTHORIZED_PARTIES", WEB_ORIGIN)
    monkeypatch.setenv("PROMO_API_CLERK_SECRET_KEY", SYNTHETIC_CLERK_SECRET)
    monkeypatch.setenv("PROMO_API_DB_POOL_SIZE", "2")
    monkeypatch.setenv("PROMO_API_DB_MAX_OVERFLOW", "1")

    loaded = ApiSettings()  # type: ignore[call-arg]

    assert (loaded.db_pool_size, loaded.db_max_overflow) == (2, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"db_pool_size": 0},
        {"db_pool_size": 11},
        {"db_max_overflow": -1},
        {"db_max_overflow": 11},
        {"db_pool_timeout_seconds": 0},
        {"db_connect_timeout_seconds": 0},
        {"db_connect_timeout_seconds": 61},
        {"db_pool_recycle_seconds": 10},
    ],
)
def test_pool_settings_are_bounded(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


async def test_the_engine_applies_the_prepared_url_and_the_pool_settings() -> None:
    engine = create_engine(settings(database_url=NEON_DIRECT))
    try:
        pool = engine.sync_engine.pool

        assert isinstance(pool, QueuePool)
        assert dict(engine.url.query) == {}  # libpq parameters never reach asyncpg
        assert pool.size() == 3
        assert pool._max_overflow == 2
        assert pool._timeout == 10
        assert pool._recycle == 240
        assert pool._pre_ping is True  # a connection that Neon dropped while suspended is replaced
    finally:
        await engine.dispose()


async def test_a_database_outage_is_a_retryable_503_not_a_crash(
    offline_client: httpx.AsyncClient, make_token: TokenFactory
) -> None:
    response = await offline_client.get(
        "/v1/me", headers={"Authorization": f"Bearer {make_token()}"}
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "The database is temporarily unavailable. Try again shortly.",
        }
    }
    assert response.headers["Retry-After"] == "5"
    assert "127.0.0.1" not in response.text  # nothing about the target leaks
    assert response.headers["Cache-Control"] == "no-store"


async def test_authentication_is_still_checked_before_the_database(
    offline_client: httpx.AsyncClient,
) -> None:
    response = await offline_client.get("/v1/me")

    assert response.status_code == 401
