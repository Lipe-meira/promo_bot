"""Read-only preflight of the managed database, run by the operator before a migration.

`python -m promo_api.preflight --host <host> --database <db> --role <role>` reads the URL from
`PROMO_API_DATABASE_URL` only, checks that the *target* (host, database, role, direct endpoint,
driver) is the expected one **before connecting**, then opens one `READ ONLY` transaction that
reports identity, TLS, schema and the Alembic revision. It never writes, never migrates, and never
prints the URL, the password or any value it was given: only OK/FAIL lines and table names.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection

from promo_api.db.engine import create_operator_engine, prepare_database
from promo_api.db.models import Base
from promo_api.readiness import shipped_heads

EXPECTED_TABLES: frozenset[str] = frozenset(Base.metadata.tables)
_VERSION_TABLE = "alembic_version"


@dataclass(frozen=True, slots=True)
class Expectation:
    host: str
    database: str
    role: str
    schema: str = "public"


@dataclass(frozen=True, slots=True)
class Observed:
    database: str
    role: str
    schema: str
    #: The client's own link to the endpoint: encrypted and certificate + hostname verified.
    client_tls: bool
    #: `pg_stat_ssl.ssl` of the backend. Informational: behind a TLS-terminating proxy (Neon) it
    #: describes the proxy-to-compute hop and is legitimately false.
    server_tls: bool
    tables: frozenset[str]
    revisions: frozenset[str]


@dataclass(slots=True)
class Report:
    #: empty | migrated | outdated | unexpected
    state: str
    problems: list[str] = field(default_factory=list)


def check_target(database_url: str, expected: Expectation) -> list[str]:
    """Problems with the *target*, found without connecting. Messages never echo values."""

    try:
        url = make_url(database_url)
    except ArgumentError:
        return ["The database URL is malformed."]
    problems: list[str] = []
    if url.drivername != "postgresql+asyncpg":
        problems.append("The driver must be postgresql+asyncpg.")
    try:
        prepare_database(database_url)
    except ValueError as error:
        # The message names parameters only, never values (see db/engine.py).
        problems.append(str(error))
    host = (url.host or "").lower()
    if host.split(".", 1)[0].endswith("-pooler"):
        problems.append("The URL points at a pooler endpoint; use the direct endpoint.")
    elif host != expected.host.lower():
        problems.append("The host is not the expected one.")
    if url.database != expected.database:
        problems.append("The database name is not the expected one.")
    if url.username != expected.role:
        problems.append("The role is not the expected one.")
    return problems


def evaluate(observed: Observed, expected: Expectation, heads: frozenset[str]) -> Report:
    problems: list[str] = []
    if observed.database != expected.database:
        problems.append("The connected database is not the expected one.")
    if observed.role != expected.role:
        problems.append("The connected role is not the expected one.")
    if observed.schema != expected.schema:
        problems.append("The current schema is not the expected one.")
    if not observed.client_tls:
        problems.append(
            "The client link is not encrypted with a verified certificate and hostname "
            "(the server-side pg_stat_ssl flag is not a substitute)."
        )

    has_version = _VERSION_TABLE in observed.tables
    app_tables = observed.tables - {_VERSION_TABLE}
    if not observed.tables:
        state = "empty"
    elif not has_version:
        state = "unexpected"
        problems.append("The schema has tables but no Alembic version table; do not migrate.")
    elif observed.revisions == heads and app_tables == EXPECTED_TABLES:
        state = "migrated"
    elif observed.revisions == heads:
        state = "unexpected"
        problems.append("The revision is current but the table set differs from the models.")
    else:
        state = "outdated"
    if state == "unexpected" and app_tables - EXPECTED_TABLES:
        problems.append("The schema contains tables this application does not own.")
    return Report(state=state, problems=problems)


def client_link_is_verified(context: object, *, encrypted: bool) -> bool:
    """True only for an encrypted link whose context demands a certificate and a matching host."""

    return (
        encrypted
        and isinstance(context, ssl.SSLContext)
        and context.verify_mode == ssl.CERT_REQUIRED
        and context.check_hostname
    )


def _client_tls(connection: AsyncConnection) -> bool:
    """Reads the asyncpg transport of the live connection; any surprise counts as not verified."""

    try:
        raw = connection.sync_connection.connection.driver_connection  # type: ignore[union-attr]
        transport = raw._transport  # type: ignore[union-attr]
        return client_link_is_verified(
            transport.get_extra_info("sslcontext"),
            encrypted=transport.get_extra_info("ssl_object") is not None,
        )
    except Exception:
        return False


async def observe(database_url: str, expected: Expectation) -> Observed:
    engine = create_operator_engine(database_url, require_direct=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            row = (
                await connection.execute(
                    text(
                        "SELECT current_database(), current_user, current_schema(), "
                        "COALESCE((SELECT ssl FROM pg_stat_ssl "
                        "WHERE pid = pg_backend_pid()), false)"
                    )
                )
            ).one()
            schema = str(row[2])
            client_tls = _client_tls(connection)
            tables = frozenset(
                await connection.scalars(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
                    ),
                    {"schema": expected.schema},
                )
            )
            revisions: frozenset[str] = frozenset()
            if _VERSION_TABLE in tables:
                revisions = frozenset(
                    await connection.scalars(
                        text(f"SELECT version_num FROM {expected.schema}.{_VERSION_TABLE}")
                    )
                )
            await connection.rollback()
    finally:
        await engine.dispose()
    return Observed(
        database=str(row[0]),
        role=str(row[1]),
        schema=schema,
        client_tls=client_tls,
        server_tls=bool(row[3]),
        tables=tables,
        revisions=revisions,
    )


def _parse(argv: list[str] | None) -> Expectation:
    parser = argparse.ArgumentParser(prog="promo_api.preflight", description=__doc__)
    parser.add_argument("--host", required=True, help="expected direct endpoint host")
    parser.add_argument("--database", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--schema", default="public")
    args = parser.parse_args(argv)
    if not args.schema.isidentifier():
        parser.error("--schema must be a plain identifier")
    return Expectation(args.host, args.database, args.role, args.schema)


def main(argv: list[str] | None = None) -> int:
    expected = _parse(argv)
    url = os.environ.get("PROMO_API_DATABASE_URL", "")
    problems = check_target(url, expected)
    if problems:
        print("TARGET FAIL (nothing was connected):")
        for problem in problems:
            print(f"  - {problem}")
        return 2
    print("TARGET OK: host, database, role, driver and direct endpoint match.")
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    heads = shipped_heads(migrations)
    try:
        observed = asyncio.run(observe(url, expected))
    except Exception as error:  # never print the message: it can carry the host
        print(f"CONNECT FAIL: {type(error).__name__}")
        return 3
    report = evaluate(observed, expected, heads)
    print(
        f"CONNECTED: client_tls_verified={observed.client_tls}, "
        f"server_pg_stat_ssl={observed.server_tls}, schema state={report.state}"
    )
    print(f"  tables: {', '.join(sorted(observed.tables)) or '<none>'}")
    print(
        f"  revision: {', '.join(sorted(observed.revisions)) or '<none>'}; "
        f"expected: {', '.join(sorted(heads))}"
    )
    for problem in report.problems:
        print(f"  - {problem}")
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
