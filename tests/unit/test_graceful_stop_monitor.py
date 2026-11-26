"""Ordered stop of the real `TelegramMonitor` with a fake Telegram client and a temporary SQLite
database. Nothing here talks to Telegram."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from telethon.tl.types import PeerChannel

from promo_bot.affiliate.aliexpress_shadow_listener import ShadowRunController, ShadowRunLimits
from promo_bot.config import load_app_config
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.shutdown import StopRequest
from promo_bot.telegram.monitor import TelegramMonitor
from tests.unit.test_graceful_stop_queue import Work, database, relay, states, until

CHAT_ID = -1001234567890


class Message:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.date = datetime.now(UTC)
        self.raw_text = f"offer https://example.test/p/{message_id}"
        self.out = False
        self.buttons = None
        self.media = None

    def get_entities_text(self) -> list[tuple[object, str]]:
        return []


class Event:
    def __init__(self, message_id: int) -> None:
        self.chat_id = CHAT_ID
        self.message = Message(message_id)


class Telegram:
    def __init__(self, history: list[int] | None = None) -> None:
        self.history = history or []
        self.handler: Any = None
        self.disconnected = asyncio.Event()
        self.listening = asyncio.Event()
        self.hang_history_after: int | None = None
        self.history_hanging = asyncio.Event()

    async def connect(self) -> None:
        pass

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, reference: object) -> object:
        return PeerChannel(1234567890)

    def add_event_handler(self, callback: Any, builder: object) -> None:
        self.handler = callback

    def remove_event_handler(self, callback: Any, builder: object) -> None:
        self.handler = None

    async def iter_messages(self, entity: object, limit: int) -> Any:
        for position, message_id in enumerate(self.history):
            if position == self.hang_history_after:
                self.history_hanging.set()
                await asyncio.Event().wait()  # a slow or stuck fetch
            yield Message(message_id)

    async def run_until_disconnected(self) -> None:
        self.listening.set()
        await self.disconnected.wait()

    async def disconnect(self) -> None:
        self.disconnected.set()


def monitor(queue: Any, telegram: Telegram) -> TelegramMonitor:
    example = Path(__file__).resolve().parents[2] / "config.example.yaml"
    config = load_app_config(example).model_copy(update={"source_channels": (str(CHAT_ID),)})
    return TelegramMonitor(EnvironmentSettings(), config, queue, client=telegram)


def real_clock_relay(db: Any, work: Work) -> DurableRelayQueue:
    return relay(db, work, now=datetime.now(UTC))


async def test_a_stop_while_listening_admits_nothing_else_and_finishes_what_runs(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work, telegram, stop = Work(), Telegram(), StopRequest()
        work.gate.clear()
        subject = monitor(real_clock_relay(db, work), telegram)
        running = asyncio.create_task(subject.run(stop=stop, stop_grace_seconds=5))
        await asyncio.wait_for(telegram.listening.wait(), 5)
        handler = telegram.handler
        await handler(Event(1))
        await handler(Event(2))
        await asyncio.wait_for(work.running.wait(), 5)

        stop.request("test")
        await until(lambda: telegram.handler is None)  # no longer subscribed
        assert not running.done()  # still finishing message 1
        work.gate.set()
        await asyncio.wait_for(running, 5)

        assert telegram.disconnected.is_set()
        assert work.finished == ["1"]
        assert await states(db) == {"1": ("COMPLETED", 1, False), "2": ("RECEIVED", 0, False)}
        assert subject.shutdown_report is not None
        assert (subject.shutdown_report.released, subject.shutdown_report.pending) == (0, 1)


async def test_a_stop_during_catch_up_loses_nothing_and_the_restart_reads_the_rest(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        telegram, stop = Telegram([1, 2, 3]), StopRequest()
        telegram.hang_history_after = 2
        subject = monitor(real_clock_relay(db, Work()), telegram)
        running = asyncio.create_task(subject.run(stop=stop, stop_grace_seconds=5))
        await asyncio.wait_for(telegram.history_hanging.wait(), 5)

        stop.request("test")
        await asyncio.wait_for(running, 5)

        assert telegram.handler is None and telegram.disconnected.is_set()
        assert await states(db) == {}  # the fetch had not returned: nothing half-recorded

        work, again = Work(), Telegram([1, 2, 3])
        restarted = monitor(real_clock_relay(db, work), again)
        second_stop = StopRequest()
        second = asyncio.create_task(restarted.run(stop=second_stop))
        await until(lambda: len(work.finished) == 3)
        second_stop.request("test")
        await asyncio.wait_for(second, 5)

        assert work.started == ["1", "2", "3"]


async def test_a_message_arriving_after_a_restart_stop_cycle_is_processed_once(
    tmp_path: Path,
) -> None:
    async with database(tmp_path / "relay.sqlite3") as db:
        work, telegram, stop = Work(), Telegram(), StopRequest()
        subject = monitor(real_clock_relay(db, work), telegram)
        running = asyncio.create_task(subject.run(stop=stop))
        await asyncio.wait_for(telegram.listening.wait(), 5)
        await telegram.handler(Event(1))
        await until(lambda: work.finished == ["1"])
        stop.request("test")
        await asyncio.wait_for(running, 5)

        # While the bot was stopped, message 2 was posted: the catch-up brings 1 and 2.
        work_again, again, second_stop = Work(), Telegram([1, 2]), StopRequest()
        restarted = monitor(real_clock_relay(db, work_again), again)
        second = asyncio.create_task(restarted.run(stop=second_stop))
        await until(lambda: work_again.finished == ["2"])
        await asyncio.sleep(0.05)
        second_stop.request("test")
        await asyncio.wait_for(second, 5)

        assert work_again.started == ["2"]


class RecordingRelay:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start(self, *, recover: bool = True) -> None:
        self.calls.append("start")

    async def join(self) -> None:
        self.calls.append("join")

    async def stop(self) -> None:
        self.calls.append("stop")


async def test_a_stop_ends_a_bounded_run_the_same_way_a_limit_does() -> None:
    queue, telegram, stop = RecordingRelay(), Telegram(), StopRequest()
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=5, run_seconds=60, max_api_calls=1, shutdown_seconds=2)
    )
    subject = monitor(queue, telegram)
    running = asyncio.create_task(subject.run(bounded=controller, stop=stop))
    await until(lambda: telegram.handler is not None)

    stop.request("test")
    result = await asyncio.wait_for(running, 5)

    assert result is not None and result.stop_reason == "stop_requested"
    assert result.error_code is None
    assert telegram.handler is None and telegram.disconnected.is_set()
    assert queue.calls == ["start", "join", "stop"]


async def test_a_bounded_run_waits_for_the_item_in_progress_before_it_leaves(
    tmp_path: Path,
) -> None:
    # In the shadow delivery run, that item is where a message is converted and sent: a stop
    # must not cut it in the middle.
    async with database(tmp_path / "relay.sqlite3") as db:
        work, telegram, stop = Work(), Telegram(), StopRequest()
        work.gate.clear()
        controller = ShadowRunController(
            ShadowRunLimits(max_messages=5, run_seconds=60, max_api_calls=1, shutdown_seconds=5)
        )
        # No shadow processor here, so the controller gets no outcome for the admitted message.
        controller.reconcile_unfinished = lambda code: 0  # type: ignore[method-assign]
        subject = monitor(real_clock_relay(db, work), telegram)
        running = asyncio.create_task(subject.run(bounded=controller, stop=stop))
        await until(lambda: telegram.handler is not None)
        event = Event(1)
        event.message.date = datetime.now(UTC) + timedelta(seconds=1)  # inside the window
        await asyncio.create_task(telegram.handler(event))  # its own task, as in Telethon
        await asyncio.wait_for(work.running.wait(), 5)

        stop.request("test")
        await asyncio.sleep(0.2)
        assert not running.done()
        work.gate.set()
        result = await asyncio.wait_for(running, 5)

        assert result is not None
        assert (result.stop_reason, result.status, result.error_code) == (
            "stop_requested",
            "stopped",
            None,
        )
        assert await states(db) == {"1": ("COMPLETED", 1, False)}
