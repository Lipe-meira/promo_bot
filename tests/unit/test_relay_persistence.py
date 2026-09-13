from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import Base, SourceMessageModel
from promo_bot.database.repositories import (
    SourceMessageRepository,
    TelegramCheckpointRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import LinkSource, SourceMessageState
from promo_bot.observability import configure_logging
from promo_bot.relay.models import ExtractedLink, IncomingMessage, MessageSurfaceMetadata
from promo_bot.relay.queue import DurableRelayQueue

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


def incoming(message_id: int, text: str = "fixture") -> IncomingMessage:
    return IncomingMessage("telegram", message_id, "channel-1", NOW, text, ())


def old_content_hash(message: IncomingMessage) -> str:
    payload = {
        "text": message.original_text,
        "links": [link.as_dict() for link in message.links],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def seed_legacy_message(
    database: Database,
    message: IncomingMessage,
    *,
    state: SourceMessageState,
) -> None:
    async with database.session() as session:
        session.add(
            SourceMessageModel(
                platform=message.platform,
                message_id=str(message.message_id),
                channel_id=message.channel_id,
                occurred_at=message.occurred_at,
                original_text=message.original_text,
                links=[link.as_dict() for link in message.links],
                surface_metadata={"legacy_unknown": True},
                content_hash=old_content_hash(message),
                processing_status=state.value,
                attempt_count=1 if state is SourceMessageState.COMPLETED else 0,
                completed_at=NOW if state is SourceMessageState.COMPLETED else None,
            )
        )


def test_message_surface_metadata_changes_content_identity() -> None:
    plain = incoming(1, "same visible text")
    hidden = IncomingMessage(
        "telegram",
        1,
        "channel-1",
        NOW,
        "same visible text",
        (),
        surface_metadata=MessageSurfaceMetadata(has_hidden_links=True),
    )

    assert plain.content_hash != hidden.content_hash
    assert hidden.surface_metadata.as_dict() == {
        "has_buttons": False,
        "has_caption": False,
        "has_custom_emoji": False,
        "has_hidden_links": True,
        "has_media": False,
        "flattened_entity_types": [],
        "unsupported_entity_types": [],
    }


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
        assert (
            stored.surface_metadata
            == incoming(2, "DO_NOT_LOG_FULL_MESSAGE").surface_metadata.as_dict()
        )
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


@pytest.mark.parametrize("change", ["text", "link"])
@pytest.mark.asyncio
async def test_changed_visible_content_never_matches_legacy_hash(
    tmp_path: Path,
    change: str,
) -> None:
    database = await make_database(tmp_path, "legacy-changed.sqlite3")
    original = IncomingMessage(
        "telegram",
        71,
        "channel-1",
        NOW,
        "oferta original https://example.invalid/item/1",
        (ExtractedLink("https://example.invalid/item/1", LinkSource.TEXT, 0),),
    )
    changed_link = (
        ExtractedLink("https://example.invalid/item/2", LinkSource.TEXT, 0)
        if change == "link"
        else original.links[0]
    )
    changed = IncomingMessage(
        "telegram",
        71,
        "channel-1",
        NOW,
        (
            "oferta original https://example.invalid/item/2"
            if change == "link"
            else "oferta alterada https://example.invalid/item/1"
        ),
        (changed_link,),
        surface_metadata=MessageSurfaceMetadata(
            flattened_entity_types=("MessageEntityBold", "MessageEntityUrl")
        ),
    )
    await seed_legacy_message(database, original, state=SourceMessageState.COMPLETED)
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)

    persisted = await relay.persist(changed)

    assert not persisted.content_matches
    assert not persisted.completed_duplicate
    assert not persisted.queued
    async with database.session() as session:
        stored = await SourceMessageRepository(session).get(persisted.internal_id)
        assert stored is not None
        assert stored.processing_status == SourceMessageState.FAILED_PERMANENT.value
        assert stored.error_code == "CONTENT_HASH_MISMATCH"
        assert stored.original_text == original.original_text
    await database.dispose()


@pytest.mark.asyncio
async def test_old_hash_without_legacy_marker_never_uses_compatibility(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "old-hash-without-marker.sqlite3")
    message = incoming(74, "mesmo texto visível")
    async with database.session() as session:
        session.add(
            SourceMessageModel(
                platform=message.platform,
                message_id=str(message.message_id),
                channel_id=message.channel_id,
                occurred_at=message.occurred_at,
                original_text=message.original_text,
                links=[],
                surface_metadata={},
                content_hash=old_content_hash(message),
                processing_status=SourceMessageState.COMPLETED.value,
                attempt_count=1,
                completed_at=NOW,
            )
        )
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)

    persisted = await relay.persist(message)

    assert not persisted.completed_duplicate
    assert not persisted.queued
    async with database.session() as session:
        stored = await SourceMessageRepository(session).get(persisted.internal_id)
        assert stored is not None
        assert stored.processing_status == SourceMessageState.FAILED_PERMANENT.value
        assert stored.error_code == "CONTENT_HASH_MISMATCH"
    await database.dispose()


@pytest.mark.asyncio
async def test_exact_legacy_hash_does_not_authorize_nonterminal_message(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "legacy-pending.sqlite3")
    message = IncomingMessage(
        "telegram",
        72,
        "channel-1",
        NOW,
        "oferta https://example.invalid/item/1",
        (ExtractedLink("https://example.invalid/item/1", LinkSource.TEXT, 0),),
        surface_metadata=MessageSurfaceMetadata(
            flattened_entity_types=("MessageEntityBold", "MessageEntityUrl")
        ),
    )
    await seed_legacy_message(database, message, state=SourceMessageState.RECEIVED)
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)

    persisted = await relay.persist(message)

    assert not persisted.completed_duplicate
    assert not persisted.queued
    async with database.session() as session:
        stored = await SourceMessageRepository(session).get(persisted.internal_id)
        assert stored is not None
        assert stored.processing_status == SourceMessageState.RECEIVED.value
        assert stored.error_code is None
        assert stored.content_hash == old_content_hash(message)
        assert stored.surface_metadata == {"legacy_unknown": True}
    await database.dispose()


@pytest.mark.parametrize(
    "metadata",
    [
        MessageSurfaceMetadata(has_hidden_links=True),
        MessageSurfaceMetadata(has_buttons=True),
        MessageSurfaceMetadata(has_media=True),
        MessageSurfaceMetadata(has_caption=True),
        MessageSurfaceMetadata(has_custom_emoji=True),
        MessageSurfaceMetadata(unsupported_entity_types=("MessageEntityUnknown",)),
    ],
)
@pytest.mark.asyncio
async def test_unsafe_current_surface_never_uses_legacy_compatibility(
    tmp_path: Path,
    metadata: MessageSurfaceMetadata,
) -> None:
    database = await make_database(tmp_path, "legacy-unsafe.sqlite3")
    original = incoming(73, "mesmo texto visível")
    current = IncomingMessage(
        original.platform,
        original.message_id,
        original.channel_id,
        original.occurred_at,
        original.original_text,
        original.links,
        surface_metadata=metadata,
    )
    await seed_legacy_message(database, original, state=SourceMessageState.COMPLETED)
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)

    persisted = await relay.persist(current)

    assert not persisted.completed_duplicate
    assert not persisted.queued
    async with database.session() as session:
        stored = await SourceMessageRepository(session).get(persisted.internal_id)
        assert stored is not None
        assert stored.processing_status == SourceMessageState.COMPLETED.value
        assert stored.error_code is None
        assert stored.content_hash == old_content_hash(original)
        assert stored.surface_metadata == {"legacy_unknown": True}
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
