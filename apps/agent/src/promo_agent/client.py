"""The only two calls the agent makes, always outwards: pair once, then report and ask.

The token goes only to the address it was paired with: HTTPS (or this PC), no redirects, no proxy
taken from the environment.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

PAIR_PATH = "/v1/agent/pair"
SYNC_PATH = "/v1/agent/sync"
ACTIONS: tuple[str, ...] = ("start", "stop")


class Unreachable(Exception):
    """No usable answer: network, server error or something unexpected. Worth retrying."""


class NotPaired(Exception):
    """The API refused the token: the agent was removed or replaced in the panel."""


class BadPairingCode(Exception):
    """The code is wrong, expired or already used."""


@dataclass(frozen=True)
class Command:
    id: int
    action: Literal["start", "stop"]


@dataclass(frozen=True)
class SyncReply:
    command: Command | None
    poll_seconds: float


def api_origin(url: str) -> str:
    """`https://host[:port]`; plain http only towards this PC."""

    parts = urlsplit(url.strip())
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parts.hostname
        or not (parts.scheme == "https" or (parts.scheme == "http" and local))
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValueError("use https://host[:port] (http only for localhost)")
    return f"{parts.scheme}://{parts.netloc}"


def report_body(
    state: str, completed_command_id: int | None, last_event: str | None, closing: bool
) -> dict[str, Any]:
    return {
        "process_state": state,
        "completed_command_id": completed_command_id,
        "last_event": last_event,
        "closing": closing,
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class ApiClient:
    def __init__(self, url: str, token: str | None = None, timeout: float = 10.0) -> None:
        self._origin = api_origin(url)
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            self._origin + path, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, json.loads(response.read(65536))
        except urllib.error.HTTPError as error:
            error.close()
            return error.code, None
        # HTTPException (cut or non-HTTP answer) is neither an OSError nor a ValueError.
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise Unreachable(type(error).__name__) from None

    def pair(self, code: str) -> tuple[str, str]:
        status, payload = self._post(PAIR_PATH, {"code": code})
        if status in {401, 422}:
            raise BadPairingCode()
        try:
            if status != 201:
                raise KeyError(status)
            return str(payload["agent_token"]), str(payload["workspace_id"])
        except (KeyError, TypeError):
            raise Unreachable(f"status {status}") from None

    def sync(
        self,
        state: str,
        completed_command_id: int | None = None,
        last_event: str | None = None,
        closing: bool = False,
    ) -> SyncReply:
        status, payload = self._post(
            SYNC_PATH, report_body(state, completed_command_id, last_event, closing)
        )
        if status == 401:
            raise NotPaired()
        try:
            if status != 200:
                raise KeyError(status)
            poll = min(max(float(payload["poll_interval_seconds"]), 1.0), 30.0)
            raw = payload["command"]
            command = None
            # Anything that is not exactly start or stop is not a command of this agent.
            if raw is not None and raw["action"] in ACTIONS:
                command = Command(int(raw["id"]), raw["action"])
        except (KeyError, TypeError, ValueError):
            raise Unreachable(f"status {status}") from None
        return SyncReply(command, poll)
