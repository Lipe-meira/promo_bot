from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import (
    Base,
    ProcessedItemModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import LinkSource, RelayLinkState, SourceMessageState
from promo_bot.relay.models import ExtractedLink, IncomingMessage
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.relay.service import RelayProcessor
from promo_bot.security.urls import SafeUrlError, TransientUrlError

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


class FailingExpander:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def expand(self, url: str) -> None:
        del url
        raise self.error


async def make_database(tmp_path: Path, name: str) -> Database:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def incoming(message_id: int, url: str) -> IncomingMessage:
    return IncomingMessage(
        "telegram",
        message_id,
        "channel",
        NOW,
        f"offer {url}",
        (ExtractedLink(url, LinkSource.TEXT, 0),),
    )


@pytest.mark.asyncio
async def test_direct_store_url_completes_as_pending_affiliate(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "direct.sqlite3")
    config = TelegramRelayConfig()
    relay = DurableRelayQueue(database, config, clock=lambda: NOW)
    persisted = await relay.persist(
        incoming(1, "https://www.amazon.com.br/dp/B0ABCDEFGH?tag=other-20&utm_source=x")
    )

    await relay.processor.process(persisted.internal_id)

    async with database.session() as session:
        source = await session.get(SourceMessageModel, persisted.internal_id)
        link = (await session.execute(select(SourceMessageLinkModel))).scalar_one()
        processed = (await session.execute(select(ProcessedItemModel))).scalar_one()
        assert source is not None and source.processing_status == SourceMessageState.COMPLETED.value
        assert link.state == RelayLinkState.PENDING_AFFILIATE.value
        assert link.canonical_url == "https://www.amazon.com.br/dp/B0ABCDEFGH"
        assert processed.variation_key == ""
    await database.dispose()


@pytest.mark.asyncio
async def test_canonical_candidate_is_deduplicated_across_messages(tmp_path: Path) -> None:
    path = tmp_path / "dedup.sqlite3"
    database = await make_database(tmp_path, path.name)
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)
    first = await relay.persist(incoming(1, "https://www.kabum.com.br/produto/123?awc=old"))
    await relay.processor.process(first.internal_id)
    await database.dispose()

    restarted_database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    restarted_relay = DurableRelayQueue(
        restarted_database, TelegramRelayConfig(), clock=lambda: NOW
    )
    second = await restarted_relay.persist(
        incoming(2, "https://www.kabum.com.br/produto/123?utm_source=another")
    )
    await restarted_relay.processor.process(second.internal_id)

    async with restarted_database.session() as session:
        links = list(
            (
                await session.execute(
                    select(SourceMessageLinkModel).order_by(SourceMessageLinkModel.id)
                )
            ).scalars()
        )
        assert [item.state for item in links] == [
            RelayLinkState.PENDING_AFFILIATE.value,
            RelayLinkState.IGNORED.value,
        ]
        assert links[1].reason_code == "DUPLICATE_CANONICAL"
    await restarted_database.dispose()


@pytest.mark.asyncio
async def test_transient_http_failure_is_retryable(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "transient.sqlite3")
    config = TelegramRelayConfig()
    processor = RelayProcessor(
        database,
        config,
        expander=FailingExpander(TransientUrlError("HTTP_TIMEOUT")),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    relay = DurableRelayQueue(database, config, processor=processor, clock=lambda: NOW)
    persisted = await relay.persist(incoming(1, "https://amzn.to/example"))

    await processor.process(persisted.internal_id)

    async with database.session() as session:
        source = await session.get(SourceMessageModel, persisted.internal_id)
        assert source is not None
        assert source.processing_status == SourceMessageState.FAILED_RETRYABLE.value
        assert source.error_code == "HTTP_TIMEOUT"
        assert source.next_attempt_at is not None
    await database.dispose()


@pytest.mark.asyncio
async def test_security_rejection_is_not_retried(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "security.sqlite3")
    config = TelegramRelayConfig()
    processor = RelayProcessor(
        database,
        config,
        expander=FailingExpander(SafeUrlError("PEER_IP_UNVERIFIED")),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    relay = DurableRelayQueue(database, config, processor=processor, clock=lambda: NOW)
    persisted = await relay.persist(incoming(1, "https://amzn.to/example"))

    await processor.process(persisted.internal_id)

    async with database.session() as session:
        source = await session.get(SourceMessageModel, persisted.internal_id)
        link = (await session.execute(select(SourceMessageLinkModel))).scalar_one()
        assert source is not None and source.processing_status == SourceMessageState.COMPLETED.value
        assert link.state == RelayLinkState.REJECTED.value
        assert link.reason_code == "PEER_IP_UNVERIFIED"
    await database.dispose()


@pytest.mark.asyncio
async def test_ambiguous_variation_is_never_used_for_deduplication(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "variation.sqlite3")
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)
    persisted = await relay.persist(
        incoming(1, "https://www.amazon.com.br/dp/B0ABCDEFGH?color=blue")
    )

    await relay.processor.process(persisted.internal_id)

    async with database.session() as session:
        link = (await session.execute(select(SourceMessageLinkModel))).scalar_one()
        processed = list((await session.execute(select(ProcessedItemModel))).scalars())
        assert link.state == RelayLinkState.MANUAL_REVIEW.value
        assert link.reason_code == "VARIATION_AMBIGUOUS"
        assert processed == []
    await database.dispose()
