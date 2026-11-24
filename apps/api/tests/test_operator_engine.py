"""Engines used by operators (Alembic migrations, `promo-api-admin`) against a managed database.

They share the URL contract of the API, hold no pool (one short-lived process), and migrations
add two guards: the direct endpoint only (a PgBouncer pooler in transaction mode is not suitable
for schema changes) and an explicit confirmation of any non-local target host, so that a stale
environment variable can never migrate the wrong database. Hosts use the reserved `.invalid`
TLD: nothing here can resolve or reach a network, even if a guard were missing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from promo_api.db import engine as engine_module
from promo_api.db.engine import confirm_target, create_engine, create_operator_engine
from promo_api.settings import ApiSettings
from tests.conftest import API_ROOT

DIRECT = (
    "postgresql+asyncpg://owner:synthetic-pw@ep-synthetic-1.region.invalid/neondb?sslmode=require"
)
POOLED = DIRECT.replace("ep-synthetic-1.", "ep-synthetic-1-pooler.")
LOCAL = "postgresql+asyncpg://postgres:synthetic-pw@127.0.0.1:5432/promo_api_test_x"


async def test_an_operator_engine_holds_no_pool_and_drops_libpq_parameters() -> None:
    engine = create_operator_engine(DIRECT)
    try:
        assert isinstance(engine.sync_engine.pool, NullPool)
        assert dict(engine.url.query) == {}
        assert engine.url.host == "ep-synthetic-1.region.invalid"
    finally:
        await engine.dispose()


async def test_a_pooled_endpoint_is_allowed_for_ordinary_operator_commands() -> None:
    engine = create_operator_engine(POOLED)
    await engine.dispose()


def _connect_args_given_to_sqlalchemy(
    monkeypatch: pytest.MonkeyPatch, build: Callable[[], AsyncEngine]
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    real = engine_module.create_async_engine

    def spy(*args: Any, **kwargs: Any) -> AsyncEngine:
        captured.update(kwargs["connect_args"])
        return real(*args, **kwargs)

    monkeypatch.setattr(engine_module, "create_async_engine", spy)
    build()
    return captured


async def test_a_migration_is_not_cut_short_by_the_api_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A schema change (an index build, say) may legitimately run longer than any API query.
    args = _connect_args_given_to_sqlalchemy(monkeypatch, lambda: create_operator_engine(DIRECT))

    assert "command_timeout" not in args
    assert args["timeout"] == 15.0  # waking a suspended database is still bounded


async def test_the_api_engine_does_bound_how_long_a_query_may_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ApiSettings(  # type: ignore[call-arg]
        database_url=DIRECT,  # type: ignore[arg-type]
        clerk_issuer="https://synthetic.invalid",
        clerk_authorized_parties=("http://localhost:3000",),
        clerk_secret_key="sk_test_synthetic",  # type: ignore[arg-type]
    )

    args = _connect_args_given_to_sqlalchemy(monkeypatch, lambda: create_engine(settings))

    assert args["command_timeout"] == 30.0


def test_schema_migrations_refuse_the_pooled_endpoint() -> None:
    with pytest.raises(ValueError, match="direct"):
        create_operator_engine(POOLED, require_direct=True)


def test_the_refusal_does_not_echo_the_target() -> None:
    with pytest.raises(ValueError) as error:
        create_operator_engine(POOLED, require_direct=True)

    assert "region.invalid" not in str(error.value)
    assert "synthetic-pw" not in str(error.value)


def test_a_remote_target_needs_its_host_confirmed() -> None:
    with pytest.raises(ValueError, match="confirm"):
        confirm_target(DIRECT, None)


def test_a_wrong_confirmation_is_refused() -> None:
    with pytest.raises(ValueError, match="confirm"):
        confirm_target(DIRECT, "some-other-host.region.invalid")


def test_the_right_confirmation_is_accepted_ignoring_case() -> None:
    confirm_target(DIRECT, "EP-synthetic-1.REGION.invalid")


def test_a_local_target_needs_no_confirmation() -> None:
    confirm_target(LOCAL, None)


def _alembic(url: str, *extra: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.cmd_opts = type("Options", (), {"x": [f"database_url={url}", *extra]})()  # type: ignore[assignment]
    return config


def test_alembic_refuses_a_pooled_endpoint_before_any_connection() -> None:
    with pytest.raises(ValueError, match="direct"):
        command.upgrade(_alembic(POOLED, "confirm=ep-synthetic-1-pooler.region.invalid"), "head")


def test_alembic_refuses_an_unconfirmed_remote_target_before_any_connection() -> None:
    with pytest.raises(ValueError, match="confirm"):
        command.upgrade(_alembic(DIRECT), "head")
