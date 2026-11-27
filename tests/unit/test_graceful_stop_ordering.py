"""What happens at the very moment a stop is asked for: nothing new is started, writes already
under way finish, and the channel checkpoint never passes a message that was not recorded.

Real monitor, queue and SQLite (temporary file); fake Telegram; no network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from promo_bot.affiliate.aliexpress_shadow_listener import ShadowRunController, ShadowRunLimits
from promo_bot.database.repositories import TelegramCheckpointRepository
from promo_bot.shutdown import StopRequest
from tests.unit.test_graceful_stop_monitor import (
    CHAT_ID,
    Event,
    Message,
    Telegram,
    monitor,
    real_clock_relay,
)
from tests.unit.test_graceful_stop_queue import Work, database, states, until


class HeldReads(Telegram):
    """History read number n returns `reads[n]`; reads listed in `held` wait to be released."""

    def __init__(self, reads: list[list[int]], held: tuple[int, ...] = ()) -> None:
        super().__init__()
        self.reads = reads
        self.count = 0
        self.reading = {number: asyncio.Event() for number in held}
        self.release = {number: asyncio.Event() for number in held}

    async def iter_messages(self, entity: object, limit: int) -> Any:
        number = self.count
        self.count += 1
        if number in self.release:
            self.reading[number].set()
            await self.release[number].wait()
        for message_id in self.reads[number] if number < len(self.reads) else []:
            yield Message(message_id)


class HeldWrite:
    """Holds the recording of one message, as a slow disk would."""

    def __init__(self, queue: Any, message_id: int) -> None:
        self.writing = asyncio.Event()
        self.release = asyncio.Event()
        original = queue.persist

        async def persist(message: Any, **options: Any) -> Any:
            if message.message_id == message_id:
                self.writing.set()
                await self.release.wait()
            return await original(message, **options)

        queue.persist = persist


async def checkpoint(db: Any) -> int | None:
    async with db.session() as session:
        row = await TelegramCheckpointRepository(session).get(str(CHAT_ID))
        return None if row is None else row.last_persisted_message_id


async def run_until_completed(db: Any, telegram: Telegram, *ids: str) -> Work:
    """A second life of the bot on the same database, stopped once these messages completed."""

    work, stop = Work(), StopRequest()
    running = asyncio.create_task(monitor(real_clock_relay(db, work), telegram).run(stop=stop))

    async with asyncio.timeout(15):
        while True:
            rows = await states(db)
            if all(rows.get(name, ("",))[0] == "COMPLETED" for name in ids):
                break
            await asyncio.sleep(0.02)
    stop.request("test")
    await asyncio.wait_for(running, 10)
    return work


# --- nothing new starts, started writes finish ------------------------------------------------


async def test_no_queued_message_starts_after_the_stop_even_while_a_write_is_pending(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work, telegram, stop = Work(), Telegram(), StopRequest()
        work.gate.clear()
        queue = real_clock_relay(db, work)
        write = HeldWrite(queue, 3)
        running = asyncio.create_task(monitor(queue, telegram).run(stop=stop, stop_grace_seconds=5))
        await asyncio.wait_for(telegram.listening.wait(), 5)
        handler = telegram.handler
        await handler(Event(1))
        await handler(Event(2))
        await asyncio.wait_for(work.running.wait(), 5)
        writing = asyncio.create_task(handler(Event(3)))
        await asyncio.wait_for(write.writing.wait(), 5)

        assert queue.queue.qsize() == 1  # message 2 is waiting
        stop.request("test")
        assert queue.queue.qsize() == 0  # set aside at once, before any task got a turn
        await handler(Event(4))  # delivered before the unsubscription: not recorded
        work.gate.set()  # message 1 ends while message 3 is still being written
        await asyncio.sleep(0.2)

        assert work.started == ["1"]
        assert not running.done()  # waiting for the write
        write.release.set()
        await asyncio.wait_for(running, 10)
        await writing

        assert work.started == ["1"]
        assert await states(db) == {
            "1": ("COMPLETED", 1, False),
            "2": ("RECEIVED", 0, False),
            "3": ("RECEIVED", 0, False),
        }
        assert await checkpoint(db) == 3


async def test_a_write_started_by_the_catch_up_finishes_and_the_next_one_does_not_start(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work, stop = Work(), StopRequest()
        telegram = HeldReads([[1, 2, 3]])
        queue = real_clock_relay(db, work)
        write = HeldWrite(queue, 2)
        running = asyncio.create_task(monitor(queue, telegram).run(stop=stop, stop_grace_seconds=5))
        await asyncio.wait_for(write.writing.wait(), 5)

        stop.request("test")
        await asyncio.sleep(0.1)
        assert not running.done()  # the write is not cut
        write.release.set()
        await asyncio.wait_for(running, 10)

        assert sorted(await states(db)) == ["1", "2"]
        assert await checkpoint(db) == 2
        assert telegram.handler is None

        await run_until_completed(db, HeldReads([[1, 2, 3]]), "1", "2", "3")
        assert {state for state, _, _ in (await states(db)).values()} == {"COMPLETED"}
        assert sorted(await states(db)) == ["1", "2", "3"]


async def test_a_bounded_run_starts_no_further_item_once_stopped(tmp_path: Path) -> None:
    # In the shadow delivery run an item may send: after the stop only the one in progress goes on.
    async with database(tmp_path / "relay.sqlite3") as db:
        work, telegram, stop = Work(), Telegram(), StopRequest()
        work.gate.clear()
        queue = real_clock_relay(db, work)
        controller = ShadowRunController(
            ShadowRunLimits(max_messages=5, run_seconds=60, max_api_calls=1, shutdown_seconds=5)
        )
        process = queue.processor.process

        async def process_and_count(source_message_id: int) -> None:
            await process(source_message_id)
            controller.record_processed(cache_hit=False, preview_created=False)

        queue.processor.process = process_and_count  # type: ignore[method-assign]
        subject = monitor(queue, telegram)
        running = asyncio.create_task(subject.run(bounded=controller, stop=stop))
        await until(lambda: telegram.handler is not None)
        for number in (1, 2):
            event = Event(number)
            event.message.date = datetime.now(UTC) + timedelta(seconds=1)  # inside the window
            await asyncio.create_task(telegram.handler(event))
        await asyncio.wait_for(work.running.wait(), 5)

        stop.request("test")
        work.gate.set()
        result = await asyncio.wait_for(running, 10)

        assert result is not None
        assert (result.stop_reason, result.status, result.error_code) == (
            "stop_requested",
            "stopped",
            None,
        )
        assert work.started == ["1"]
        assert result.skip_codes == ("STOPPED_BEFORE_START",)
        assert await states(db) == {"1": ("COMPLETED", 1, False), "2": ("RECEIVED", 0, False)}


# --- the second history read (after subscribing) ----------------------------------------------


async def test_a_stop_during_the_second_read_keeps_the_checkpoint_before_the_gap(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        # First read: 1 and 2. While the second read is under way, 3 and 4 are the gap and 5
        # arrives as a new message.
        telegram, stop = HeldReads([[1, 2], [3, 4, 5]], held=(1,)), StopRequest()
        work = Work()
        running = asyncio.create_task(
            monitor(real_clock_relay(db, work), telegram).run(stop=stop, stop_grace_seconds=5)
        )
        await asyncio.wait_for(telegram.reading[1].wait(), 5)
        await telegram.handler(Event(5))

        stop.request("test")
        await asyncio.wait_for(running, 10)

        assert "5" in await states(db)  # the new message was recorded
        assert await checkpoint(db) == 2  # but the checkpoint did not jump over 3 and 4

        await run_until_completed(db, HeldReads([[1, 2, 3, 4, 5]]), "1", "2", "3", "4", "5")

        rows = await states(db)
        assert sorted(rows) == ["1", "2", "3", "4", "5"]
        assert {state for state, _, _ in rows.values()} == {"COMPLETED"}
        assert {attempts for _, attempts, _ in rows.values()} == {1}
        assert await checkpoint(db) == 5


async def test_a_stop_in_the_middle_of_recording_the_gap_resumes_from_the_last_one_written(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        telegram, stop = HeldReads([[1], [2, 3, 4]], held=(1,)), StopRequest()
        queue = real_clock_relay(db, Work())
        write = HeldWrite(queue, 2)
        running = asyncio.create_task(monitor(queue, telegram).run(stop=stop, stop_grace_seconds=5))
        await asyncio.wait_for(telegram.reading[1].wait(), 5)
        await telegram.handler(Event(9))
        telegram.release[1].set()
        await asyncio.wait_for(write.writing.wait(), 5)

        stop.request("test")
        write.release.set()
        await asyncio.wait_for(running, 10)

        assert sorted(await states(db)) == ["1", "2", "9"]  # 2 finished; 3 was not started
        assert await checkpoint(db) == 2

        await run_until_completed(db, HeldReads([[1, 2, 3, 4, 9]]), "1", "2", "3", "4", "9")

        assert sorted(await states(db)) == ["1", "2", "3", "4", "9"]
        assert {state for state, _, _ in (await states(db)).values()} == {"COMPLETED"}
        assert await checkpoint(db) == 9


async def test_without_a_stop_the_checkpoint_catches_up_after_the_second_read(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        telegram, stop = HeldReads([[1, 2], [3, 4]], held=(1,)), StopRequest()
        work = Work()
        running = asyncio.create_task(monitor(real_clock_relay(db, work), telegram).run(stop=stop))
        await asyncio.wait_for(telegram.reading[1].wait(), 5)
        await telegram.handler(Event(5))
        assert await checkpoint(db) == 2  # held back while the gap is unread

        telegram.release[1].set()
        await asyncio.wait_for(telegram.listening.wait(), 5)

        assert await checkpoint(db) == 5
        await until(lambda: len(work.finished) == 5)
        await telegram.handler(Event(6))
        assert await checkpoint(db) == 6  # back to normal
        stop.request("test")
        await asyncio.wait_for(running, 10)
