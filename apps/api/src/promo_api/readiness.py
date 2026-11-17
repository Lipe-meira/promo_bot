"""Readiness: the database answers and is at the schema revision this build was written for.

The expected revision is never a constant: it is read from the versioned Alembic scripts that ship
with the application, so adding a migration moves the expectation with it. Nothing here runs a
migration; applying them is a separate, deliberate step (`alembic upgrade head`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("promo_api.readiness")

# apps/api/migrations, next to the package source (the same directory alembic.ini points to).
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


# `/readyz` is public and queries the database. A free Neon compute suspends after five idle
# minutes and the plan allows 100 CU-hours a month, so an uncached probe lets any caller who knows
# the URL keep the compute awake. A success is remembered long enough that a flood wakes it for
# about 14% of the time (a 0.25 CU compute: roughly 26 CU-hours a month); a failure only briefly,
# so an operator who has just applied a migration sees the 200 within seconds.
SUCCESS_TTL_SECONDS = 1_800.0
FAILURE_TTL_SECONDS = 5.0


class CachedReadiness:
    """Remembers the last readiness answer and lets concurrent callers share one probe.

    The cache is per process: a new deployment starts empty, so the first call after a deploy is
    always a real probe. A schema change made while the process runs (a rollback) can therefore be
    reported late, by at most `SUCCESS_TTL_SECONDS`.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        success_ttl: float = SUCCESS_TTL_SECONDS,
        failure_ttl: float = FAILURE_TTL_SECONDS,
    ) -> None:
        self._clock = clock
        self._success_ttl = success_ttl
        self._failure_ttl = failure_ttl
        self._lock = asyncio.Lock()
        self._value = False
        self._valid_until = float("-inf")

    async def check(self, probe: Callable[[], Awaitable[bool]]) -> bool:
        if self._clock() < self._valid_until:
            return self._value
        async with self._lock:
            if self._clock() < self._valid_until:
                return self._value
            value = await probe()
            ttl = self._success_ttl if value else self._failure_ttl
            self._value = value
            self._valid_until = self._clock() + ttl
            return value


def shipped_heads(migrations_dir: Path) -> frozenset[str]:
    """Every head of the versioned migrations. More than one is legal (parallel branches)."""

    try:
        heads = frozenset(ScriptDirectory(str(migrations_dir)).get_heads())
    except Exception as error:  # a missing directory or a broken revision graph
        raise RuntimeError(
            f"The versioned migrations could not be read from {migrations_dir}: "
            f"{type(error).__name__}"
        ) from None
    if not heads:
        raise RuntimeError(f"No versioned migrations were found in {migrations_dir}.")
    return heads


async def database_is_ready(db: AsyncSession, expected_heads: frozenset[str]) -> bool:
    """True only if `alembic_version` records exactly the expected head revision(s).

    Anything else is "not ready": an unreachable database, a missing or empty version table, an
    unknown, older or newer revision, or extra rows. The reason is logged (never returned): the
    response must not describe the database to an unauthenticated caller.
    """

    try:
        recorded = frozenset(await db.scalars(text("SELECT version_num FROM alembic_version")))
    except Exception as error:
        _log.warning("readiness: the version table could not be read (%s)", type(error).__name__)
        return False
    if recorded != expected_heads:
        _log.warning(
            "readiness: schema revision %s does not match the expected %s",
            sorted(recorded) or "<none>",
            sorted(expected_heads),
        )
        return False
    return True
