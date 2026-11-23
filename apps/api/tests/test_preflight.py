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
        "tls": True,
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
    assert evaluate(_observed(tls=False), EXPECT, HEADS).problems


def test_expected_tables_match_the_models() -> None:
    from promo_api.db.models import Base

    assert EXPECTED_TABLES == frozenset(Base.metadata.tables)
