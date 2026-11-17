"""The database URL contract for managed PostgreSQL (Neon) through asyncpg.

Connection strings copied from a provider carry libpq parameters (`sslmode`, `channel_binding`)
that asyncpg does not understand: SQLAlchemy would forward them as keyword arguments and the first
connection would fail with `TypeError`. Quietly dropping them is not enough either, because
asyncpg does not verify the server certificate unless it is given an explicit `ssl` argument.
These tests pin the behaviour of `prepare_database`; no network or database is used.
"""

from __future__ import annotations

import ssl

import pytest

from promo_api.db.engine import prepare_database

NEON_DIRECT = (
    "postgresql+asyncpg://neondb_owner:synthetic-pw@ep-synthetic-1234.us-east-2.aws.neon.tech"
    "/neondb?sslmode=require&channel_binding=require"
)
NEON_POOLED = NEON_DIRECT.replace("ep-synthetic-1234.", "ep-synthetic-1234-pooler.")
LOCAL = "postgresql+asyncpg://postgres:synthetic-pw@127.0.0.1:5432/promo_api_test_x"


def _verified(context: object) -> bool:
    return (
        isinstance(context, ssl.SSLContext)
        and context.verify_mode == ssl.CERT_REQUIRED
        and context.check_hostname
    )


def test_libpq_parameters_never_reach_asyncpg() -> None:
    prepared = prepare_database(NEON_DIRECT)

    assert dict(prepared.url.query) == {}


def test_a_provider_url_keeps_its_credentials_and_target() -> None:
    url = prepare_database(NEON_DIRECT).url

    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "ep-synthetic-1234.us-east-2.aws.neon.tech"
    assert url.username == "neondb_owner"
    assert url.password == "synthetic-pw"
    assert url.database == "neondb"


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_remote_tls_is_always_verified(mode: str) -> None:
    # libpq's `require` means "encrypt, do not verify"; this API never accepts an unverified peer.
    prepared = prepare_database(NEON_DIRECT.replace("sslmode=require", f"sslmode={mode}"))

    assert _verified(prepared.connect_args["ssl"])


def test_a_remote_host_without_sslmode_still_uses_verified_tls() -> None:
    prepared = prepare_database("postgresql+asyncpg://u:p@db.example.test:5432/app")

    assert _verified(prepared.connect_args["ssl"])


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer"])
def test_a_remote_host_cannot_opt_out_of_tls(mode: str) -> None:
    with pytest.raises(ValueError, match="TLS"):
        prepare_database(f"postgresql+asyncpg://u:p@db.example.test/app?sslmode={mode}")


def test_plaintext_is_only_for_loopback() -> None:
    prepared = prepare_database(LOCAL)

    assert "ssl" not in prepared.connect_args


def test_an_unknown_sslmode_is_refused() -> None:
    with pytest.raises(ValueError, match="sslmode"):
        prepare_database("postgresql+asyncpg://u:p@db.example.test/app?sslmode=bogus")


@pytest.mark.parametrize("parameter", ["options=endpoint%3Dsecret-value", "ssl=disable", "x=1"])
def test_other_query_parameters_are_refused_by_name_only(parameter: str) -> None:
    name, _, value = parameter.partition("=")

    with pytest.raises(ValueError) as error:
        prepare_database(f"postgresql+asyncpg://u:p@db.example.test/app?{parameter}")

    assert name in str(error.value)
    assert value not in str(error.value)


def test_errors_never_echo_the_password_or_the_host() -> None:
    with pytest.raises(ValueError) as error:
        prepare_database("postgresql+asyncpg://u:hunter2@db.example.test/app?sslmode=disable")

    assert "hunter2" not in str(error.value)
    assert "db.example.test" not in str(error.value)


def test_a_malformed_url_is_refused_without_echoing_it() -> None:
    with pytest.raises(ValueError) as error:
        prepare_database("hunter2-is-not-a-url")

    assert "hunter2" not in str(error.value)


def test_a_pooled_host_is_detected_and_asyncpg_prepared_statements_are_disabled() -> None:
    # The pooler runs PgBouncer in transaction mode: a server connection is not tied to a client.
    prepared = prepare_database(NEON_POOLED)

    assert prepared.pooled is True
    assert prepared.connect_args["statement_cache_size"] == 0
    assert prepared.connect_args["prepared_statement_cache_size"] == 0
    name_func = prepared.connect_args["prepared_statement_name_func"]
    assert name_func() != name_func()  # never reuse a statement name across server connections


def test_a_direct_host_keeps_the_default_statement_cache() -> None:
    prepared = prepare_database(NEON_DIRECT)

    assert prepared.pooled is False
    assert "statement_cache_size" not in prepared.connect_args


def test_the_connect_timeout_is_configurable_and_bounded_by_default() -> None:
    assert prepare_database(LOCAL).connect_args["timeout"] == 15.0
    assert prepare_database(LOCAL, connect_timeout=3.0).connect_args["timeout"] == 3.0


def test_a_query_that_hangs_cannot_hold_a_connection_forever() -> None:
    assert prepare_database(LOCAL).connect_args["command_timeout"] == 30.0
