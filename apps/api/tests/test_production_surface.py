"""What a public production URL exposes, and what a failing database may leak.

Findings of the independent audit of the Render/Neon preparation: the machine-readable API contract
stayed public next to the disabled interactive docs, and SQLAlchemy's default error text carries the
bound parameters (invitee e-mail addresses, Clerk subjects) into server logs.
"""

from __future__ import annotations

import io
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from promo_api.admin import entrypoint
from promo_api.app import create_app
from promo_api.db.engine import create_engine, create_operator_engine
from promo_api.settings import ApiSettings
from tests.conftest import (
    ISSUER,
    SYNTHETIC_CLERK_SECRET,
    UNREACHABLE_DATABASE,
    WEB_ORIGIN,
)


def _settings(environment: str) -> ApiSettings:
    return ApiSettings(
        environment=environment,  # type: ignore[arg-type]
        database_url=UNREACHABLE_DATABASE,  # type: ignore[arg-type]
        clerk_issuer=ISSUER,
        clerk_authorized_parties=(WEB_ORIGIN,),
        clerk_secret_key=SYNTHETIC_CLERK_SECRET,  # type: ignore[arg-type]
    )


async def _status(environment: str, path: str, key_source: Any) -> int:
    app = create_app(_settings(environment), key_source=key_source)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        return (await client.get(path)).status_code


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc"])
async def test_production_publishes_no_interactive_docs_nor_api_contract(
    path: str, key_source: Any
) -> None:
    assert await _status("production", path, key_source) == 404


async def test_development_keeps_the_docs_and_the_contract(key_source: Any) -> None:
    assert await _status("development", "/openapi.json", key_source) == 200
    assert await _status("development", "/docs", key_source) == 200


async def test_both_engines_keep_sql_parameters_out_of_error_text() -> None:
    api_engine = create_engine(_settings("production"))
    operator_engine = create_operator_engine(UNREACHABLE_DATABASE)
    try:
        assert api_engine.sync_engine.hide_parameters is True
        assert operator_engine.sync_engine.hide_parameters is True
    finally:
        await api_engine.dispose()
        await operator_engine.dispose()


# --- the admin CLI never prints a traceback ----------------------------------------------------

ADDRESS = "someone.private@example.test"
HOST = "ep-secret-host.us-east-2.aws.neon.tech"


def _failing_engine(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    async def refuse() -> Any:
        raise error

    def factory(_url: str, **_: Any) -> AsyncEngine:
        return create_async_engine(UNREACHABLE_DATABASE, async_creator=refuse)

    monkeypatch.setattr("promo_api.admin.create_operator_engine", factory)


@pytest.mark.parametrize("command", ["invite", "revoke", "list"])
def test_a_database_failure_prints_the_error_type_only(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PROMO_API_DATABASE_URL", UNREACHABLE_DATABASE)
    _failing_engine(monkeypatch, OSError(f"cannot reach {HOST} for {ADDRESS}"))

    code = entrypoint([command], stdin=io.StringIO(ADDRESS + "\n"))

    captured = capsys.readouterr()
    assert code == 1
    assert "OSError" in captured.err
    assert ADDRESS not in captured.out + captured.err
    assert HOST not in captured.out + captured.err
    assert "Traceback" not in captured.err
