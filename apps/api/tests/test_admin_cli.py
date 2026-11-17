"""`promo-api-admin` input rules that hold without a database.

E-mail addresses are personal data: they must not travel in process arguments (visible to every
local user in the process list, kept in shell history and CI logs) nor be echoed back by errors.
The address is read from standard input instead.
"""

from __future__ import annotations

import io

import pytest

from promo_api.admin import entrypoint, mask_email

EMAIL = "someone.private@example.test"
UNREACHABLE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/promo_api_test_unreachable"


@pytest.fixture(autouse=True)
def database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMO_API_DATABASE_URL", UNREACHABLE)


@pytest.mark.parametrize("command", ["invite", "revoke"])
def test_an_address_passed_as_an_argument_is_refused_without_echoing_it(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        entrypoint([command, EMAIL], stdin=io.StringIO(""))

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert EMAIL not in captured.out + captured.err
    assert "standard input" in captured.err


@pytest.mark.parametrize("command", ["invite", "revoke"])
def test_an_empty_standard_input_is_an_error(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entrypoint([command], stdin=io.StringIO("\n")) == 2

    assert "e-mail" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["invite", "revoke"])
def test_an_invalid_address_is_refused_without_echoing_it(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entrypoint([command], stdin=io.StringIO("not an address with spaces\n")) == 2

    captured = capsys.readouterr()
    assert "not an address" not in captured.out + captured.err


def test_only_the_first_line_of_standard_input_is_used() -> None:
    # Anything after the first line is ignored, never executed or stored.
    assert entrypoint(["invite"], stdin=io.StringIO("bad\nsecond-line@example.test\n")) == 2


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("someone.private@example.test", "s***@example.test"),
        ("a@example.test", "a***@example.test"),
        ("UPPER@Example.Test", "U***@Example.Test"),
    ],
)
def test_addresses_are_masked_for_listing(email: str, masked: str) -> None:
    assert mask_email(email) == masked


def test_a_missing_database_url_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PROMO_API_DATABASE_URL")

    assert entrypoint(["invite"], stdin=io.StringIO(EMAIL + "\n")) == 2

    assert EMAIL not in capsys.readouterr().err


def test_an_unsafe_database_url_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "PROMO_API_DATABASE_URL",
        "postgresql+asyncpg://u:hunter2@db.example.test/app?sslmode=disable",
    )

    assert entrypoint(["list"], stdin=io.StringIO("")) == 2

    captured = capsys.readouterr()
    assert "hunter2" not in captured.out + captured.err
    assert "db.example.test" not in captured.out + captured.err
