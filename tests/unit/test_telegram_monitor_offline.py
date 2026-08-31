from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from telethon.tl.types import MessageEntityTextUrl  # type: ignore[import-untyped]

from promo_bot.config.schema import AppConfig, TelegramRelayConfig
from promo_bot.domain.enums import LinkSource
from promo_bot.observability import configure_logging
from promo_bot.relay.models import IncomingMessage, PersistedMessage
from promo_bot.telegram.monitor import (
    TelegramMonitor,
    _adapt_message,
    _channel_reference,
    _ensure_external_session_path,
)

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


@dataclass
class FakeRelay:
    messages: list[IncomingMessage] = field(default_factory=list)
    lifecycle: list[str] = field(default_factory=list)

    async def persist(self, message: IncomingMessage) -> PersistedMessage:
        self.messages.append(message)
        return PersistedMessage(1, True, False, True, True)

    async def start(self) -> None:
        self.lifecycle.append("start")

    async def stop(self) -> None:
        self.lifecycle.append("stop")


@dataclass
class FakeClient:
    lifecycle: list[str] = field(default_factory=list)
    registered: list[tuple[object, object]] = field(default_factory=list)
    prohibited_calls: list[str] = field(default_factory=list)

    async def connect(self) -> None:
        self.lifecycle.append("connect")

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")

    async def is_user_authorized(self) -> bool:
        return True

    def add_event_handler(self, callback: object, builder: object) -> None:
        self.registered.append((callback, builder))

    async def run_until_disconnected(self) -> None:
        self.lifecycle.append("listen")

    async def send_message(self, *_args: object, **_kwargs: object) -> None:
        self.prohibited_calls.append("send_message")

    async def send_read_acknowledge(self, *_args: object, **_kwargs: object) -> None:
        self.prohibited_calls.append("mark_read")


class FakeMessage:
    def __init__(self, message_id: int, *, own: bool, text: str = "fixture") -> None:
        self.id = message_id
        self.date = NOW
        self.raw_text = text
        self.out = own
        self.buttons: None = None

    @staticmethod
    def get_entities_text() -> list[tuple[object, str]]:
        return []


def make_event_monitor(
    channel_ids: frozenset[str],
) -> tuple[TelegramMonitor, FakeRelay, FakeClient]:
    monitor = object.__new__(TelegramMonitor)
    relay = FakeRelay()
    client = FakeClient()
    monitor.relay = relay  # type: ignore[assignment]
    monitor.client = client  # type: ignore[assignment]
    monitor._source_channel_ids = channel_ids
    return monitor, relay, client


def test_session_path_inside_workspace_is_rejected() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    with pytest.raises(ValueError, match="outside the Git workspace"):
        _ensure_external_session_path(repository_root / "runtime" / "monitor.session")


def test_external_session_parent_is_created(tmp_path: Path) -> None:
    path = tmp_path / "telegram" / "monitor.session"

    _ensure_external_session_path(path)

    assert path.parent.is_dir()


def test_numeric_channel_ids_are_passed_to_telethon_as_integers() -> None:
    assert _channel_reference("-100123456") == -100123456
    assert _channel_reference("@configured_channel") == "@configured_channel"


def test_telethon_adapter_reads_entities_and_buttons_without_clicking() -> None:
    class Button:
        url = "https://www.kabum.com.br/produto/123"

    class Message:
        id = 10
        date = datetime(2026, 8, 27, 12, tzinfo=UTC)
        raw_text = "oferta"
        buttons: ClassVar[list[list[Button]]] = [[Button()]]

        @staticmethod
        def get_entities_text() -> list[tuple[object, str]]:
            entity = MessageEntityTextUrl(
                offset=0,
                length=6,
                url="https://www.amazon.com.br/dp/B0ABCDEFGH",
            )
            return [(entity, "oferta")]

    adapted = _adapt_message(Message(), "channel")  # type: ignore[arg-type]

    assert [link.source for link in adapted.links] == [
        LinkSource.ENTITY_TEXT_URL,
        LinkSource.BUTTON,
    ]


@pytest.mark.asyncio
async def test_message_received_from_another_account_is_persisted() -> None:
    monitor, relay, client = make_event_monitor(frozenset({"-1001"}))
    event = SimpleNamespace(chat_id=-1001, message=FakeMessage(10, own=False))

    await monitor._handle_new_message(event)  # type: ignore[arg-type]

    assert [message.message_id for message in relay.messages] == [10]
    assert client.prohibited_calls == []


@pytest.mark.asyncio
async def test_message_published_by_the_authenticated_account_is_persisted() -> None:
    monitor, relay, client = make_event_monitor(frozenset({"-1001"}))
    event = SimpleNamespace(chat_id=-1001, message=FakeMessage(11, own=True))

    await monitor._handle_new_message(event)  # type: ignore[arg-type]

    assert [message.message_id for message in relay.messages] == [11]
    assert client.prohibited_calls == []


@pytest.mark.asyncio
async def test_message_outside_configured_sources_is_ignored() -> None:
    monitor, relay, client = make_event_monitor(frozenset({"-1001"}))
    event = SimpleNamespace(chat_id=-2002, message=FakeMessage(12, own=False))

    await monitor._handle_new_message(event)  # type: ignore[arg-type]

    assert relay.messages == []
    assert client.prohibited_calls == []


@pytest.mark.asyncio
async def test_listener_registers_allowlisted_sources_without_direction_filter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    monitor, relay, client = make_event_monitor(frozenset())
    monitor.config = AppConfig(
        source_channels=("configured",),
        telegram_relay=TelegramRelayConfig(catch_up_on_start=False),
        affiliate_disclosure="fixture",
    )

    async def resolve_channels() -> list[tuple[str, object]]:
        return [("-1001", "configured-entity")]

    captured: dict[str, Any] = {}

    def new_message(**kwargs: object) -> object:
        captured.update(kwargs)
        return "builder"

    monitor._resolve_channels = resolve_channels  # type: ignore[method-assign]
    monkeypatch.setattr("promo_bot.telegram.monitor.events.NewMessage", new_message)

    await monitor.run()

    assert captured == {"chats": ["configured-entity"]}
    assert monitor._source_channel_ids == frozenset({"-1001"})
    assert relay.lifecycle == ["start", "stop"]
    assert client.lifecycle == ["connect", "listen", "disconnect"]
    assert len(client.registered) == 1
    assert client.prohibited_calls == []
    logs = capsys.readouterr().err
    assert '"stage":"telegram_connect"' in logs
    assert '"stage":"telegram_source_resolve"' in logs


@pytest.mark.asyncio
async def test_persistence_log_does_not_include_message_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    monitor, _, _ = make_event_monitor(frozenset({"-1001"}))
    event = SimpleNamespace(
        chat_id=-1001,
        message=FakeMessage(13, own=True, text="DO_NOT_LOG_THIS_FULL_MESSAGE token=secret"),
    )

    await monitor._handle_new_message(event)  # type: ignore[arg-type]

    logs = capsys.readouterr().err
    assert '"message":"Telegram message persisted"' in logs
    assert '"result":"queued"' in logs
    assert "DO_NOT_LOG_THIS_FULL_MESSAGE" not in logs
    assert "token=secret" not in logs
