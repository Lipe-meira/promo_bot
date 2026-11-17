"""A bound on the request body, enforced before anything parses it.

FastAPI reads and parses a JSON body *before* it resolves the authentication dependency, so an
anonymous caller could otherwise make a small free instance buffer an arbitrarily large body.
Legitimate payloads are a few hundred bytes; the limit leaves ample room.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 16 * 1024

_REFUSAL = json.dumps(
    {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "The request body is too large."}}
).encode()
_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_REFUSAL)).encode()),
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
]


class _BodyTooLarge(Exception):
    pass


class BodySizeLimit:
    """Pure ASGI middleware: 413 when the body is, or grows past, `max_bytes`."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._declared_length(scope) > self.max_bytes:
            await self._refuse(send)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded:
                # Whatever the application made of the failed read, the answer is a 413.
                if not started:
                    started = True
                    await self._refuse(send)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if exceeded and not started:
            await self._refuse(send)

    @staticmethod
    def _declared_length(scope: Scope) -> int:
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0  # the server rejects a malformed length itself
        return 0

    @staticmethod
    async def _refuse(send: Send) -> None:
        await send({"type": "http.response.start", "status": 413, "headers": _HEADERS})
        await send({"type": "http.response.body", "body": _REFUSAL})
