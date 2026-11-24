"""Read-only preflight for the managed database: target identity and schema state, offline."""

from __future__ import annotations

import pytest

from promo_api.preflight import (
    EXPECTED_TABLES,
    Expectation,
    Observed,
    check_target,
    evaluate,
)

URL = "postgresql+asyncpg://app_owner:s3cr3t-pw@ep-test-123.us-east-2.aws.neon.tech/neondb?sslmode=require"
EXPECT = Expectation(
    host="ep-test-123.us-east-2.aws.neon.tech", database="neondb", role="app_owner", schema="public"
)
HEADS = frozenset({"0001"})


def test_target_matching_the_expectation_has_no_problems() -> None:
    assert check_target(URL, EXPECT) == []


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("ep-test-123", "ep-test-123-pooler"),  # pooler
        URL.replace("ep-test-123", "ep-other-999"),  # wrong host
        URL.replace("/neondb", "/otherdb"),  # wrong database
        URL.replace("app_owner:", "someone:"),  # wrong role
        URL.replace("postgresql+asyncpg", "postgresql+psycopg"),  # wrong driver
    ],
)
def test_target_mismatch_is_reported_without_echoing_the_url(url: str) -> None:
    problems = check_target(url, EXPECT)
    assert problems
    text = " ".join(problems)
    for secret in ("s3cr3t-pw", "ep-other-999", "otherdb", "someone"):
        assert secret not in text


def test_malformed_url_is_a_problem_not_a_traceback() -> None:
    assert check_target("not a url", EXPECT)


def _observed(**kw: object) -> Observed:
    base: dict[str, object] = {
        "database": "neondb",
        "role": "app_owner",
        "schema": "public",
        "client_tls": True,
        "server_tls": False,  # behind a TLS-terminating proxy (Neon) pg_stat_ssl reports false
        "tables": EXPECTED_TABLES | {"alembic_version"},
        "revisions": HEADS,
    }
    base.update(kw)
    return Observed(**base)  # type: ignore[arg-type]


def test_migrated_database_is_ready() -> None:
    assert evaluate(_observed(), EXPECT, HEADS).state == "migrated"


def test_empty_schema_is_ready_for_the_first_migration() -> None:
    report = evaluate(_observed(tables=frozenset(), revisions=frozenset()), EXPECT, HEADS)
    assert report.state == "empty"
    assert report.problems == []


def test_unexpected_tables_without_a_version_table_block_the_migration() -> None:
    report = evaluate(_observed(tables=frozenset({"stuff"}), revisions=frozenset()), EXPECT, HEADS)
    assert report.state == "unexpected"


def test_older_revision_needs_an_upgrade() -> None:
    report = evaluate(_observed(revisions=frozenset({"0000"})), EXPECT, HEADS)
    assert report.state == "outdated"


def test_identity_and_tls_mismatches_are_problems() -> None:
    assert evaluate(_observed(database="x"), EXPECT, HEADS).problems
    assert evaluate(_observed(role="x"), EXPECT, HEADS).problems
    assert evaluate(_observed(schema="x"), EXPECT, HEADS).problems
    assert evaluate(_observed(client_tls=False), EXPECT, HEADS).problems


def test_server_side_ssl_state_alone_is_not_a_failure() -> None:
    """pg_stat_ssl describes the proxy-to-compute hop; the client-verified link is what matters."""

    report = evaluate(_observed(client_tls=True, server_tls=False), EXPECT, HEADS)
    assert report.problems == []
    assert report.state == "migrated"


def test_server_side_ssl_cannot_stand_in_for_the_client_check() -> None:
    report = evaluate(_observed(client_tls=False, server_tls=True), EXPECT, HEADS)
    assert any("not encrypted" in problem for problem in report.problems)


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer"])
def test_remote_target_without_tls_is_rejected_before_connecting(mode: str) -> None:
    problems = check_target(URL.replace("sslmode=require", f"sslmode={mode}"), EXPECT)
    assert any("TLS" in problem for problem in problems)


def test_remote_target_without_any_sslmode_still_verifies_tls() -> None:
    assert check_target(URL.replace("?sslmode=require", ""), EXPECT) == []


def test_client_tls_requires_a_verified_context() -> None:
    import ssl

    from promo_api.preflight import client_link_is_verified

    verified = ssl.create_default_context()
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    assert client_link_is_verified(verified, encrypted=True)
    assert not client_link_is_verified(unverified, encrypted=True)
    assert not client_link_is_verified(verified, encrypted=False)
    assert not client_link_is_verified(None, encrypted=True)


def test_expected_tables_match_the_models() -> None:
    from promo_api.db.models import Base

    assert EXPECTED_TABLES == frozenset(Base.metadata.tables)
