"""A fake of the two API routes the agent calls, on loopback. No other network is used."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

TOKEN = "pbagent_synthetic-token-for-tests-only-000000000000"
WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"


class FakeApi:
    def __init__(self) -> None:
        self.reports: list[dict[str, Any]] = []
        self.authorizations: list[str] = []
        self.pairings: list[dict[str, Any]] = []
        self.command: dict[str, Any] | None = None
        self.status = 200
        self.redirect_to: str | None = None
        self.url = ""

    def reply(self, path: str, body: dict[str, Any], authorization: str) -> tuple[int, Any]:
        if self.status != 200:
            return self.status, {"error": {"code": "SYNTHETIC", "message": "synthetic"}}
        if path == "/v1/agent/pair":
            self.pairings.append(body)
            if body["code"] != "ABCD-EFGH-JKMN":
                return 401, {"error": {"code": "PAIRING_CODE_INVALID", "message": "no"}}
            return 201, {"agent_token": TOKEN, "workspace_id": WORKSPACE_ID}
        if path == "/v1/agent/sync":
            self.authorizations.append(authorization)
            if authorization != f"Bearer {TOKEN}":
                return 401, {"error": {"code": "AGENT_TOKEN_INVALID", "message": "no"}}
            self.reports.append(body)
            if self.command and body.get("completed_command_id") == self.command["id"]:
                self.command = None
            return 200, {"command": self.command, "poll_interval_seconds": 1}
        return 404, {}


def _handler(api: FakeApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if api.redirect_to is not None:
                self.send_response(307)
                self.send_header("Location", api.redirect_to + self.path)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status, payload = api.reply(self.path, body, self.headers.get("Authorization", ""))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def serve() -> Iterator[FakeApi]:
    api = FakeApi()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(api))
    api.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield api
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def api() -> Iterator[FakeApi]:
    yield from serve()


@pytest.fixture
def other_api() -> Iterator[FakeApi]:
    yield from serve()
