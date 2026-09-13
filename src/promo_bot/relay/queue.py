"""SQLite-backed intake with a bounded in-memory delivery queue."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    SourceMessageRepository,
    TelegramCheckpointRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import SourceMessageState
from promo_bot.relay.models import IncomingMessage, PersistedMessage
from promo_bot.relay.service import RelayProcessor

LOGGER = logging.getLogger("promo_bot.relay.queue")


class RelayWorkProcessor(Protocol):
    async def process(self, source_message_id: int) -> None: ...


class DurableRelayQueue:
    def __init__(
        self,
        database: Database,
        config: TelegramRelayConfig,
        *,
        processor: RelayWorkProcessor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        self.processor = processor or RelayProcessor(database, config, clock=self.clock)
        self.queue: asyncio.Queue[int] = asyncio.Queue(maxsize=config.queue_max_size)
        self._enqueued: set[int] = set()
        self._tasks: list[asyncio.Task[None]] = []

    async def persist(self, message: IncomingMessage) -> PersistedMessage:
        persisted = await self.persist_without_enqueue(message)
        if persisted.completed_duplicate or not persisted.content_matches or not persisted.queued:
            return persisted
        queued = await self._enqueue(persisted.internal_id, mark_capacity=True)
        return PersistedMessage(
            persisted.internal_id,
            persisted.created,
            persisted.completed_duplicate,
            queued,
            persisted.content_matches,
        )

    async def persist_without_enqueue(self, message: IncomingMessage) -> PersistedMessage:
        """Persist one message while leaving worker scheduling to an explicit caller."""

        async with self.database.session() as session:
            messages = SourceMessageRepository(session)
            result = await messages.receive(
                platform=message.platform,
                message_id=str(message.message_id),
                channel_id=message.channel_id,
                occurred_at=message.occurred_at,
                original_text=message.original_text,
                links=[link.as_dict() for link in message.links],
                content_hash=message.content_hash,
                legacy_content_hash=message.legacy_content_hash,
                surface_metadata=message.surface_metadata.as_dict(),
            )
            await TelegramCheckpointRepository(session).record_persisted(
                channel_id=message.channel_id,
                message_id=message.message_id,
                occurred_at=message.occurred_at,
            )
            if not result.content_matches and not result.legacy_content_matches:
                await messages.mark_content_mismatch(result.message.id)
            state = SourceMessageState(result.message.processing_status)
            internal_id = result.message.id
            legacy_history = bool(
                result.legacy_content_matches
                and (
                    state is SourceMessageState.COMPLETED
                    or await messages.has_shadow_preview_or_terminal_delivery(internal_id)
                )
            )

        completed_duplicate = (
            not result.created and result.content_matches and state is SourceMessageState.COMPLETED
        )
        if result.legacy_content_matches:
            safe_surface = message.surface_metadata.is_safe_plain_text
            rejection_code = None
            if not safe_surface:
                rejection_code = "ALIEXPRESS_MESSAGE_SURFACE_UNSAFE"
            elif not legacy_history:
                rejection_code = "TELEGRAM_LEGACY_SOURCE_UNVALIDATED"
            return PersistedMessage(
                internal_id,
                result.created,
                completed_duplicate=legacy_history and safe_surface,
                queued=False,
                content_matches=False,
                legacy_compatible=True,
                legacy_rejection_code=rejection_code,
            )
        if (
            completed_duplicate
            or not result.content_matches
            or state is SourceMessageState.FAILED_PERMANENT
        ):
            return PersistedMessage(
                internal_id, result.created, completed_duplicate, False, result.content_matches
            )
        return PersistedMessage(
            internal_id, result.created, completed_duplicate, True, result.content_matches
        )

    async def recover_once(self) -> int:
        now = self.clock()
        async with self.database.session() as session:
            repository = SourceMessageRepository(session)
            await repository.mark_exhausted(max_attempts=self.config.processing_max_attempts)
            messages = await repository.recoverable(
                now=now,
                max_attempts=self.config.processing_max_attempts,
                limit=self.config.recovery_batch_size,
            )
        queued = 0
        for message in messages:
            if await self._enqueue(message.id, mark_capacity=False):
                queued += 1
            else:
                break
        return queued

    async def backfill_affiliate_candidates_once(self) -> int:
        async with self.database.session() as session:
            count = await AffiliateCandidateRepository(session).backfill_shopee(
                limit=self.config.recovery_batch_size
            )
        if count:
            LOGGER.info(
                "legacy Shopee candidates retained for affiliate enrichment",
                extra={
                    "stage": "affiliate_backfill",
                    "result": "retained",
                    "count": count,
                },
            )
        return count

    async def start(self, *, worker_count: int = 1, recover: bool = True) -> None:
        if self._tasks:
            return
        if recover:
            await self.backfill_affiliate_candidates_once()
            await self.recover_once()
        self._tasks.extend(
            asyncio.create_task(self._worker(), name=f"relay-worker-{index}")
            for index in range(worker_count)
        )
        if recover:
            self._tasks.append(asyncio.create_task(self._recovery_loop(), name="relay-recovery"))

    async def stop(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._tasks.clear()

    async def join(self) -> None:
        await self.queue.join()

    async def _enqueue(self, internal_id: int, *, mark_capacity: bool) -> bool:
        if internal_id in self._enqueued:
            return True
        try:
            self.queue.put_nowait(internal_id)
        except asyncio.QueueFull:
            if mark_capacity:
                async with self.database.session() as session:
                    await SourceMessageRepository(session).defer_queue_full(internal_id)
            LOGGER.warning(
                "relay queue at capacity; message remains durable",
                extra={
                    "message_id": str(internal_id),
                    "stage": "enqueue",
                    "result": "deferred",
                    "error_code": "QUEUE_CAPACITY_DEFERRED",
                },
            )
            return False
        self._enqueued.add(internal_id)
        return True

    async def _worker(self) -> None:
        while True:
            internal_id = await self.queue.get()
            try:
                await self.processor.process(internal_id)
            finally:
                self._enqueued.discard(internal_id)
                self.queue.task_done()

    async def _recovery_loop(self) -> None:
        interval = min(5.0, float(self.config.retry_initial_seconds))
        while True:
            await asyncio.sleep(interval)
            await self.backfill_affiliate_candidates_once()
            await self.recover_once()
