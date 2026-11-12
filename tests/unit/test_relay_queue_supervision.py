"""Supervision of the durable relay queue's background loops.

An exception that escapes ``RelayProcessor.process`` (for example a SQLite error while claiming,
failing or completing a message) must not end the worker, and an error in a recovery pass must
not end the recovery loop. Durable state stays authoritative: nothing is marked processed and
nothing external is retried by these loops.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import Base
from promo_bot.database.repositories import SourceMessageRepository
from promo_bot.database.session import Database
from promo_bot.domain.enums import SourceMessageState
from promo_bot.observability import configure_logging
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.relay.service import RelayProcessor

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
SECRET_IN_ERROR = "SECRET-IN-EXCEPTION"
# TelegramRelayConfig(retry_initial_seconds=2): the loops back off 2s, 4s, 8s... plus <=20% jitter.
CONFIG = TelegramRelayConfig(retry_initial_seconds=2, retry_max_seconds=300)


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """configure_logging() replaces the global handlers; put them back after the test.

    The test itself calls configure_logging so that the handler binds the stream that capsys
    is capturing during the test body (a handler built in a fixture holds a closed stream).
    """

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def incoming(message_id: int) -> IncomingMessage:
    return IncomingMessage("telegram", message_id, "channel-1", NOW, f"fixture {message_id}", ())


class RecordingSleep:
    """Records requested delays but only yields briefly, so loops cannot starve the test."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0.005)


class ScriptedProcessor:
    """Fake processor: raises a database-like error for scripted ids, records every call."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.always_fail: set[int] = set()
        self.fail_once: set[int] = set()

    async def process(self, internal_id: int) -> None:
        self.calls.append(internal_id)
        if internal_id in self.always_fail:
            raise RuntimeError(f"database is locked {SECRET_IN_ERROR}")
        if internal_id in self.fail_once:
            self.fail_once.discard(internal_id)
            raise RuntimeError(f"database is locked {SECRET_IN_ERROR}")


class CountingProcessor:
    """Delegates to the real RelayProcessor so durable state changes are real."""

    def __init__(self, inner: RelayProcessor) -> None:
        self.inner = inner
        self.calls: list[int] = []

    async def process(self, internal_id: int) -> None:
        self.calls.append(internal_id)
        await self.inner.process(internal_id)


class BlockingProcessor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[int] = []

    async def process(self, internal_id: int) -> None:
        self.calls.append(internal_id)
        self.started.set()
        await self.release.wait()


async def wait_until(condition: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition was not reached before the timeout")
        await asyncio.sleep(0.005)


@asynccontextmanager
async def relay_under_test(
    tmp_path: Path,
    processor: object | None = None,
    *,
    sleep: RecordingSleep | None = None,
) -> AsyncIterator[tuple[Database, DurableRelayQueue]]:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'relay.sqlite3').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    relay = DurableRelayQueue(
        database,
        CONFIG,
        processor=processor,  # type: ignore[arg-type]
        clock=lambda: NOW,
        sleep=sleep,
    )
    try:
        yield database, relay
    finally:
        await relay.stop()
        await database.dispose()


async def persist_all(relay: DurableRelayQueue, count: int) -> list[int]:
    return [(await relay.persist(incoming(number))).internal_id for number in range(1, count + 1)]


def database_locked() -> OperationalError:
    return OperationalError(
        "SELECT 1", {}, sqlite3.OperationalError(f"database is locked {SECRET_IN_ERROR}")
    )


@pytest.mark.asyncio
async def test_worker_keeps_processing_after_an_unexpected_processor_error(tmp_path: Path) -> None:
    # Breaks if: the worker loop lets the exception end the task (later messages starve and
    # join() never returns) or skips task_done() on the failure path.
    processor = ScriptedProcessor()
    async with relay_under_test(tmp_path, processor, sleep=RecordingSleep()) as (_, relay):
        first, second = await persist_all(relay, 2)
        processor.always_fail.add(first)
        await relay.start(worker_count=1, recover=False)

        await asyncio.wait_for(relay.join(), timeout=3)
        third = (await relay.persist(incoming(3))).internal_id
        await asyncio.wait_for(relay.join(), timeout=3)

        assert processor.calls == [first, second, third]


@pytest.mark.asyncio
async def test_failed_item_is_released_so_durable_recovery_can_requeue_it(tmp_path: Path) -> None:
    # Breaks if: the failure path leaves the id in the in-memory "already enqueued" set, which
    # makes every later recovery pass skip a message that is still pending in SQLite.
    processor = ScriptedProcessor()
    async with relay_under_test(tmp_path, processor, sleep=RecordingSleep()) as (_, relay):
        (message_id,) = await persist_all(relay, 1)
        processor.fail_once.add(message_id)
        await relay.start(worker_count=1, recover=False)
        await wait_until(lambda: processor.calls == [message_id])
        await asyncio.wait_for(relay.join(), timeout=3)

        await relay.recover_once()
        await asyncio.wait_for(relay.join(), timeout=3)

        assert processor.calls == [message_id, message_id]


@pytest.mark.asyncio
async def test_every_worker_hitting_an_error_still_finishes_the_work_exactly_once(
    tmp_path: Path,
) -> None:
    # Breaks if: each failing worker dies (three failures would leave zero workers and the
    # remaining items stuck) or if a failure causes an item to be processed twice.
    processor = ScriptedProcessor()
    async with relay_under_test(tmp_path, processor, sleep=RecordingSleep()) as (_, relay):
        ids = await persist_all(relay, 6)
        processor.always_fail.update(ids[:3])
        await relay.start(worker_count=3, recover=False)

        await asyncio.wait_for(relay.join(), timeout=3)

        assert sorted(processor.calls) == ids
        assert len(processor.calls) == 6


@pytest.mark.asyncio
async def test_consecutive_failures_back_off_and_reset_after_a_success(tmp_path: Path) -> None:
    # Breaks if: a failing database is hammered without delay, delays never grow, or they do
    # not reset once a message processes normally again.
    processor = ScriptedProcessor()
    sleep = RecordingSleep()
    async with relay_under_test(tmp_path, processor, sleep=sleep) as (_, relay):
        ids = await persist_all(relay, 4)
        processor.always_fail.update({ids[0], ids[1], ids[3]})
        await relay.start(worker_count=1, recover=False)

        await asyncio.wait_for(relay.join(), timeout=3)

        first, second, after_success = sleep.delays
        assert 2.0 <= first <= 2.4
        assert 4.0 <= second <= 4.8
        assert 2.0 <= after_success <= 2.4


@pytest.mark.asyncio
async def test_failure_backoff_grows_but_stays_within_the_configured_maximum(
    tmp_path: Path,
) -> None:
    # Breaks if: delays are unbounded (a long outage would stall the worker for hours) or stop
    # growing (a long outage would still be probed every few seconds).
    processor = ScriptedProcessor()
    sleep = RecordingSleep()
    async with relay_under_test(tmp_path, processor, sleep=sleep) as (_, relay):
        ids = await persist_all(relay, 9)
        processor.always_fail.update(ids)
        await relay.start(worker_count=1, recover=False)

        await asyncio.wait_for(relay.join(), timeout=5)

        assert len(sleep.delays) == 9
        assert sleep.delays == sorted(sleep.delays)
        assert sleep.delays[-1] >= 16.0
        assert max(sleep.delays) <= CONFIG.retry_max_seconds


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_logging")
async def test_unexpected_worker_error_is_logged_with_stable_code_and_no_exception_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Breaks if: the loop logs str(exc)/a traceback (driver errors may embed bound SQL values
    # such as affiliate links) or logs nothing, which would hide a degraded relay.
    configure_logging("INFO")
    processor = ScriptedProcessor()
    async with relay_under_test(tmp_path, processor, sleep=RecordingSleep()) as (_, relay):
        (message_id,) = await persist_all(relay, 1)
        processor.always_fail.add(message_id)
        await relay.start(worker_count=1, recover=False)
        await asyncio.wait_for(relay.join(), timeout=3)

    error_output = capsys.readouterr().err
    assert '"error_code":"RELAY_WORKER_UNEXPECTED_ERROR"' in error_output
    assert '"error_summary":"RuntimeError"' in error_output
    assert SECRET_IN_ERROR not in error_output
    assert "Traceback" not in error_output


@pytest.mark.asyncio
async def test_cancelled_worker_releases_its_item_for_durable_recovery(tmp_path: Path) -> None:
    # Regression guard (behavior that already worked): cancellation mid-process keeps task
    # accounting correct and does not leave the id blocked, so a restart still recovers it.
    processor = BlockingProcessor()
    async with relay_under_test(tmp_path, processor, sleep=RecordingSleep()) as (_, relay):
        (message_id,) = await persist_all(relay, 1)
        await relay.start(worker_count=1, recover=False)
        await asyncio.wait_for(processor.started.wait(), timeout=3)

        await asyncio.wait_for(relay.stop(), timeout=3)
        await asyncio.wait_for(relay.join(), timeout=3)
        processor.release.set()
        await relay.start(worker_count=1, recover=True)
        await wait_until(lambda: processor.calls == [message_id, message_id])


@pytest.mark.asyncio
async def test_stop_cancels_idle_workers_and_the_recovery_loop(tmp_path: Path) -> None:
    # Regression guard: supervision must not make shutdown wait for loops that sleep or idle.
    async with relay_under_test(tmp_path, ScriptedProcessor(), sleep=RecordingSleep()) as (
        _,
        relay,
    ):
        await relay.start(worker_count=2, recover=True)

        await asyncio.wait_for(relay.stop(), timeout=3)

        leftovers = [t for t in asyncio.all_tasks() if t.get_name().startswith("relay-")]
        assert leftovers == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_logging")
async def test_recovery_loop_survives_transient_database_errors_and_recovers_the_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Breaks if: the first failing recovery pass ends the loop (pending messages would then
    # wait for a process restart), retries without backoff, or reports success falsely.
    configure_logging("INFO")
    sleep = RecordingSleep()
    async with relay_under_test(tmp_path, sleep=sleep) as (database, relay):
        counting = CountingProcessor(RelayProcessor(database, CONFIG, clock=lambda: NOW))
        relay.processor = counting
        await relay.start(worker_count=1, recover=True)
        original = relay.recover_once
        attempts = {"count": 0}

        async def flaky_recover_once() -> int:
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise database_locked()
            return await original()

        monkeypatch.setattr(relay, "recover_once", flaky_recover_once)
        message_id = (await relay.persist_without_enqueue(incoming(1))).internal_id

        await wait_until(lambda: counting.calls == [message_id])
        await wait_until(lambda: len(sleep.delays) >= 5)

        assert attempts["count"] >= 3
        assert counting.calls == [message_id]
        async with database.session() as session:
            stored = await SourceMessageRepository(session).get(message_id)
        assert stored is not None
        assert stored.processing_status == SourceMessageState.COMPLETED.value
        interval, after_first, after_second, after_success = sleep.delays[:4]
        assert interval == 2.0
        assert 2.0 <= after_first <= 2.4
        assert 4.0 <= after_second <= 4.8
        assert after_success == 2.0

    error_output = capsys.readouterr().err
    assert '"error_code":"RELAY_RECOVERY_UNEXPECTED_ERROR"' in error_output
    assert SECRET_IN_ERROR not in error_output


@pytest.mark.asyncio
async def test_recovery_still_runs_when_candidate_backfill_keeps_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Breaks if: one failing step in the pass (the Shopee backfill) prevents the independent
    # message recovery step from ever running.
    async with relay_under_test(tmp_path, sleep=RecordingSleep()) as (database, relay):
        counting = CountingProcessor(RelayProcessor(database, CONFIG, clock=lambda: NOW))
        relay.processor = counting
        await relay.start(worker_count=1, recover=True)

        async def failing_backfill() -> int:
            raise database_locked()

        monkeypatch.setattr(relay, "backfill_affiliate_candidates_once", failing_backfill)
        message_id = (await relay.persist_without_enqueue(incoming(1))).internal_id

        await wait_until(lambda: counting.calls == [message_id])


@pytest.mark.asyncio
async def test_persistent_recovery_failures_back_off_instead_of_spinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Breaks if: a persistent outage is probed at the base interval forever (no growth) or the
    # delay is unbounded.
    sleep = RecordingSleep()
    async with relay_under_test(tmp_path, ScriptedProcessor(), sleep=sleep) as (_, relay):
        await relay.start(worker_count=1, recover=True)

        async def always_failing() -> int:
            raise database_locked()

        monkeypatch.setattr(relay, "recover_once", always_failing)
        await wait_until(lambda: len(sleep.delays) >= 9)

        delays = sleep.delays[:9]
        assert delays[0] == 2.0
        assert delays[1:] == sorted(delays[1:])
        assert delays[-1] >= 16.0
        assert max(delays) <= CONFIG.retry_max_seconds
