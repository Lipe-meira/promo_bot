from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import Base
from promo_bot.database.repositories import (
    SourceMessageRepository,
    TelegramCheckpointRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import SourceMessageState
from promo_bot.observability import configure_logging
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.queue import DurableRelayQueue

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


def incoming(message_id: int, text: str = "fixture") -> IncomingMessage:
    return IncomingMessage("telegram", message_id, "channel-1", NOW, text, ())


async def make_database(tmp_path: Path, name: str) -> Database:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


@pytest.mark.asyncio
async def test_queue_full_keeps_message_received_and_advances_durable_checkpoint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    database = await make_database(tmp_path, "queue.sqlite3")
    relay = DurableRelayQueue(database, TelegramRelayConfig(queue_max_size=1), clock=lambda: NOW)

    first = await relay.persist(incoming(1))
    second = await relay.persist(incoming(2, "DO_NOT_LOG_FULL_MESSAGE"))

    assert first.queued
    assert not second.queued
    async with database.session() as session:
        await TelegramCheckpointRepository(session).record_persisted(
            channel_id="channel-1", message_id=1, occurred_at=NOW - timedelta(minutes=1)
        )
        stored = await SourceMessageRepository(session).get(second.internal_id)
        checkpoint = await TelegramCheckpointRepository(session).get("channel-1")
        assert stored is not None
        assert stored.processing_status == SourceMessageState.RECEIVED.value
        assert stored.error_code == "QUEUE_CAPACITY_DEFERRED"
        assert checkpoint is not None and checkpoint.last_persisted_message_id == 2
    assert "DO_NOT_LOG_FULL_MESSAGE" not in capsys.readouterr().err
    await database.dispose()


@pytest.mark.asyncio
async def test_only_completed_message_is_a_completed_duplicate(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "duplicate.sqlite3")
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)
    first = await relay.persist(incoming(1))
    async with database.session() as session:
        repository = SourceMessageRepository(session)
        claimed = await repository.claim(
            first.internal_id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
            max_attempts=5,
        )
        assert claimed is not None
        await repository.complete(first.internal_id, now=NOW)

    duplicate = await relay.persist(incoming(1))

    assert duplicate.completed_duplicate
    assert not duplicate.queued
    await database.dispose()


@pytest.mark.asyncio
async def test_content_change_for_same_identity_fails_permanently(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "mismatch.sqlite3")
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)
    first = await relay.persist(incoming(1, "original"))

    mismatch = await relay.persist(incoming(1, "changed"))

    assert not mismatch.content_matches
    assert not mismatch.completed_duplicate
    async with database.session() as session:
        stored = await SourceMessageRepository(session).get(first.internal_id)
        assert stored is not None
        assert stored.processing_status == SourceMessageState.FAILED_PERMANENT.value
        assert stored.error_code == "CONTENT_HASH_MISMATCH"
        assert stored.original_text == "original"
    await database.dispose()


@pytest.mark.asyncio
async def test_stale_processing_and_due_retry_are_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "recovery.sqlite3"
    database = await make_database(tmp_path, path.name)
    async with database.session() as session:
        repository = SourceMessageRepository(session)
        stale = await repository.receive(
            platform="telegram",
            message_id="1",
            channel_id="channel",
            occurred_at=NOW,
            original_text="stale",
            links=[],
            content_hash="a" * 64,
        )
        retry = await repository.receive(
            platform="telegram",
            message_id="2",
            channel_id="channel",
            occurred_at=NOW,
            original_text="retry",
            links=[],
            content_hash="b" * 64,
        )
        claimed_stale = await repository.claim(
            stale.message.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
            max_attempts=3,
        )
        claimed_retry = await repository.claim(
            retry.message.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
            max_attempts=3,
        )
        assert claimed_stale is not None and claimed_retry is not None
        await repository.fail(
            retry.message.id,
            retryable=True,
            max_attempts=3,
            next_attempt_at=NOW + timedelta(seconds=10),
            error_code="HTTP_TIMEOUT",
        )

    await database.dispose()

    restarted_database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    relay = DurableRelayQueue(
        restarted_database,
        TelegramRelayConfig(processing_max_attempts=3),
        clock=lambda: NOW + timedelta(minutes=2),
    )
    assert await relay.recover_once() == 2
    recovered_ids = [relay.queue.get_nowait(), relay.queue.get_nowait()]
    assert recovered_ids == [stale.message.id, retry.message.id]
    await restarted_database.dispose()


@pytest.mark.asyncio
async def test_retry_limit_becomes_permanent(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "exhausted.sqlite3")
    async with database.session() as session:
        repository = SourceMessageRepository(session)
        received = await repository.receive(
            platform="telegram",
            message_id="1",
            channel_id="channel",
            occurred_at=NOW,
            original_text="fixture",
            links=[],
            content_hash="a" * 64,
        )
        for attempt in range(2):
            claimed = await repository.claim(
                received.message.id,
                now=NOW + timedelta(minutes=attempt),
                lease_until=NOW + timedelta(minutes=attempt + 1),
                max_attempts=2,
            )
            assert claimed is not None
            state = await repository.fail(
                received.message.id,
                retryable=True,
                max_attempts=2,
                next_attempt_at=NOW,
                error_code="HTTP_TIMEOUT",
            )

    assert state is SourceMessageState.FAILED_PERMANENT
    async with database.session() as session:
        stored = await SourceMessageRepository(session).get(received.message.id)
        assert stored is not None and stored.error_code == "RETRY_EXHAUSTED"
    await database.dispose()
