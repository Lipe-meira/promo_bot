"""Behavior that must hold before any database is involved: configuration, authentication on
every route, request validation, caching headers, CORS and the published contract."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from promo_api.schemas import WorkspaceCreate, WorkspaceUpdate
from promo_api.settings import ApiSettings
from tests.conftest import (
    ISSUER,
    SYNTHETIC_CLERK_SECRET,
    UNREACHABLE_DATABASE,
    WEB_ORIGIN,
    TokenFactory,
)

WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
PROTECTED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/v1/me", None),
    ("POST", "/v1/workspaces", {"name": "Synthetic"}),
    ("GET", f"/v1/workspaces/{WORKSPACE_ID}", None),
    ("PATCH", f"/v1/workspaces/{WORKSPACE_ID}", {"version": 1, "name": "Other"}),
    ("GET", f"/v1/workspaces/{WORKSPACE_ID}/status", None),
    ("GET", f"/v1/workspaces/{WORKSPACE_ID}/capabilities", None),
]
CONTRACT = Path(__file__).resolve().parents[1] / "openapi.json"


# --- configuration -------------------------------------------------------------------------


def settings(**overrides: Any) -> ApiSettings:
    values: dict[str, Any] = {
        "database_url": UNREACHABLE_DATABASE,
        "clerk_issuer": ISSUER,
        "clerk_authorized_parties": (WEB_ORIGIN,),
        "clerk_secret_key": SYNTHETIC_CLERK_SECRET,
    }
    values.update(overrides)
    return ApiSettings(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"clerk_secret_key": ""},
        {"clerk_secret_key": "   "},
        {"clerk_secret_key": "pk_test_this_is_the_publishable_key"},
        {"clerk_secret_key": "not-a-clerk-key"},
        {"clerk_issuer": "http://insecure.example"},
        {"clerk_issuer": "not a url"},
        {"clerk_authorized_parties": ()},
        {"clerk_authorized_parties": ""},
        {"clerk_authorized_parties": ("http://not-local.example",)},
        {"clerk_authorized_parties": ("https://app.example/path",)},
        {"clerk_authorized_parties": ("*",)},
        {"cors_allowed_origins": ("*",)},
        {"database_url": "sqlite+aiosqlite:///runtime.sqlite3"},
        {"database_url": "postgresql://sync-driver@localhost/db"},
        {"clerk_jwks_url": "http://insecure.example/jwks.json"},
        {"environment": "staging"},
    ],
)
def test_unsafe_configuration_does_not_start(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_configuration_comes_from_prefixed_variables_and_hides_the_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMO_API_DATABASE_URL", UNREACHABLE_DATABASE)
    monkeypatch.setenv("PROMO_API_CLERK_ISSUER", ISSUER + "/")
    monkeypatch.setenv("PROMO_API_CLERK_AUTHORIZED_PARTIES", f"{WEB_ORIGIN}, https://app.example")
    monkeypatch.setenv("PROMO_API_CLERK_SECRET_KEY", SYNTHETIC_CLERK_SECRET)
    # Variables of the legacy bot must never configure the API.
    monkeypatch.setenv("PROMO_BOT_DATABASE_URL", "sqlite+aiosqlite:///legacy.sqlite3")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://other/other")

    loaded = ApiSettings()  # type: ignore[call-arg]

    assert loaded.clerk_issuer == ISSUER
    assert loaded.jwks_url == f"{ISSUER}/.well-known/jwks.json"
    assert loaded.clerk_authorized_parties == (WEB_ORIGIN, "https://app.example")
    assert loaded.cors_origins == loaded.clerk_authorized_parties
    assert loaded.database_url.get_secret_value() == UNREACHABLE_DATABASE
    assert "nothing" not in repr(loaded)
    assert loaded.clerk_secret_key.get_secret_value() == SYNTHETIC_CLERK_SECRET
    assert SYNTHETIC_CLERK_SECRET not in repr(loaded)


@pytest.mark.parametrize(
    "missing",
    ["PROMO_API_CLERK_ISSUER", "PROMO_API_CLERK_AUTHORIZED_PARTIES", "PROMO_API_CLERK_SECRET_KEY"],
)
def test_missing_identity_configuration_is_an_error_not_an_open_mode(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMO_API_DATABASE_URL", UNREACHABLE_DATABASE)
    monkeypatch.setenv("PROMO_API_CLERK_ISSUER", ISSUER)
    monkeypatch.setenv("PROMO_API_CLERK_AUTHORIZED_PARTIES", WEB_ORIGIN)
    monkeypatch.setenv("PROMO_API_CLERK_SECRET_KEY", SYNTHETIC_CLERK_SECRET)
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        ApiSettings()  # type: ignore[call-arg]


def test_a_rejected_clerk_secret_is_not_echoed_in_the_startup_error() -> None:
    with pytest.raises(ValidationError) as error:
        settings(clerk_secret_key="pk_test_looks_like_a_secret_that_must_not_be_printed")

    assert "must_not_be_printed" not in str(error.value)


def test_there_is_no_setting_that_could_switch_authentication_off() -> None:
    suspicious = ("disable", "insecure", "skip", "bypass", "dev_user", "fake", "mock", "no_auth")

    assert not [
        name for name in ApiSettings.model_fields if any(word in name for word in suspicious)
    ]


# --- request validation (pure) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Minha   Loja ", "Minha Loja"), ("Ab", "Ab"), ("Promoções 24h ✨", "Promoções 24h ✨")],
)
def test_workspace_names_are_normalized(raw: str, expected: str) -> None:
    assert WorkspaceCreate(name=raw).name == expected


@pytest.mark.parametrize("name", ["", " ", "a", " a ", "x" * 61, "tab\tseparated\x00", "a​b\x07"])
def test_invalid_workspace_names_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreate(name=name)


@pytest.mark.parametrize(
    "timezone", ["", "Mars/Olympus", "america/sao_paulo", "../../etc/passwd", "UTC+3", "x" * 65]
)
def test_invalid_timezones_are_refused(timezone: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreate(name="Valid", timezone=timezone)


def test_timezone_defaults_and_accepts_iana_names() -> None:
    assert WorkspaceCreate(name="Valid").timezone == "America/Sao_Paulo"
    assert WorkspaceCreate(name="Valid", timezone="Europe/Lisbon").timezone == "Europe/Lisbon"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Valid", "owner_user_id": WORKSPACE_ID},
        {"name": "Valid", "role": "owner"},
        {"name": "Valid", "id": WORKSPACE_ID},
        {"name": "Valid", "version": 7},
    ],
)
def test_creation_refuses_fields_the_client_does_not_own(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreate.model_validate(body)


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Valid"},
        {"version": 0, "name": "Valid"},
        {"version": "1", "name": "Valid"},
        {"version": 1.0, "name": "Valid"},
        {"version": True, "name": "Valid"},
        {"version": 1, "owner_user_id": WORKSPACE_ID},
        {"version": 1, "role": "admin"},
        {"version": 1, "id": WORKSPACE_ID},
        {"version": 1, "created_at": "2026-01-01T00:00:00Z"},
    ],
)
def test_updates_require_a_strict_version_and_only_editable_fields(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkspaceUpdate.model_validate(body)


# --- authentication on every route -------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
async def test_every_v1_route_requires_a_valid_token(
    offline_client: httpx.AsyncClient,
    make_token: TokenFactory,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    now = int(time.time())
    attempts: dict[str, dict[str, str]] = {
        "no_header": {},
        "basic_scheme": {"Authorization": "Basic dXNlcjpwYXNz"},
        "empty_bearer": {"Authorization": "Bearer "},
        "garbage": {"Authorization": "Bearer not-a-token"},
        "wrong_issuer": {"Authorization": f"Bearer {make_token(iss='https://attacker.example')}"},
        "expired": {
            "Authorization": f"Bearer {make_token(iat=now - 300, nbf=now - 300, exp=now - 120)}"
        },
        "identity_headers_only": {"X-User-Id": "user_1", "X-Workspace-Role": "owner"},
    }

    for name, headers in attempts.items():
        response = await offline_client.request(method, path, json=body, headers=headers)

        assert response.status_code == 401, name
        error = response.json()["error"]
        expected = "AUTH_TOKEN_EXPIRED" if name == "expired" else "AUTH_INVALID_TOKEN"
        assert error["code"] == expected, name
        assert response.headers["www-authenticate"].startswith("Bearer"), name
        assert response.headers["cache-control"] == "no-store", name


async def test_tokens_are_not_accepted_from_cookies_or_query_strings(
    offline_client: httpx.AsyncClient, make_token: TokenFactory
) -> None:
    token = make_token()

    by_cookie = await offline_client.get("/v1/me", headers={"Cookie": f"__session={token}"})
    by_query = await offline_client.get("/v1/me", params={"token": token, "access_token": token})

    assert by_cookie.status_code == by_query.status_code == 401


# --- health, caching and CORS ------------------------------------------------------------


async def test_liveness_needs_no_database_and_readiness_reports_its_absence(
    offline_client: httpx.AsyncClient,
) -> None:
    alive = await offline_client.get("/healthz")
    ready = await offline_client.get("/readyz")

    assert (alive.status_code, alive.json()) == (200, {"status": "ok"})
    assert ready.status_code == 503
    assert ready.json()["error"]["code"] == "NOT_READY"
    assert "127.0.0.1" not in ready.text and "nobody" not in ready.text


async def test_every_response_forbids_shared_caching(offline_client: httpx.AsyncClient) -> None:
    for path in ("/healthz", "/v1/me", "/does-not-exist"):
        response = await offline_client.get(path)

        assert response.headers["cache-control"] == "no-store", path
        assert "Authorization" in response.headers["vary"], path
        assert "etag" not in response.headers, path


async def test_cors_allows_only_the_configured_origin_and_no_credentials(
    offline_client: httpx.AsyncClient,
) -> None:
    def preflight(origin: str) -> dict[str, str]:
        return {
            "Origin": origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type",
        }

    allowed = await offline_client.options("/v1/me", headers=preflight(WEB_ORIGIN))
    refused = await offline_client.options("/v1/me", headers=preflight("https://attacker.example"))

    assert allowed.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in refused.headers
    assert refused.status_code == 400


async def test_unknown_routes_and_methods_use_the_error_envelope(
    offline_client: httpx.AsyncClient,
) -> None:
    missing = await offline_client.get("/v1/unknown")
    wrong_method = await offline_client.delete("/v1/me")

    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert (wrong_method.status_code, wrong_method.json()["error"]["code"]) == (
        405,
        "METHOD_NOT_ALLOWED",
    )


# --- contract ----------------------------------------------------------------------------


def test_the_committed_openapi_document_matches_the_application(offline_app: FastAPI) -> None:
    """`openapi.json` feeds the web app's generated types; drift must fail here."""

    committed = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert committed == offline_app.openapi(), "run `uv run python -m promo_api.openapi`"


def test_the_contract_exposes_the_expected_operations_and_error_envelope(
    offline_app: FastAPI,
) -> None:
    document = offline_app.openapi()
    operations = {
        f"{method.upper()} {path}" for path, item in document["paths"].items() for method in item
    }

    assert operations == {
        "GET /healthz",
        "GET /readyz",
        "GET /v1/me",
        "POST /v1/workspaces",
        "GET /v1/workspaces/{workspace_id}",
        "PATCH /v1/workspaces/{workspace_id}",
        "GET /v1/workspaces/{workspace_id}/status",
        "GET /v1/workspaces/{workspace_id}/capabilities",
    }
    schemas = document["components"]["schemas"]
    assert schemas["WorkspaceCreate"]["additionalProperties"] is False
    assert schemas["WorkspaceUpdate"]["additionalProperties"] is False
    assert set(schemas["WorkspaceUpdate"]["required"]) == {"version"}
    assert "ErrorResponse" in schemas
    for path, item in document["paths"].items():
        if path.startswith("/v1/"):
            for operation in item.values():
                assert operation["security"] == [{"HTTPBearer": []}], path
                assert {"401", "403"} <= set(operation["responses"]), path
