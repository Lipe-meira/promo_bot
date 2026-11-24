"""Manual registry of Telegram channels against an ephemeral PostgreSQL built by the real
migrations. Ids here are synthetic; nothing talks to Telegram.

Run explicitly: `pytest -m postgres` with `PROMO_API_TEST_DATABASE_URL`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from promo_api.db.models import TelegramDestination, TelegramSourceChannel
from tests.conftest import TokenFactory
from tests.test_api_postgres import ALICE, BOB, auth, count, create_workspace, invite

pytestmark = pytest.mark.postgres

SRC_1 = "-1001000000001"
SRC_2 = "-1001000000002"
SRC_3 = "-1001000000003"
DEST = "-1002000000001"
DEST_2 = "-1002000000002"


def base(workspace: dict[str, Any]) -> str:
    return f"/v1/workspaces/{workspace['id']}/telegram-channels"


async def setup_alice(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> tuple[dict[str, str], dict[str, Any]]:
    await invite(engine, ALICE[1])
    headers = auth(make_token, ALICE)
    return headers, await create_workspace(client, headers)


# --- persistence --------------------------------------------------------------------------


async def test_a_new_workspace_starts_empty_and_says_access_is_not_verified(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)

    response = await client.get(base(workspace), headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": workspace["id"],
        "sources": [],
        "destination": None,
        "max_sources": 50,
        "access_status": "not_verified",
    }


async def test_registered_channels_are_persisted_and_read_back_in_order(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)

    first = await client.post(
        f"{base(workspace)}/sources", json={"chat_id": SRC_2}, headers=headers
    )
    await client.post(f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers)
    saved = await client.put(
        f"{base(workspace)}/destination", json={"chat_id": DEST}, headers=headers
    )
    reloaded = await client.get(base(workspace), headers=headers)

    assert first.status_code == 201
    assert saved.status_code == 200
    body = reloaded.json()
    assert [item["chat_id"] for item in body["sources"]] == [SRC_2, SRC_1]  # registration order
    assert body["destination"]["chat_id"] == DEST
    assert body["access_status"] == "not_verified"
    assert await count(engine, TelegramSourceChannel) == 2
    assert await count(engine, TelegramDestination) == 1


async def test_a_source_can_be_removed_and_the_removal_is_persisted(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)
    for chat_id in (SRC_1, SRC_2):
        await client.post(f"{base(workspace)}/sources", json={"chat_id": chat_id}, headers=headers)

    removed = await client.delete(f"{base(workspace)}/sources/{SRC_1}", headers=headers)
    again = await client.delete(f"{base(workspace)}/sources/{SRC_1}", headers=headers)

    assert removed.status_code == 200
    assert [item["chat_id"] for item in removed.json()["sources"]] == [SRC_2]
    assert (again.status_code, again.json()["error"]["code"]) == (404, "SOURCE_CHANNEL_NOT_FOUND")
    reloaded = await client.get(base(workspace), headers=headers)
    assert [item["chat_id"] for item in reloaded.json()["sources"]] == [SRC_2]
    assert await count(engine, TelegramSourceChannel) == 1


async def test_the_destination_is_replaced_in_place_and_saving_it_again_changes_nothing(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)
    url = f"{base(workspace)}/destination"

    first = (await client.put(url, json={"chat_id": DEST}, headers=headers)).json()
    same = (await client.put(url, json={"chat_id": DEST}, headers=headers)).json()
    swapped = (await client.put(url, json={"chat_id": DEST_2}, headers=headers)).json()

    assert same["destination"] == first["destination"]  # idempotent, `updated_at` untouched
    assert swapped["destination"]["chat_id"] == DEST_2
    assert swapped["destination"]["updated_at"] >= first["destination"]["updated_at"]
    assert await count(engine, TelegramDestination) == 1  # one destination per workspace


# --- validation and duplicates --------------------------------------------------------------

INVALID_IDS: list[Any] = [
    "",
    "-100",
    "-1000123456789",  # the number after -100 cannot start with 0
    "100123456789",  # positive: a user, not a channel
    "-123456789",  # a basic group, not what the bot accepts
    "+1001234567890",
    "@some_channel",
    "https://t.me/some_channel",
    "-100 1234567890",
    " -1001234567890",
    "-1001234567890\n",
    "-1001234567890abc",
    "-100" + "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10",  # full-width digits
    "-100" + "1" * 16,  # too long
    -1001234567890,  # a number, not a string
    None,
]


@pytest.mark.parametrize("chat_id", INVALID_IDS)
async def test_invalid_ids_are_refused_without_echoing_them_or_writing(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory, chat_id: Any
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)

    added = await client.post(
        f"{base(workspace)}/sources", json={"chat_id": chat_id}, headers=headers
    )
    saved = await client.put(
        f"{base(workspace)}/destination", json={"chat_id": chat_id}, headers=headers
    )

    for response in (added, saved):
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"]["fields"][0]["field"] == "chat_id"
        assert "some_channel" not in response.text and "1234567890" not in response.text
    assert await count(engine, TelegramSourceChannel) == 0
    assert await count(engine, TelegramDestination) == 0


@pytest.mark.parametrize(
    "chat_id",
    [
        "-100",
        "abc",
        "-1001234567890abc",
        "-100" + "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10",
    ],
)
async def test_a_malformed_id_in_the_path_is_refused_before_any_lookup(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory, chat_id: str
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)

    response = await client.delete(f"{base(workspace)}/sources/{chat_id}", headers=headers)

    assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


async def test_unknown_fields_in_the_body_are_refused(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)

    response = await client.post(
        f"{base(workspace)}/sources",
        json={"chat_id": SRC_1, "workspace_id": str(uuid4())},
        headers=headers,
    )

    assert response.status_code == 422
    assert await count(engine, TelegramSourceChannel) == 0


async def test_a_duplicate_source_is_refused_and_leaves_one_row(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)
    await client.post(f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers)

    again = await client.post(
        f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers
    )

    assert (again.status_code, again.json()["error"]["code"]) == (409, "SOURCE_CHANNEL_DUPLICATE")
    assert await count(engine, TelegramSourceChannel) == 1


async def test_concurrent_registrations_of_the_same_source_succeed_exactly_once(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)

    responses = await asyncio.gather(
        *(
            client.post(f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers)
            for _ in range(5)
        )
    )

    assert sorted(response.status_code for response in responses) == [201, 409, 409, 409, 409]
    assert await count(engine, TelegramSourceChannel) == 1


async def test_a_chat_cannot_be_both_source_and_destination(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)
    await client.post(f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers)
    await client.put(f"{base(workspace)}/destination", json={"chat_id": DEST}, headers=headers)

    as_destination = await client.put(
        f"{base(workspace)}/destination", json={"chat_id": SRC_1}, headers=headers
    )
    as_source = await client.post(
        f"{base(workspace)}/sources", json={"chat_id": DEST}, headers=headers
    )

    assert as_destination.json()["error"]["code"] == "DESTINATION_IS_SOURCE"
    assert as_source.json()["error"]["code"] == "CHANNEL_IS_DESTINATION"
    assert {as_destination.status_code, as_source.status_code} == {409}
    state = (await client.get(base(workspace), headers=headers)).json()
    assert [item["chat_id"] for item in state["sources"]] == [SRC_1]
    assert state["destination"]["chat_id"] == DEST


async def test_the_number_of_sources_is_capped(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)
    for number in range(50):
        response = await client.post(
            f"{base(workspace)}/sources",
            json={"chat_id": f"-100{3000000000 + number}"},
            headers=headers,
        )
        assert response.status_code == 201

    over = await client.post(f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers)

    assert (over.status_code, over.json()["error"]["code"]) == (409, "SOURCE_CHANNEL_LIMIT")
    assert over.json()["error"]["details"] == {"max_sources": 50}
    assert await count(engine, TelegramSourceChannel) == 50


async def test_database_constraints_back_the_id_rules(engine: AsyncEngine, clean: None) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, clerk_user_id, email) "
                "VALUES (gen_random_uuid(), 'u', 'u@e.test')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, owner_user_id, name, timezone) "
                "SELECT gen_random_uuid(), id, 'Loja', 'UTC' FROM users"
            )
        )
    for bad in ("100123", "-1000123", "@canal", "-1001234567890\n", "-100" + "1" * 16):
        for table in ("telegram_source_channels", "telegram_destinations"):
            with pytest.raises(Exception, match=r"violates|too long"):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f"INSERT INTO {table} (workspace_id, chat_id) "
                            "SELECT id, :chat_id FROM workspaces"
                        ),
                        {"chat_id": bad},
                    )


# --- isolation between workspaces ------------------------------------------------------------


async def test_a_user_cannot_read_or_change_another_workspaces_channels(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1], BOB[1])
    alice, bob = auth(make_token, ALICE), auth(make_token, BOB)
    hers = await create_workspace(client, alice, "Loja da Alice")
    await create_workspace(client, bob, "Loja do Bob")
    await client.post(f"{base(hers)}/sources", json={"chat_id": SRC_1}, headers=alice)
    await client.put(f"{base(hers)}/destination", json={"chat_id": DEST}, headers=alice)
    unknown = await client.get(f"/v1/workspaces/{uuid4()}/telegram-channels", headers=bob)

    attempts = [
        await client.get(base(hers), headers=bob),
        await client.post(f"{base(hers)}/sources", json={"chat_id": SRC_2}, headers=bob),
        await client.delete(f"{base(hers)}/sources/{SRC_1}", headers=bob),
        await client.put(f"{base(hers)}/destination", json={"chat_id": DEST_2}, headers=bob),
    ]

    for response in attempts:
        assert response.status_code == 404  # same answer as a workspace that does not exist
        assert response.json() == unknown.json()
        assert SRC_1 not in response.text and DEST not in response.text
    mine = (await client.get(base(hers), headers=alice)).json()
    assert [item["chat_id"] for item in mine["sources"]] == [SRC_1]
    assert mine["destination"]["chat_id"] == DEST
    assert await count(engine, TelegramSourceChannel) == 1


async def test_two_workspaces_may_register_the_same_chat_without_seeing_each_other(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    await invite(engine, ALICE[1], BOB[1])
    alice, bob = auth(make_token, ALICE), auth(make_token, BOB)
    hers = await create_workspace(client, alice, "Loja da Alice")
    his = await create_workspace(client, bob, "Loja do Bob")

    first = await client.post(f"{base(hers)}/sources", json={"chat_id": SRC_1}, headers=alice)
    second = await client.post(f"{base(his)}/sources", json={"chat_id": SRC_1}, headers=bob)
    await client.post(f"{base(his)}/sources", json={"chat_id": SRC_2}, headers=bob)

    assert (first.status_code, second.status_code) == (201, 201)  # no cross-workspace uniqueness
    assert [
        i["chat_id"] for i in (await client.get(base(hers), headers=alice)).json()["sources"]
    ] == [SRC_1]
    assert [i["chat_id"] for i in (await client.get(base(his), headers=bob)).json()["sources"]] == [
        SRC_1,
        SRC_2,
    ]
    removed = await client.delete(f"{base(hers)}/sources/{SRC_1}", headers=alice)
    assert removed.json()["sources"] == []
    assert [i["chat_id"] for i in (await client.get(base(his), headers=bob)).json()["sources"]] == [
        SRC_1,
        SRC_2,
    ]


async def test_deleting_a_workspace_removes_its_channels(
    client: httpx.AsyncClient, engine: AsyncEngine, make_token: TokenFactory
) -> None:
    headers, workspace = await setup_alice(client, engine, make_token)
    await client.post(f"{base(workspace)}/sources", json={"chat_id": SRC_1}, headers=headers)
    await client.put(f"{base(workspace)}/destination", json={"chat_id": DEST}, headers=headers)

    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM workspaces"))

    async with engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(TelegramSourceChannel)) == 0
        assert await connection.scalar(select(func.count()).select_from(TelegramDestination)) == 0
