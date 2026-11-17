"""Turns a provider connection string into a safe asyncpg configuration.

Managed PostgreSQL (Neon) hands out libpq-style URLs (`?sslmode=require&channel_binding=require`).
SQLAlchemy forwards every URL query parameter to `asyncpg.connect()` as a keyword argument, so
those parameters raise `TypeError`; and asyncpg verifies nothing unless it receives an explicit
`ssl` argument. `prepare_database` therefore removes the libpq parameters and replaces them with
an explicit, always-verified TLS context for any host that is not the local machine.

Errors raised here never contain the URL, the host or the password: only parameter *names*.
"""

from __future__ import annotations

import ssl
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from promo_api.settings import ApiSettings

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
# libpq parameters that are safe to drop once the equivalent explicit setting is applied.
_LIBPQ_ONLY = frozenset({"sslmode", "channel_binding", "sslnegotiation"})
_PLAINTEXT_MODES = frozenset({"disable", "allow", "prefer"})
_TLS_MODES = frozenset({"require", "verify-ca", "verify-full"})

CONNECT_TIMEOUT_SECONDS = 15.0
COMMAND_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class PreparedDatabase:
    """A URL free of libpq parameters plus the `connect_args` that replace them."""

    url: URL
    connect_args: dict[str, Any]
    #: True for a Neon-style pooler endpoint (PgBouncer in transaction mode).
    pooled: bool


def _unique_statement_name() -> str:
    return f"__promo_{uuid.uuid4().hex}__"


def prepare_database(
    database_url: str,
    *,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    command_timeout: float | None = None,
) -> PreparedDatabase:
    """`command_timeout` bounds one query: only request-serving engines set it, never migrations."""

    try:
        url = make_url(database_url)
    except ArgumentError:
        raise ValueError("The database URL is malformed.") from None

    unknown = sorted(name for name in url.query if name not in _LIBPQ_ONLY)
    if unknown:
        raise ValueError(f"Unsupported database URL parameter(s): {', '.join(unknown)}.")

    sslmode = url.query.get("sslmode")
    if sslmode is not None and (
        not isinstance(sslmode, str) or sslmode not in _PLAINTEXT_MODES | _TLS_MODES
    ):
        raise ValueError("The sslmode parameter has an unsupported value.")

    host = url.host
    local = host is None or host in _LOOPBACK
    connect_args: dict[str, Any] = {"timeout": connect_timeout}
    if command_timeout is not None:
        connect_args["command_timeout"] = command_timeout
    if sslmode in _TLS_MODES or (not local and sslmode is None):
        # Never "encrypt but do not verify": libpq's `require` is upgraded to full verification.
        connect_args["ssl"] = ssl.create_default_context()
    elif not local:
        raise ValueError("TLS is required for a database that is not on the local machine.")

    pooled = host is not None and host.split(".", 1)[0].endswith("-pooler")
    if pooled:
        # A server connection is shared between clients: asyncpg must not keep named statements.
        connect_args.update(
            statement_cache_size=0,
            prepared_statement_cache_size=0,
            prepared_statement_name_func=_unique_statement_name,
        )
    return PreparedDatabase(url=url.set(query={}), connect_args=connect_args, pooled=pooled)


def create_operator_engine(database_url: str, *, require_direct: bool = False) -> AsyncEngine:
    """Engine for a short-lived operator process (migrations, admin CLI): no pool is kept.

    `require_direct` refuses a pooler endpoint. PgBouncer in transaction mode shares server
    connections between clients, which is unsuitable for schema changes.
    """

    prepared = prepare_database(database_url)
    if require_direct and prepared.pooled:
        raise ValueError("Schema migrations require the direct (non-pooler) connection endpoint.")
    return create_async_engine(
        prepared.url,
        connect_args=prepared.connect_args,
        poolclass=NullPool,
        # Error text would otherwise carry the bound parameters (e-mail addresses, Clerk ids).
        hide_parameters=True,
    )


def confirm_target(database_url: str, confirmation: str | None) -> None:
    """A non-local target must be named explicitly by the operator before a migration runs.

    Guards against a stale exported variable pointing at the wrong database. The expected host is
    deliberately not echoed in the error.
    """

    host = prepare_database(database_url).url.host
    if host is None or host in _LOOPBACK:
        return
    if confirmation is None or confirmation.strip().lower() != host.lower():
        raise ValueError("The target database host must be confirmed: pass -x confirm=<host>.")


def create_engine(settings: ApiSettings) -> AsyncEngine:
    """The API's engine: a small pool whose connections are verified before use.

    `pool_pre_ping` replaces a connection that the provider dropped while the compute was
    suspended; `pool_recycle` retires connections before that happens; both waits are bounded.
    """

    prepared = prepare_database(
        settings.database_url.get_secret_value(),
        connect_timeout=settings.db_connect_timeout_seconds,
        command_timeout=COMMAND_TIMEOUT_SECONDS,
    )
    return create_async_engine(
        prepared.url,
        connect_args=prepared.connect_args,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        # Server logs must not receive bound parameters (e-mail addresses, Clerk ids).
        hide_parameters=True,
    )
