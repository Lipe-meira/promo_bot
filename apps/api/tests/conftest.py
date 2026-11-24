"""Synthetic infrastructure for the API tests.

Identity: RSA keys generated in memory and a JWKS-shaped key source, so the *real* verifier and
authorization code run; there is no test-only authentication bypass.

Database: only an explicitly provided, clearly ephemeral PostgreSQL is accepted (see
`database_url`). The suite never reads `PROMO_API_DATABASE_URL`.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from promo_api.app import create_app
from promo_api.settings import ApiSettings

API_ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://synthetic-instance.clerk.example"
WEB_ORIGIN = "http://localhost:3000"
KID = "synthetic-key-1"
TEST_DATABASE_ENV = "PROMO_API_TEST_DATABASE_URL"
TABLES = "workspace_memberships, workspaces, invitations, users"


class StaticKeys:
    """A key source backed by a dict, standing in for the issuer's published JWKS."""

    def __init__(self, keys: dict[str, Any]) -> None:
        self.keys = keys

    async def get(self, kid: str) -> Any | None:
        return self.keys.get(kid)


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def other_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def key_source(signing_key: rsa.RSAPrivateKey) -> StaticKeys:
    return StaticKeys({KID: signing_key.public_key()})


TokenFactory = Callable[..., str]


@pytest.fixture(scope="session")
def make_token(signing_key: rsa.RSAPrivateKey) -> TokenFactory:
    def factory(
        subject: str = "user_synthetic_a",
        *,
        email: str | None = "a@example.test",
        email_verified: object = True,
        key: Any = None,
        algorithm: str = "RS256",
        kid: str | None = KID,
        drop: tuple[str, ...] = (),
        **claims: Any,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": subject,
            "iss": ISSUER,
            "azp": WEB_ORIGIN,
            "iat": now,
            "nbf": now - 1,
            "exp": now + 60,
            "sid": "sess_synthetic",
            "email_verified": email_verified,
        }
        if email is not None:
            payload["email"] = email
        payload.update(claims)
        for name in drop:
            payload.pop(name, None)
        headers = {"kid": kid} if kid is not None else {}
        return jwt.encode(payload, key or signing_key, algorithm=algorithm, headers=headers)

    return factory


def settings_for(database_url: str) -> ApiSettings:
    return ApiSettings(
        environment="test",
        database_url=database_url,  # type: ignore[arg-type]
        clerk_issuer=ISSUER,
        clerk_authorized_parties=(WEB_ORIGIN,),
    )


# --- no-database app: enough for authentication, validation and contract tests ---------------

UNREACHABLE_DATABASE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/promo_api_test_unreachable"


@pytest.fixture
def offline_app(key_source: StaticKeys) -> FastAPI:
    """The real app with a database that is never reached (engines connect lazily)."""

    return create_app(settings_for(UNREACHABLE_DATABASE), key_source=key_source)


@pytest_asyncio.fixture
async def offline_client(offline_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        yield client


# --- ephemeral PostgreSQL --------------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url() -> str:
    """Refuse anything that does not look like a throwaway local test database."""

    url = os.environ.get(TEST_DATABASE_ENV, "")
    if not url:
        pytest.fail(
            f"{TEST_DATABASE_ENV} is required for `-m postgres` (see scripts/test-postgres.ps1)"
        )
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    if (
        not url.startswith("postgresql+asyncpg://")
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not database.startswith("promo_api_test")
    ):
        pytest.fail("the test database must be local and named promo_api_test*")
    return url


def alembic_config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.cmd_opts = type("Options", (), {"x": [f"database_url={url}"]})()  # type: ignore[assignment]
    return config


@pytest.fixture(scope="session")
def migrated(database_url: str) -> Iterator[str]:
    """Build the schema with the real migrations, never with `metadata.create_all`."""

    command.downgrade(alembic_config(database_url), "base")
    command.upgrade(alembic_config(database_url), "head")
    yield database_url


@pytest_asyncio.fixture(scope="session")
async def engine(migrated: str) -> AsyncIterator[AsyncEngine]:
    db = create_async_engine(migrated)
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def clean(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {TABLES} CASCADE"))


@pytest_asyncio.fixture
async def app(engine: AsyncEngine, clean: None, key_source: StaticKeys) -> FastAPI:
    url = engine.url.render_as_string(hide_password=False)
    return create_app(settings_for(url), engine=engine, key_source=key_source)


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http
