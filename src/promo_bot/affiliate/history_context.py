"""Per-call audited intent: no credentials/URLs in repr, and no implicit retry."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text

from promo_bot.database.history_repository import (
    AffiliateHistoryError,
    AffiliateLinkHistoryRepository,
)
from promo_bot.database.history_storage import (
    HISTORY_SCHEMA_VERSION,
    durable_sqlite_path,
    validate_history_schema_readonly,
)
from promo_bot.database.session import AffiliateShadowDatabase, Database


def history_scope(database: Database) -> str:
    return "shadow" if isinstance(database, AffiliateShadowDatabase) else "runtime"


def history_fingerprint(secret: str, value: str, domain: str) -> str:
    key = hmac.new(secret.encode(), domain.encode("ascii"), hashlib.sha256).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


async def validate_history_storage(database: Database, *, real: bool) -> None:
    expected = durable_sqlite_path(str(database.engine.url)) if real else None
    try:
        if expected is not None:
            # Opening a writable SQLite connection can checkpoint WAL even when
            # the only SQL is SELECT and validation subsequently refuses schema.
            await asyncio.to_thread(validate_history_schema_readonly, expected)
        async with database.session() as session:
            if expected is not None:
                files = (await session.execute(text("PRAGMA database_list"))).all()
                from pathlib import Path

                if not any(
                    row[1] == "main" and Path(row[2]).resolve() == expected for row in files
                ):
                    raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_NOT_DURABLE")
                version = await session.scalar(text("SELECT version_num FROM alembic_version"))
                if version != HISTORY_SCHEMA_VERSION:
                    raise AffiliateHistoryError("AFFILIATE_HISTORY_SCHEMA_REQUIRED")
            tables = set(
                (
                    await session.scalars(text("SELECT name FROM sqlite_master WHERE type='table'"))
                ).all()
            )
            if (
                not {
                    "affiliate_link_generations",
                    "affiliate_link_uses",
                    "affiliate_link_use_links",
                }
                <= tables
            ):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_SCHEMA_REQUIRED")
            if (
                await session.scalar(text("PRAGMA foreign_keys")) != 1
                or (await session.execute(text("PRAGMA foreign_key_check"))).first() is not None
            ):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_FOREIGN_KEY_INVALID")
            if real and (
                await session.scalar(text("PRAGMA journal_mode")) in {"off", "memory"}
                or await session.scalar(text("PRAGMA synchronous")) != 2
                or await session.scalar(text("PRAGMA quick_check")) != "ok"
            ):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_NOT_DURABLE")
    except (AffiliateHistoryError, ValueError):
        raise
    except Exception:
        raise AffiliateHistoryError("AFFILIATE_HISTORY_STORAGE_UNAVAILABLE") from None


@dataclass(repr=False)
class AuditedGenerationCall:
    database: Database
    generation_ids: tuple[str, ...]
    lease_tokens: tuple[str, ...]
    payload: Mapping[str, str]
    clock: Callable[[], datetime]
    started: bool = field(default=False, init=False)
    wire_entered: bool = field(default=False, init=False)

    async def mark_started(self) -> None:
        if not self.started:
            async with self.database.session() as session:
                await AffiliateLinkHistoryRepository(session).start_call(
                    self.generation_ids,
                    now=self.clock(),
                    lease_tokens=self.lease_tokens,
                )
            self.started = True

    async def before_network(self) -> None:
        if self.wire_entered:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_ALREADY_STARTED")
        await self.mark_started()
        self.wire_entered = True


CURRENT_GENERATION_CALL: ContextVar[AuditedGenerationCall | None] = ContextVar(
    "affiliate_generation_call",
    default=None,
)


@contextmanager
def audited_generation_call(call: AuditedGenerationCall) -> Iterator[AuditedGenerationCall]:
    token = CURRENT_GENERATION_CALL.set(call)
    try:
        yield call
    finally:
        CURRENT_GENERATION_CALL.reset(token)
