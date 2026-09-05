"""Read-only Telethon monitoring with bounded catch-up."""

from __future__ import annotations

import asyncio
import getpass
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

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
TELEGRAM_PUBLIC_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")


def build_telegram_user_client(
    settings: EnvironmentSettings,
    *,
    connection_retries: int = 3,
    retry_delay: float = 2,
) -> TelegramClient:
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
    session_path = settings.resolved_telegram_session_path
    _ensure_external_session_path(session_path)
    client = TelegramClient(
        str(session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
        connection_retries=connection_retries,
        request_retries=connection_retries,
        retry_delay=retry_delay,
        auto_reconnect=True,
        flood_sleep_threshold=0,
    )
    client.session.save_entities = False
    return client


@dataclass(frozen=True, slots=True)
class TelegramMessageReference:
    message_id: int
    chat_id: int | None = None
    username: str | None = None

    def __post_init__(self) -> None:
        if self.message_id < 1 or (self.chat_id is None) == (self.username is None):
            raise ValueError("TELEGRAM_MESSAGE_REFERENCE_INVALID")
        if self.chat_id == 0:
            raise ValueError("TELEGRAM_MESSAGE_REFERENCE_INVALID")
        if self.username is not None and TELEGRAM_PUBLIC_USERNAME.fullmatch(self.username) is None:
            raise ValueError("TELEGRAM_MESSAGE_REFERENCE_INVALID")


@dataclass(frozen=True, slots=True)
class ResolvedTelegramChannel:
    reference: str | int
    chat_id: int
    entity: object


class ReadOnlyTelegramClient(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_user_authorized(self) -> bool: ...

    async def resolve_channel(self, reference: str | int) -> ResolvedTelegramChannel: ...

    async def get_message(self, entity: object, message_id: int) -> Message | None: ...


class TelethonReadOnlyClient:
    """Narrow Telethon to connection and single-message read operations."""

    def __init__(
        self,
        client: Any,
        *,
        peer_id: Callable[[object], int] = utils.get_peer_id,
    ) -> None:
        self._client = client
        self._peer_id = peer_id

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def is_user_authorized(self) -> bool:
        return bool(await self._client.is_user_authorized())

    async def resolve_channel(self, reference: str | int) -> ResolvedTelegramChannel:
        entity = await self._client.get_entity(reference)
        return ResolvedTelegramChannel(
            reference=reference,
            chat_id=int(self._peer_id(entity)),
            entity=entity,
        )

    async def get_message(self, entity: object, message_id: int) -> Message | None:
        message = await self._client.get_messages(entity, ids=message_id)
        if message is None:
            return None
        if isinstance(message, Sequence) and not isinstance(message, (str, bytes)):
            return message[0] if message else None
        return message

    def __repr__(self) -> str:
        return "TelethonReadOnlyClient(session=<redacted>)"

    __str__ = __repr__


def parse_telegram_message_link(url: str) -> TelegramMessageReference:
    """Parse one canonical Telegram message URL without resolving or opening it."""

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("TELEGRAM_MESSAGE_LINK_INVALID") from None
    if (
        parts.scheme != "https"
        or parts.hostname != "t.me"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "%" in parts.path
    ):
        raise ValueError("TELEGRAM_MESSAGE_LINK_INVALID")
    segments = parts.path.strip("/").split("/")
    if len(segments) == 3 and segments[0] == "c":
        channel_part, message_part = segments[1:]
        if not channel_part.isdigit() or not message_part.isdigit():
            raise ValueError("TELEGRAM_MESSAGE_LINK_INVALID")
        channel_id = int(channel_part)
        message_id = int(message_part)
        if channel_id < 1 or message_id < 1:
            raise ValueError("TELEGRAM_MESSAGE_LINK_INVALID")
        return TelegramMessageReference(
            message_id=message_id,
            chat_id=int(f"-100{channel_id}"),
        )
    if len(segments) == 2:
        username, message_part = segments
        if (
            TELEGRAM_PUBLIC_USERNAME.fullmatch(username) is None
            or not message_part.isdigit()
            or int(message_part) < 1
        ):
            raise ValueError("TELEGRAM_MESSAGE_LINK_INVALID")
        return TelegramMessageReference(message_id=int(message_part), username=username)
    raise ValueError("TELEGRAM_MESSAGE_LINK_INVALID")


class TelegramOneShotReader:
    """Fetch exactly one allowlisted Telegram message through a read-only interface."""

    def __init__(
        self,
        client: ReadOnlyTelegramClient,
        *,
        source_channels: Sequence[str],
    ) -> None:
        if not source_channels:
            raise ValueError("TELEGRAM_SOURCE_ALLOWLIST_EMPTY")
        self.client = client
        self.numeric_sources = frozenset(
            int(item.strip()) for item in source_channels if item.strip().lstrip("-").isdigit()
        )
        self.username_sources = frozenset(
            item.strip().removeprefix("@").casefold()
            for item in source_channels
            if not item.strip().lstrip("-").isdigit()
        )

    async def fetch(self, reference: TelegramMessageReference) -> IncomingMessage:
        connected = False
        try:
            await self.client.connect()
            connected = True
            if not await self.client.is_user_authorized():
                raise ValueError("TELEGRAM_SESSION_NOT_AUTHORIZED")
            channel = await self._resolve_allowlisted(reference)
            raw_message = await self.client.get_message(channel.entity, reference.message_id)
            if raw_message is None or raw_message.id != reference.message_id:
                raise ValueError("TELEGRAM_MESSAGE_NOT_FOUND")
            return _adapt_message(cast(Message, raw_message), str(channel.chat_id))
        except ValueError:
            raise
        except Exception:
            raise ValueError("TELEGRAM_READ_FAILED") from None
        finally:
            if connected:
                try:
                    await self.client.disconnect()
                except Exception:
                    raise ValueError("TELEGRAM_DISCONNECT_FAILED") from None

    async def _resolve_allowlisted(
        self, reference: TelegramMessageReference
    ) -> ResolvedTelegramChannel:
        if reference.username is not None:
            username = reference.username.casefold()
            if username not in self.username_sources:
                raise ValueError("TELEGRAM_SOURCE_NOT_ALLOWLISTED")
            return await self.client.resolve_channel(username)

        assert reference.chat_id is not None
        if reference.chat_id in self.numeric_sources:
            return await self.client.resolve_channel(reference.chat_id)
        for username in sorted(self.username_sources):
            resolved = await self.client.resolve_channel(username)
            if resolved.chat_id == reference.chat_id:
                return resolved
        raise ValueError("TELEGRAM_SOURCE_NOT_ALLOWLISTED")


async def authorize_telegram_session(
    client: Any,
    *,
    phone_prompt: Callable[[str], str] = input,
    secret_prompt: Callable[[str], str] = getpass.getpass,
) -> bool:
    """Create a Telethon user session without starting any listener."""

    await client.connect()
    try:
        if await client.is_user_authorized():
            return False
        phone = phone_prompt("Telegram phone number: ").strip()
        if not phone:
            raise ValueError("Telegram phone number cannot be empty")
        await client.send_code_request(phone)
        code = secret_prompt("Telegram login code: ").strip()
        if not code:
            raise ValueError("Telegram login code cannot be empty")
        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = secret_prompt("Telegram 2FA password: ")
            if not password:
                raise ValueError("Telegram 2FA password cannot be empty") from None
            await client.sign_in(password=password)
        return True
    finally:
        await client.disconnect()


class TelegramMonitor:
    def __init__(
        self,
        settings: EnvironmentSettings,
        config: AppConfig,
        relay: DurableRelayQueue,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not config.source_channels:
            raise ValueError("source_channels must list at least one Telegram channel")
        self.settings = settings
        self.config = config
        self.relay = relay
        self._source_channel_ids: frozenset[str] = frozenset()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.backoff = BackoffPolicy(
            config.telegram_relay.retry_initial_seconds,
            config.telegram_relay.retry_max_seconds,
        )
        self.client = build_telegram_user_client(
            settings,
            connection_retries=config.telegram_relay.processing_max_attempts,
            retry_delay=config.telegram_relay.retry_initial_seconds,
        )

    async def run(self, *, authorize: bool = False) -> None:
        await self.relay.start()
        try:
            await self.client.connect()
            await self._ensure_authorized(authorize=authorize)
            resolved = await self._resolve_channels()
            self._source_channel_ids = frozenset(channel_id for channel_id, _ in resolved)
            for channel_id in self._source_channel_ids:
                LOGGER.info(
                    "configured Telegram source resolved",
                    extra={
                        "channel_id": channel_id,
                        "stage": "telegram_source_resolve",
                        "result": "resolved",
                    },
                )
            catch_up_enabled = self.config.telegram_relay.catch_up_on_start
            if catch_up_enabled:
                await self._catch_up(resolved)
            self.client.add_event_handler(
                self._handle_new_message,
                events.NewMessage(chats=[entity for _, entity in resolved]),
            )
            if catch_up_enabled:
                await self._bridge_gap(resolved)
            LOGGER.info(
                "Telegram listener connected",
                extra={"stage": "telegram_connect", "result": "connected"},
            )
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
        if channel_id not in self._source_channel_ids:
            LOGGER.debug(
                "Telegram event outside configured sources ignored",
                extra={
                    "message_id": str(event.message.id),
                    "channel_id": channel_id,
                    "stage": "telegram_receive",
                    "result": "ignored",
                    "error_code": "SOURCE_NOT_CONFIGURED",
                },
            )
            return
        try:
            persisted = await self.relay.persist(_adapt_message(event.message, channel_id))
            if persisted.completed_duplicate:
                result = "completed_duplicate"
            elif not persisted.content_matches:
                result = "content_mismatch"
            elif persisted.queued:
                result = "queued"
            else:
                result = "persisted_pending"
            LOGGER.info(
                "Telegram message persisted",
                extra={
                    "message_id": str(event.message.id),
                    "channel_id": channel_id,
                    "stage": "telegram_receive",
                    "result": result,
                },
            )
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
