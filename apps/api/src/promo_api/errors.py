"""One error envelope for the whole API: a stable `code`, a safe message, optional safe details.

Messages never echo request input, tokens or database values other than what is listed in
`details` on purpose.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("promo_api")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def _response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        body.model_dump(exclude_none=True), status_code=status_code, headers=headers
    )


_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
# Connection-level failures only; integrity or programming errors must stay loud 500s.
# `OSError` is here because asyncpg lets operating-system errors (DNS failure, unreachable
# network or host) escape unwrapped; `ConnectionError` and `TimeoutError` are subclasses of it,
# and `ssl.SSLError` is excluded again inside the handler.
_DATABASE_FAILURES: tuple[type[Exception], ...] = (
    OSError,
    OperationalError,
    InterfaceError,
    PoolTimeout,
)


# SQLSTATE classes: 08 connection exception, 53 insufficient resources (too many connections),
# 57 operator intervention (administrator shutdown, cannot connect now, crash shutdown).
_TRANSIENT_SQLSTATE_CLASSES = frozenset({"08", "53", "57"})


def _is_transient(exc: DBAPIError) -> bool:
    if exc.connection_invalidated:
        return True
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return isinstance(sqlstate, str) and sqlstate[:2] in _TRANSIENT_SQLSTATE_CLASSES


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field paths and pydantic error types only: never the rejected input.
        fields = [
            {"field": ".".join(str(part) for part in item["loc"][1:]), "type": item["type"]}
            for item in exc.errors()
        ]
        return _response(422, "VALIDATION_ERROR", "The request is invalid.", {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return _response(exc.status_code, code, "The request could not be handled.")

    async def database_unavailable(_: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, ssl.SSLError):
            # A failed certificate check or handshake is a configuration fault, not an outage:
            # retrying will not help, so it must stay a loud 500 instead of a retryable 503.
            return await unexpected(_, exc)
        # A suspended or restarting database, an exhausted pool or a dropped connection: the caller
        # should retry shortly. Only the exception type is logged; the message may name the host.
        logger.warning("database unavailable: %s", type(exc).__name__)
        return _response(
            503,
            "DATABASE_UNAVAILABLE",
            "The database is temporarily unavailable. Try again shortly.",
            headers={"Retry-After": "5"},
        )

    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__)
        return _response(500, "INTERNAL_ERROR", "Unexpected error.")

    async def driver_error(request: Request, exc: Exception) -> JSONResponse:
        # asyncpg's server-side errors are translated by SQLAlchemy to a bare `DBAPIError`, never to
        # `OperationalError`. Only the SQLSTATE classes that mean "try again" are an outage; any
        # other database error (integrity, syntax, ...) is a bug and stays a loud 500.
        if isinstance(exc, DBAPIError) and _is_transient(exc):
            return await database_unavailable(request, exc)
        return await unexpected(request, exc)

    for failure in _DATABASE_FAILURES:
        app.add_exception_handler(failure, database_unavailable)
    app.add_exception_handler(DBAPIError, driver_error)
    app.add_exception_handler(Exception, unexpected)
