"""`promo-api-admin` against an ephemeral PostgreSQL: the address comes from standard input and
the tool never prints it back unless explicitly asked to."""

from __future__ import annotations

import asyncio
import io

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from promo_api.admin import entrypoint

pytestmark = pytest.mark.postgres

EMAIL = "someone.private@example.test"


async def _run(monkeypatch: pytest.MonkeyPatch, url: str, *argv: str, stdin: str = "") -> int:
    monkeypatch.setenv("PROMO_API_DATABASE_URL", url)
    # The CLI owns its event loop (`asyncio.run`): run it away from the test's loop.
    return await asyncio.to_thread(entrypoint, list(argv), stdin=io.StringIO(stdin))


async def _invitations(engine: AsyncEngine) -> list[tuple[str, bool]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT email, revoked_at IS NOT NULL FROM invitations")
        )
        return [(email, revoked) for email, revoked in rows]


async def test_invite_reads_the_address_from_standard_input(
    migrated: str,
    clean: None,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await _run(monkeypatch, migrated, "invite", stdin=f"  {EMAIL.upper()}  \n") == 0

    assert await _invitations(engine) == [(EMAIL, False)]
    captured = capsys.readouterr()
    assert captured.out.strip() == "invited"
    assert EMAIL not in captured.out + captured.err


async def test_revoke_reads_the_address_from_standard_input(
    migrated: str,
    clean: None,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(monkeypatch, migrated, "invite", stdin=EMAIL)
    capsys.readouterr()

    assert await _run(monkeypatch, migrated, "revoke", stdin=EMAIL) == 0

    assert await _invitations(engine) == [(EMAIL, True)]
    captured = capsys.readouterr()
    assert captured.out.strip() == "revoked"
    assert EMAIL not in captured.out + captured.err


async def test_revoking_an_unknown_address_reports_it_without_the_address(
    migrated: str,
    clean: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await _run(monkeypatch, migrated, "revoke", stdin=EMAIL) == 1

    captured = capsys.readouterr()
    assert "no such invitation" in captured.out
    assert EMAIL not in captured.out + captured.err


async def test_list_masks_addresses_unless_revealed(
    migrated: str,
    clean: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(monkeypatch, migrated, "invite", stdin=EMAIL)
    capsys.readouterr()

    assert await _run(monkeypatch, migrated, "list") == 0
    masked = capsys.readouterr().out
    assert masked.strip() == "pending\ts***@example.test"

    assert await _run(monkeypatch, migrated, "list", "--reveal") == 0
    assert capsys.readouterr().out.strip() == f"pending\t{EMAIL}"
