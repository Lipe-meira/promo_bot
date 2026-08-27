"""Read-only Telethon monitoring with bounded catch-up."""

from __future__ import annotations

import asyncio
import getpass
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from telethon import TelegramClient, events, utils  # type: ignore[import-untyped]
from telethon.errors import (  # type: ignore[import-untyped]
    FloodWaitError,
    SessionPasswordNeededError,
)
from telethon.tl.custom.message import Message  # type: ignore[import-untyped]
from telethon.tl.types import (  # type: ignore[import-untyped]
    MessageEntityTextUrl,
    MessageEntityUrl,
)

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.repositories import TelegramCheckpointRepository
from promo_bot.domain.enums import LinkSource
from promo_bot.relay.catchup import select_catch_up_messages
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.parser import EntityUrl, extract_links
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.relay.retry import BackoffPolicy

LOGGER = logging.getLogger("promo_bot.telegram.monitor")


class TelegramMonitor:
    def __init__(
        self,
        settings: EnvironmentSettings,
        config: AppConfig,
        relay: DurableRelayQueue,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if settings.telegram_api_id is None or settings.telegram_api_hash is None:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        if not config.source_channels:
            raise ValueError("source_channels must list at least one Telegram channel")
        self.settings = settings
        self.config = config
        self.relay = relay
        self.clock = clock or (lambda: datetime.now(UTC))
        self.backoff = BackoffPolicy(
            config.telegram_relay.retry_initial_seconds,
            config.telegram_relay.retry_max_seconds,
        )
        session_path = settings.resolved_telegram_session_path
        _ensure_external_session_path(session_path)
        self.client = TelegramClient(
            str(session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash.get_secret_value(),
            connection_retries=config.telegram_relay.processing_max_attempts,
            request_retries=config.telegram_relay.processing_max_attempts,
            retry_delay=config.telegram_relay.retry_initial_seconds,
            auto_reconnect=True,
            flood_sleep_threshold=0,
        )
        self.client.session.save_entities = False

    async def run(self, *, authorize: bool = False) -> None:
        await self.relay.start()
        try:
            await self.client.connect()
            await self._ensure_authorized(authorize=authorize)
            resolved = await self._resolve_channels()
            catch_up_enabled = self.config.telegram_relay.catch_up_on_start
            if catch_up_enabled:
                await self._catch_up(resolved)
            self.client.add_event_handler(
                self._handle_new_message,
                events.NewMessage(chats=[entity for _, entity in resolved], incoming=True),
            )
            if catch_up_enabled:
                await self._bridge_gap(resolved)
            await self.client.run_until_disconnected()
        finally:
            await self.relay.stop()
            await self.client.disconnect()

    async def _ensure_authorized(self, *, authorize: bool) -> None:
        if await self.client.is_user_authorized():
            return
        if not authorize:
            raise ValueError(
                "Telethon session is not authorized; rerun interactively with listen --authorize"
            )
        phone = input("Telegram phone number: ").strip()
        if not phone:
            raise ValueError("Telegram phone number cannot be empty")
        await self.client.send_code_request(phone)
        code = getpass.getpass("Telegram login code: ").strip()
        try:
            await self.client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = getpass.getpass("Telegram 2FA password: ")
            await self.client.sign_in(password=password)

    async def _resolve_channels(self) -> list[tuple[str, object]]:
        resolved: list[tuple[str, object]] = []
        for configured_channel in self.config.source_channels:
            entity = await self.client.get_entity(_channel_reference(configured_channel))
            channel_id = str(utils.get_peer_id(entity))
            resolved.append((channel_id, entity))
        return resolved

    async def _catch_up(self, channels: Sequence[tuple[str, object]]) -> None:
        for channel_id, entity in channels:
            try:
                checkpoint_id: int | None = None
                async with self.relay.database.session() as session:
                    checkpoint = await TelegramCheckpointRepository(session).get(channel_id)
                    if checkpoint is not None:
                        checkpoint_id = checkpoint.last_persisted_message_id
                messages = await self._fetch_recent(entity, channel_id, checkpoint_id)
                for message in messages:
                    await self.relay.persist(message)
                async with self.relay.database.session() as session:
                    await TelegramCheckpointRepository(session).mark_catch_up_complete(
                        channel_id, now=self.clock()
                    )
            except Exception:
                async with self.relay.database.session() as session:
                    await TelegramCheckpointRepository(session).record_failure(
                        channel_id, "CATCH_UP_FAILED"
                    )
                raise RuntimeError("CATCH_UP_FAILED") from None

    async def _bridge_gap(self, channels: Sequence[tuple[str, object]]) -> None:
        for channel_id, entity in channels:
            async with self.relay.database.session() as session:
                checkpoint = await TelegramCheckpointRepository(session).get(channel_id)
                checkpoint_id = checkpoint.last_persisted_message_id if checkpoint else None
            for message in await self._fetch_recent(entity, channel_id, checkpoint_id):
                await self.relay.persist(message)

    async def _fetch_recent(
        self, entity: object, channel_id: str, checkpoint_id: int | None
    ) -> list[IncomingMessage]:
        for attempt in range(1, self.config.telegram_relay.processing_max_attempts + 1):
            try:
                items: list[IncomingMessage] = []
                async for message in self.client.iter_messages(
                    entity,
                    limit=self.config.telegram_relay.catch_up_max_messages_per_channel,
                ):
                    items.append(_adapt_message(message, channel_id))
                if len(items) >= self.config.telegram_relay.catch_up_max_messages_per_channel:
                    LOGGER.warning(
                        "catch-up scan reached its configured per-channel limit",
                        extra={
                            "channel_id": channel_id,
                            "stage": "catch_up",
                            "result": "bounded",
                            "error_code": "CATCH_UP_LIMIT_REACHED",
                        },
                    )
                return select_catch_up_messages(
                    items,
                    now=self.clock(),
                    lookback_hours=self.config.telegram_relay.catch_up_lookback_hours,
                    max_messages=self.config.telegram_relay.catch_up_max_messages_per_channel,
                    checkpoint_id=checkpoint_id,
                )
            except FloodWaitError as exc:
                if attempt >= self.config.telegram_relay.processing_max_attempts:
                    raise
                if exc.seconds > self.config.telegram_relay.retry_max_seconds:
                    raise RuntimeError("TELEGRAM_FLOOD_WAIT_EXCEEDS_LIMIT") from exc
                await asyncio.sleep(float(exc.seconds))
            except (ConnectionError, OSError, TimeoutError):
                if attempt >= self.config.telegram_relay.processing_max_attempts:
                    raise
                await asyncio.sleep(self.backoff.delay_seconds(attempt))
        raise AssertionError("unreachable")

    async def _handle_new_message(self, event: events.NewMessage.Event) -> None:
        channel_id = str(event.chat_id)
        try:
            await self.relay.persist(_adapt_message(event.message, channel_id))
        except Exception:
            LOGGER.error(
                "failed to persist Telegram event",
                extra={
                    "message_id": str(event.message.id),
                    "stage": "telegram_receive",
                    "result": "failed",
                    "error_code": "TELEGRAM_EVENT_PERSIST_FAILED",
                },
            )


def _adapt_message(message: Message, channel_id: str) -> IncomingMessage:
    text = message.raw_text or ""
    entities: list[EntityUrl] = []
    for entity, entity_text in message.get_entities_text():
        if isinstance(entity, MessageEntityTextUrl) and entity.url:
            entities.append(EntityUrl(entity.url, LinkSource.ENTITY_TEXT_URL, entity.offset))
        elif isinstance(entity, MessageEntityUrl):
            entities.append(EntityUrl(entity_text, LinkSource.ENTITY_URL, entity.offset))
    button_urls: list[str] = []
    if message.buttons:
        for row in message.buttons:
            for button in row:
                url = getattr(button, "url", None)
                if isinstance(url, str):
                    button_urls.append(url)
    return IncomingMessage(
        platform="telegram",
        message_id=message.id,
        channel_id=channel_id,
        occurred_at=message.date,
        original_text=text,
        links=extract_links(text, entity_urls=entities, button_urls=button_urls),
    )


def _ensure_external_session_path(path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    resolved = path.expanduser().resolve()
    if resolved == repository_root or repository_root in resolved.parents:
        raise ValueError("Telethon session path must be outside the Git workspace")
    resolved.parent.mkdir(parents=True, exist_ok=True)


def _channel_reference(value: str) -> str | int:
    stripped = value.strip()
    return int(stripped) if stripped.lstrip("-").isdigit() else stripped
