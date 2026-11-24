"""The request body is bounded before anything reads it.

FastAPI parses a JSON body before it resolves the authentication dependency, so without a limit
an anonymous caller can make a 512 MB free instance buffer an arbitrarily large body. Legitimate
payloads here are a few hundred bytes (a workspace name and a time zone).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from promo_api.app import create_app
from promo_api.limits import MAX_BODY_BYTES
from tests.conftest import UNREACHABLE_DATABASE, settings_for


class Reads:
    """Counts the bytes the application actually pulled from the connection."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.bytes = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def counting() -> Any:
            message = await receive()
            self.bytes += len(message.get("body", b""))
            return message

        await self.app(scope, counting, send)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


@pytest.fixture
def reads(key_source: Any) -> Reads:
    return Reads(create_app(settings_for(UNREACHABLE_DATABASE), key_source=key_source))


async def test_an_oversized_declared_body_is_refused_without_reading_it(reads: Reads) -> None:
    body = b'{"name":"' + b"a" * (MAX_BODY_BYTES * 8) + b'"}'

    async with _client(reads) as client:
        response = await client.post("/v1/workspaces", content=body)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert reads.bytes == 0
    assert response.headers["Cache-Control"] == "no-store"


async def test_an_oversized_streamed_body_stops_being_read_at_the_limit(reads: Reads) -> None:
    async def stream() -> AsyncIterator[bytes]:
        for _ in range(2_000):  # 2 MB with no Content-Length: chunked transfer
            yield b"a" * 1_024

    async with _client(reads) as client:
        response = await client.post("/v1/workspaces", content=stream())

    assert response.status_code == 413
    assert reads.bytes <= MAX_BODY_BYTES + 1_024  # at most the chunk that crossed the limit


async def test_the_refusal_carries_the_cors_headers_so_a_browser_can_read_it(
    reads: Reads,
) -> None:
    from tests.conftest import WEB_ORIGIN

    async with _client(reads) as client:
        response = await client.post(
            "/v1/workspaces",
            content=b"a" * (MAX_BODY_BYTES + 1),
            headers={"Origin": WEB_ORIGIN},
        )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == WEB_ORIGIN


async def test_a_normal_request_is_unaffected(reads: Reads) -> None:
    async with _client(reads) as client:
        response = await client.post(
            "/v1/workspaces",
            json={"name": "My workspace", "timezone": "America/Sao_Paulo"},
        )  # no token: refused by authentication, not by the size limit

    assert response.status_code == 401
    assert reads.bytes > 0


async def test_a_body_exactly_at_the_limit_is_still_read(reads: Reads) -> None:
    async with _client(reads) as client:
        response = await client.post("/v1/workspaces", content=b"a" * MAX_BODY_BYTES)

    assert response.status_code != 413
    assert reads.bytes == MAX_BODY_BYTES
