"""End-to-end API behavior against an ephemeral PostgreSQL built by the real migrations.

Run explicitly: `scripts/test-postgres.ps1` (or `pytest -m postgres` with
`PROMO_API_TEST_DATABASE_URL`). Never selected by the default test run.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from promo_api import identity
from promo_api.db.models import Base, Invitation, User, Workspace, WorkspaceMembership
from tests.conftest import TokenFactory, alembic_config

pytestmark = pytest.mark.postgres

ALICE = ("user_alice", "alice@example.test")
BOB = ("user_bob", "bob@example.test")


def auth(make_token: TokenFactory, who: tuple[str, str] = ALICE, **claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(who[0], email=who[1], **claims)}"}


async def invite(engine: AsyncEngine, *emails: str) -> None:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for email in emails:
            await identity.invite(session, email)


async def count(engine: AsyncEngine, model: type[Base]) -> int:
    async with engine.connect() as connection:
        return int(await connection.scalar(select(func.count()).select_from(model)) or 0)


async def create_workspace(
    client: httpx.AsyncClient, headers: dict[str, str], name: str = "Loja da Alice"
) -> dict[str, Any]:
    response = await client.post("/v1/workspaces", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- private admission ---------------------------------------------------------------------


async def test_a_valid_identity_without_invitation_is_not_admitted(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, "someone-else@example.test")

    response = await client.get("/v1/me", headers=auth(make_token))

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "NOT_ADMITTED",
        "message": "This account has not been admitted to the private preview.",
        "details": {"reason": "NO_INVITATION"},
    }
    assert await count(engine, User) == 0


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"email_verified": False}, "EMAIL_NOT_VERIFIED"),
        ({"email_verified": "true"}, "EMAIL_NOT_VERIFIED"),
        ({"drop": ("email",)}, "EMAIL_CLAIM_MISSING"),
        ({"drop": ("email_verified",)}, "EMAIL_NOT_VERIFIED"),
    ],
)
async def test_an_invitation_only_counts_for_a_verified_email_claim(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    claims: dict[str, Any],
    reason: str,
) -> None:
    await invite(engine, ALICE[1])

    response = await client.get("/v1/me", headers=auth(make_token, **claims))

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"reason": reason}
    assert await count(engine, User) == 0


async def test_an_invited_user_is_admitted_once_and_starts_without_workspaces(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, "  ALICE@Example.TEST ")

    first = await client.get("/v1/me", headers=auth(make_token))
    second = await client.get("/v1/me", headers=auth(make_token))

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["user"]["email"] == ALICE[1]
    assert first.json()["workspaces"] == []
    assert await count(engine, User) == 1
    async with engine.connect() as connection:
        accepted = await connection.scalar(select(Invitation.accepted_at))
    assert accepted is not None


async def test_concurrent_first_requests_create_exactly_one_user(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])

    responses = await asyncio.gather(
        *(client.get("/v1/me", headers=auth(make_token)) for _ in range(8))
    )

    assert [response.status_code for response in responses] == [200] * 8
    assert len({response.json()["user"]["id"] for response in responses}) == 1
    assert await count(engine, User) == 1


async def test_the_email_of_an_admitted_user_cannot_be_claimed_by_another_identity(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    assert (await client.get("/v1/me", headers=auth(make_token))).status_code == 200

    impostor = await client.get("/v1/me", headers=auth(make_token, ("user_other", ALICE[1])))

    assert impostor.status_code == 403
    assert impostor.json()["error"]["details"] == {"reason": "INVITATION_ALREADY_USED"}
    assert await count(engine, User) == 1


async def test_two_identities_racing_for_one_invitation_admit_exactly_one(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    rivals = [(f"user_rival_{index}", ALICE[1]) for index in range(6)]

    responses = await asyncio.gather(
        *(client.get("/v1/me", headers=auth(make_token, rival)) for rival in rivals)
    )

    assert sorted(response.status_code for response in responses) == [200] + [403] * 5
    for response in responses:
        if response.status_code == 403:
            assert response.json()["error"]["details"] == {"reason": "INVITATION_ALREADY_USED"}
    assert await count(engine, User) == 1


async def test_admission_follows_the_local_user_not_later_token_claims(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    assert (await client.get("/v1/me", headers=auth(make_token))).status_code == 200

    changed = await client.get(
        "/v1/me", headers=auth(make_token, (ALICE[0], "renamed@example.test"))
    )

    assert changed.status_code == 200
    assert changed.json()["user"]["email"] == ALICE[1]


async def test_revoking_an_invitation_disables_the_user_and_reinviting_restores_it(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    workspace = await create_workspace(client, headers)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        assert await identity.revoke(session, ALICE[1]) is True
    revoked = [
        await client.get("/v1/me", headers=headers),
        await client.get(f"/v1/workspaces/{workspace['id']}", headers=headers),
        await client.patch(
            f"/v1/workspaces/{workspace['id']}", json={"version": 1, "name": "X1"}, headers=headers
        ),
    ]
    await invite(engine, ALICE[1])
    restored = await client.get("/v1/me", headers=headers)

    for response in revoked:
        assert response.status_code == 403
        assert response.json()["error"]["details"] == {"reason": "ACCESS_REVOKED"}
    assert restored.status_code == 200
    assert [item["id"] for item in restored.json()["workspaces"]] == [workspace["id"]]


# --- workspace creation --------------------------------------------------------------------


async def test_a_created_workspace_is_persisted_with_its_owner_membership(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)

    created = await client.post(
        "/v1/workspaces",
        json={"name": "  Promoções   da Alice ", "timezone": "America/Manaus"},
        headers=headers,
    )
    fetched = await client.get(f"/v1/workspaces/{created.json()['id']}", headers=headers)
    me = await client.get("/v1/me", headers=headers)

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Promoções da Alice"
    assert body["timezone"] == "America/Manaus"
    assert (body["version"], body["role"]) == (1, "owner")
    assert fetched.json() == body
    assert me.json()["workspaces"] == [{"id": body["id"], "name": body["name"], "role": "owner"}]
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(Workspace.name, WorkspaceMembership.role, User.clerk_user_id)
                .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
                .join(User, User.id == WorkspaceMembership.user_id)
            )
        ).one()
    assert tuple(row) == ("Promoções da Alice", "owner", ALICE[0])


async def test_repeated_creation_is_refused_and_points_to_the_existing_workspace(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    first = await create_workspace(client, headers)

    again = await client.post("/v1/workspaces", json={"name": "Another one"}, headers=headers)

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "WORKSPACE_ALREADY_EXISTS"
    assert again.json()["error"]["details"] == {"workspace_id": first["id"]}
    assert await count(engine, Workspace) == 1
    assert await count(engine, WorkspaceMembership) == 1


async def test_concurrent_creation_yields_exactly_one_workspace(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    assert (await client.get("/v1/me", headers=headers)).status_code == 200

    responses = await asyncio.gather(
        *(
            client.post("/v1/workspaces", json={"name": f"Race {index}"}, headers=headers)
            for index in range(8)
        )
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201] + [409] * 7
    winner = next(response.json() for response in responses if response.status_code == 201)
    for response in responses:
        if response.status_code == 409:
            assert response.json()["error"]["details"] == {"workspace_id": winner["id"]}
    assert await count(engine, Workspace) == 1
    assert await count(engine, WorkspaceMembership) == 1


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": "a"}, "name"),
        ({"name": "x" * 61}, "name"),
        ({"name": "bad\x00name"}, "name"),
        ({"name": 123}, "name"),
        ({}, "name"),
        ({"name": "Valid", "timezone": "Mars/Olympus"}, "timezone"),
        ({"name": "Valid", "timezone": "../etc/passwd"}, "timezone"),
        ({"name": "Valid", "owner_user_id": str(uuid4())}, "owner_user_id"),
        ({"name": "Valid", "role": "owner"}, "role"),
        ({"name": "Valid", "secret-marker-777": True}, "secret-marker-777"),
    ],
)
async def test_invalid_creation_is_refused_without_echoing_values_or_writing(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    body: dict[str, Any],
    field: str,
) -> None:
    await invite(engine, ALICE[1])

    response = await client.post("/v1/workspaces", json=body, headers=auth(make_token))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert field in [item["field"] for item in error["details"]["fields"]]
    assert "input" not in response.text and "Mars/Olympus" not in response.text
    assert await count(engine, Workspace) == 0


# --- isolation between users ---------------------------------------------------------------


async def test_a_user_cannot_see_or_change_another_users_workspace(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1], BOB[1])
    alice, bob = auth(make_token, ALICE), auth(make_token, BOB)
    hers = await create_workspace(client, alice, "Loja da Alice")
    his = await create_workspace(client, bob, "Loja do Bob")
    path = f"/v1/workspaces/{hers['id']}"

    attempts = [
        await client.get(path, headers=bob),
        await client.get(f"{path}/status", headers=bob),
        await client.get(f"{path}/capabilities", headers=bob),
        await client.patch(path, json={"version": 1, "name": "Tomada"}, headers=bob),
        await client.patch(path, json={"version": 99, "name": "Tomada"}, headers=bob),
    ]
    unknown = await client.get(f"/v1/workspaces/{uuid4()}", headers=bob)

    for response in attempts:
        # Indistinguishable from a workspace that does not exist.
        assert response.status_code == 404
        assert response.json() == unknown.json()
        assert "Alice" not in response.text
    assert (await client.get(path, headers=alice)).json() == hers
    assert [
        item["id"] for item in (await client.get("/v1/me", headers=bob)).json()["workspaces"]
    ] == [his["id"]]
    assert [
        item["id"] for item in (await client.get("/v1/me", headers=alice)).json()["workspaces"]
    ] == [hers["id"]]


async def test_identity_and_role_sent_by_the_browser_are_ignored(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1], BOB[1])
    hers = await create_workspace(client, auth(make_token, ALICE))
    async with engine.connect() as connection:
        alice_id = await connection.scalar(select(User.id).where(User.clerk_user_id == ALICE[0]))
    forged_headers = {
        **auth(make_token, BOB, role="owner", org_role="admin", workspace_id=hers["id"]),
        "X-User-Id": str(alice_id),
        "X-Clerk-User-Id": ALICE[0],
        "X-Workspace-Id": hers["id"],
        "X-Role": "owner",
    }

    response = await client.get(
        f"/v1/workspaces/{hers['id']}", headers=forged_headers, params={"user_id": str(alice_id)}
    )

    assert response.status_code == 404


async def test_malformed_workspace_ids_are_refused_before_any_lookup(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])

    response = await client.get("/v1/workspaces/not-a-uuid", headers=auth(make_token))

    assert response.status_code == 422


# --- updates with optimistic concurrency -------------------------------------------------


async def test_settings_are_updated_persisted_and_versioned(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)
    path = f"/v1/workspaces/{created['id']}"

    renamed = await client.patch(path, json={"version": 1, "name": "Novo nome"}, headers=headers)
    moved = await client.patch(
        path, json={"version": 2, "timezone": "Europe/Lisbon"}, headers=headers
    )
    reloaded = await client.get(path, headers=headers)

    assert (renamed.status_code, moved.status_code) == (200, 200)
    assert (renamed.json()["name"], renamed.json()["version"]) == ("Novo nome", 2)
    assert reloaded.json() == moved.json()
    assert reloaded.json()["name"] == "Novo nome"
    assert reloaded.json()["timezone"] == "Europe/Lisbon"
    assert reloaded.json()["version"] == 3
    assert reloaded.json()["updated_at"] > created["updated_at"]
    assert reloaded.json()["created_at"] == created["created_at"]


async def test_a_stale_version_is_refused_and_changes_nothing(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)
    path = f"/v1/workspaces/{created['id']}"
    assert (
        await client.patch(path, json={"version": 1, "name": "Primeira"}, headers=headers)
    ).status_code == 200

    stale = await client.patch(path, json={"version": 1, "name": "Atrasada"}, headers=headers)
    future = await client.patch(path, json={"version": 9, "name": "Futura"}, headers=headers)

    for response in (stale, future):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "WORKSPACE_VERSION_CONFLICT"
        assert response.json()["error"]["details"] == {"current_version": 2}
    assert (await client.get(path, headers=headers)).json()["name"] == "Primeira"


async def test_concurrent_updates_from_the_same_version_apply_exactly_once(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)
    path = f"/v1/workspaces/{created['id']}"

    responses = await asyncio.gather(
        *(
            client.patch(path, json={"version": 1, "name": f"Edição {index}"}, headers=headers)
            for index in range(6)
        )
    )

    assert sorted(response.status_code for response in responses) == [200] + [409] * 5
    winner = next(response.json() for response in responses if response.status_code == 200)
    final = (await client.get(path, headers=headers)).json()
    assert final["version"] == 2
    assert final["name"] == winner["name"]


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Sem versão"},
        {"version": 1},
        {"version": 1, "name": None, "timezone": None},
        {"version": "1", "name": "Texto"},
        {"version": 1, "name": "a"},
        {"version": 1, "timezone": "Mars/Olympus"},
        {"version": 1, "name": "Válido", "owner_user_id": str(uuid4())},
        {"version": 1, "name": "Válido", "role": "admin"},
        {"version": 1, "name": "Válido", "id": str(uuid4())},
    ],
)
async def test_invalid_updates_are_refused_and_change_nothing(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory, body: dict[str, Any]
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)
    path = f"/v1/workspaces/{created['id']}"

    response = await client.patch(path, json=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await client.get(path, headers=headers)).json() == created


# --- status and capabilities: only what is real ------------------------------------------


async def test_status_never_reports_anything_online_without_a_heartbeat(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)

    response = await client.get(f"/v1/workspaces/{created['id']}/status", headers=headers)

    body = response.json()
    assert response.status_code == 200
    assert body["workspace_id"] == created["id"]
    assert body["operational"] is False
    assert body["worker"]["state"] == "not_provisioned"
    assert body["telegram"]["state"] == "not_connected"
    assert body["aliexpress"]["state"] == "not_configured"
    for component in ("worker", "telegram", "aliexpress"):
        assert body[component]["last_heartbeat_at"] is None
        assert body[component]["reason"]


async def test_only_implemented_capabilities_are_available_and_the_rest_explain_why(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)

    response = await client.get(f"/v1/workspaces/{created['id']}/capabilities", headers=headers)

    capabilities = {item["key"]: item for item in response.json()["capabilities"]}
    assert response.status_code == 200
    assert [key for key, item in capabilities.items() if item["available"]] == [
        "workspace_settings"
    ]
    for key in (
        "telegram_connection",
        "relay_automation",
        "aliexpress_credentials",
        "automatic_publishing",
        "promotion_history",
        "channel_selection",
    ):
        assert capabilities[key]["available"] is False
        assert capabilities[key]["reason_code"]
        assert capabilities[key]["reason"]


# --- operational properties --------------------------------------------------------------


async def test_authenticated_responses_are_never_cacheable(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1])
    headers = auth(make_token)
    created = await create_workspace(client, headers)

    for path in (
        "/v1/me",
        f"/v1/workspaces/{created['id']}",
        f"/v1/workspaces/{created['id']}/status",
    ):
        response = await client.get(path, headers=headers)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "Authorization" in response.headers["vary"]
        assert "etag" not in response.headers and "expires" not in response.headers


async def test_readiness_is_true_only_on_a_migrated_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")

    assert (response.status_code, response.json()) == (200, {"status": "ready", "database": "ok"})


async def test_database_constraints_back_the_application_rules(
    engine: AsyncEngine, clean: None
) -> None:
    statements = [
        "INSERT INTO users (id, clerk_user_id, email) "
        "VALUES (gen_random_uuid(), 'u1', 'UPPER@example.test')",
        "INSERT INTO invitations (id, email) VALUES (gen_random_uuid(), 'UPPER@example.test')",
        "INSERT INTO workspaces (id, owner_user_id, name, timezone) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'Sem dono', 'UTC')",
    ]
    for statement in statements:
        with pytest.raises(Exception, match="violates"):
            async with engine.begin() as connection:
                await connection.execute(text(statement))


def test_migrations_match_the_models_and_round_trip(migrated: str) -> None:
    """Sync on purpose: Alembic drives its own event loop."""

    config = alembic_config(migrated)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)  # fails when the models and the migrations have drifted
