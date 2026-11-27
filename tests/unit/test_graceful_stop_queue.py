"""Ordered stop of the durable queue and what a restart finds. Real SQLite (temporary file) and
the real claim/complete flow; only the work done for a link is replaced by a controllable wait."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import Base, SourceMessageModel
from promo_bot.database.session import Database
from promo_bot.relay.models import ExtractedLink, IncomingMessage
from promo_bot.relay.parser import extract_links
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.relay.service import RelayProcessor

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
CONFIG = TelegramRelayConfig(retry_initial_seconds=2, retry_max_seconds=300)


class Work:
    """Stands for the work of one message: records it, and waits while `gate` is closed."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.running = asyncio.Event()

    async def __call__(self, source_message_id: int, link: ExtractedLink) -> None:
        name = link.url.rsplit("/", 1)[-1]
        self.started.append(name)
        self.running.set()
        await self.gate.wait()
        self.finished.append(name)


def incoming(message_id: int) -> IncomingMessage:
    text = f"offer https://example.test/p/{message_id}"
    return IncomingMessage("telegram", message_id, "channel-1", NOW, text, extract_links(text))


@asynccontextmanager
async def database(path: Path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    before = asyncio.all_tasks()
    try:
        yield db
    finally:
        # A failed assertion must fail, not hang: end what the test left running first, or the
        # database connections keep the interpreter alive.
        leftovers = asyncio.all_tasks() - before
        for task in leftovers:
            task.cancel()
        await asyncio.gather(*leftovers, return_exceptions=True)
        await db.dispose()


def relay(db: Database, work: Work, *, now: datetime = NOW) -> DurableRelayQueue:
    processor = RelayProcessor(db, CONFIG, clock=lambda: now)
    processor._process_link = work  # type: ignore[method-assign]
    return DurableRelayQueue(db, CONFIG, processor=processor, clock=lambda: now)


async def states(db: Database) -> dict[str, tuple[str, int, bool]]:
    async with db.session() as session:
        rows = (await session.execute(select(SourceMessageModel))).scalars().all()
        return {
            row.message_id: (
                row.processing_status,
                row.attempt_count,
                row.processing_lease_until is not None,
            )
            for row in rows
        }


async def until(condition: Any, seconds: float = 5) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.005)


async def test_the_message_in_progress_finishes_and_the_rest_waits_for_the_restart(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work = Work()
        work.gate.clear()
        queue = relay(db, work)
        await queue.start()
        for number in (1, 2, 3):
            await queue.persist(incoming(number))
        await asyncio.wait_for(work.running.wait(), 5)

        stopping = asyncio.create_task(queue.shutdown(timeout_seconds=5))
        await asyncio.sleep(0.05)
        assert not stopping.done()  # it waits for the message in progress
        work.gate.set()
        report = await asyncio.wait_for(stopping, 10)

        assert (work.started, work.finished) == (["1"], ["1"])  # nothing new was started
        assert (report.released, report.pending) == (0, 2)
        assert await states(db) == {
            "1": ("COMPLETED", 1, False),
            "2": ("RECEIVED", 0, False),
            "3": ("RECEIVED", 0, False),
        }

        restarted = Work()
        again = relay(db, restarted)
        await again.start()
        await until(lambda: len(restarted.finished) == 2)
        await again.shutdown(timeout_seconds=5)

        assert restarted.started == ["2", "3"]  # in order, and message 1 not again
        assert {state for state, _, _ in (await states(db)).values()} == {"COMPLETED"}


async def test_work_that_outlasts_the_deadline_is_given_back_without_spending_an_attempt(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work = Work()
        work.gate.clear()  # never finishes
        queue = relay(db, work)
        await queue.start()
        await queue.persist(incoming(1))
        await asyncio.wait_for(work.running.wait(), 5)

        report = await queue.shutdown(timeout_seconds=0.1)

        assert (report.released, report.pending) == (1, 1)
        assert work.finished == []
        assert await states(db) == {"1": ("FAILED_RETRYABLE", 0, False)}

        # Available at once: the restart does not wait for any lease to expire.
        restarted = Work()
        again = relay(db, restarted)
        await again.start()
        await until(lambda: restarted.finished == ["1"])
        await again.shutdown(timeout_seconds=5)
        assert await states(db) == {"1": ("COMPLETED", 1, False)}


async def test_repeated_stops_never_exhaust_the_attempts_of_a_message(tmp_path: Path) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        for cycle in range(CONFIG.processing_max_attempts + 2):
            work = Work()
            work.gate.clear()
            queue = relay(db, work)
            await queue.start()
            if cycle == 0:
                await queue.persist(incoming(1))
            await asyncio.wait_for(work.running.wait(), 5)
            await queue.shutdown(timeout_seconds=0.05)

        assert await states(db) == {"1": ("FAILED_RETRYABLE", 0, False)}


async def test_a_message_that_had_failed_before_keeps_its_earlier_attempts(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work = Work()
        queue = relay(db, work)
        await queue.persist_without_enqueue(incoming(1))
        async with db.session() as session:
            row = (await session.execute(select(SourceMessageModel))).scalar_one()
            row.processing_status = "FAILED_RETRYABLE"
            row.attempt_count = 2
        work.gate.clear()
        await queue.start()
        await asyncio.wait_for(work.running.wait(), 5)

        await queue.shutdown(timeout_seconds=0.05)

        assert await states(db) == {"1": ("FAILED_RETRYABLE", 2, False)}


async def test_nothing_is_picked_up_while_stopping(tmp_path: Path) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work = Work()
        queue = relay(db, work)
        await queue.start()
        await queue.shutdown(timeout_seconds=5)

        persisted = await queue.persist(incoming(1))  # arrives too late
        await asyncio.sleep(0.05)

        assert persisted.queued is False
        assert work.started == []
        assert await states(db) == {"1": ("RECEIVED", 0, False)}


async def test_a_queue_that_does_not_recover_keeps_its_claims_as_before(tmp_path: Path) -> None:
    # Bounded shadow runs start with recover=False: an interrupted item there stays claimed, so
    # that nothing with external effects is silently run again.
    async with database(tmp_path / "relay.sqlite3") as db:
        work = Work()
        work.gate.clear()
        queue = relay(db, work)
        await queue.start(recover=False)
        await queue.persist(incoming(1))
        await asyncio.wait_for(work.running.wait(), 5)

        report = await queue.shutdown(timeout_seconds=0.05)

        assert report.released == 0
        assert await states(db) == {"1": ("PROCESSING", 1, True)}


async def test_after_a_forced_kill_the_message_still_returns_when_its_lease_expires(
    tmp_path: Path,
) -> None:
    # What a kill leaves behind (claimed, never finished) is not changed by this feature.
    async with database(tmp_path / "relay.sqlite3") as db:
        work = Work()
        work.gate.clear()
        queue = relay(db, work)
        await queue.start()
        await queue.persist(incoming(1))
        await asyncio.wait_for(work.running.wait(), 5)
        await queue.stop()  # abrupt: no release

        assert await states(db) == {"1": ("PROCESSING", 1, True)}
        later = NOW + timedelta(minutes=CONFIG.processing_stale_after_minutes + 1)
        restarted = Work()
        again = relay(db, restarted, now=later)
        await again.start()
        await until(lambda: restarted.finished == ["1"])
        await again.shutdown(timeout_seconds=5)
        assert await states(db) == {"1": ("COMPLETED", 2, False)}
