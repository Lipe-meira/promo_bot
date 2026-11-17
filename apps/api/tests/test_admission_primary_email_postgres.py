"""Admission requires Clerk to confirm that the claimed address is the user's *verified primary*.

The token claim `email_verified` can be true because some other address of the user is verified.
Clerk's Backend API is therefore consulted once, at first admission, after the invitation has
been found: unknown or uninvited accounts never cause a call, and an admitted user never does.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from promo_api import identity
from promo_api.auth.clerk_backend import identity_provider_unavailable
from promo_api.db.models import Invitation, User
from tests.conftest import FakeClerkDirectory, TokenFactory
from tests.test_api_postgres import ALICE, auth, count, invite

pytestmark = pytest.mark.postgres


async def _accepted(engine: AsyncEngine) -> bool:
    async with engine.connect() as connection:
        return await connection.scalar(select(Invitation.accepted_at)) is not None


async def test_an_unverified_primary_is_not_admitted_despite_a_verified_email_claim(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    await invite(engine, ALICE[1])
    clerk_directory.outcomes[ALICE[0]] = False  # token says verified; Clerk says the primary is not

    response = await client.get("/v1/me", headers=auth(make_token))

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"reason": "PRIMARY_EMAIL_NOT_VERIFIED"}
    assert await count(engine, User) == 0
    assert not await _accepted(engine)  # the invitation stays available for the real owner


async def test_a_provider_outage_never_admits_and_a_retry_does(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    await invite(engine, ALICE[1])
    clerk_directory.outcomes[ALICE[0]] = identity_provider_unavailable()

    unavailable = await client.get("/v1/me", headers=auth(make_token))
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "IDENTITY_PROVIDER_UNAVAILABLE"
    assert unavailable.headers["Retry-After"] == "5"
    assert await count(engine, User) == 0
    assert not await _accepted(engine)

    del clerk_directory.outcomes[ALICE[0]]
    assert (await client.get("/v1/me", headers=auth(make_token))).status_code == 200
    assert await count(engine, User) == 1


async def test_clerk_is_asked_once_at_admission_and_never_for_an_admitted_user(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    await invite(engine, ALICE[1])

    for _ in range(3):
        assert (await client.get("/v1/me", headers=auth(make_token))).status_code == 200

    assert clerk_directory.calls == [(ALICE[0], ALICE[1])]


async def test_an_admitted_user_is_not_affected_by_a_later_provider_outage(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    await invite(engine, ALICE[1])
    assert (await client.get("/v1/me", headers=auth(make_token))).status_code == 200
    clerk_directory.outcomes[ALICE[0]] = identity_provider_unavailable()

    assert (await client.get("/v1/me", headers=auth(make_token))).status_code == 200


async def test_clerk_is_not_asked_about_an_account_without_an_invitation(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    # Strangers must not be able to drive traffic to the Backend API (quota, rate limits).
    await invite(engine, "someone-else@example.test")

    response = await client.get("/v1/me", headers=auth(make_token))

    assert response.json()["error"]["details"] == {"reason": "NO_INVITATION"}
    assert clerk_directory.calls == []


async def test_clerk_is_not_asked_when_the_token_claims_already_fail(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    await invite(engine, ALICE[1])

    response = await client.get("/v1/me", headers=auth(make_token, email_verified=False))

    assert response.json()["error"]["details"] == {"reason": "EMAIL_NOT_VERIFIED"}
    assert clerk_directory.calls == []


async def test_a_revoked_invitation_is_refused_without_asking_clerk(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    make_token: TokenFactory,
    clerk_directory: FakeClerkDirectory,
) -> None:
    await invite(engine, ALICE[1])
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await identity.revoke(session, ALICE[1])

    response = await client.get("/v1/me", headers=auth(make_token))

    assert response.json()["error"]["details"] == {"reason": "NO_INVITATION"}
    assert clerk_directory.calls == []
