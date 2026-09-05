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
    TelegramMessageReference,
    TelegramMonitor,
    TelegramOneShotReader,
    TelethonReadOnlyClient,
    _adapt_message,
    _channel_reference,
    _ensure_external_session_path,
    authorize_telegram_session,
    parse_telegram_message_link,
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


@dataclass
class FakeResolvedChannel:
    reference: str | int
    chat_id: int
    entity: object


@dataclass
class FakeReadOnlyClient:
    resolved: dict[str | int, FakeResolvedChannel]
    message: FakeMessage | None
    lifecycle: list[str] = field(default_factory=list)
    resolutions: list[str | int] = field(default_factory=list)
    fetches: list[tuple[object, int]] = field(default_factory=list)

    async def connect(self) -> None:
        self.lifecycle.append("connect")

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")

    async def is_user_authorized(self) -> bool:
        self.lifecycle.append("authorized")
        return True

    async def resolve_channel(self, reference: str | int) -> FakeResolvedChannel:
        self.resolutions.append(reference)
        return self.resolved[reference]

    async def get_message(self, entity: object, message_id: int) -> FakeMessage | None:
        self.fetches.append((entity, message_id))
        return self.message

    async def send_message(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("send must not be reachable from the read-only boundary")

    async def edit_message(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("edit must not be reachable from the read-only boundary")

    async def forward_messages(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("forward must not be reachable from the read-only boundary")

    async def send_read_acknowledge(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("mark-read must not be reachable from the read-only boundary")


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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://t.me/c/1234567890/77",
            TelegramMessageReference(message_id=77, chat_id=-1001234567890),
        ),
        (
            "https://t.me/ofertas_publicas/88",
            TelegramMessageReference(message_id=88, username="ofertas_publicas"),
        ),
    ],
)
def test_telegram_message_links_are_parsed_without_network(
    url: str, expected: TelegramMessageReference
) -> None:
    assert parse_telegram_message_link(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://t.me/c/123/1",
        "https://telegram.me/c/123/1",
        "https://t.me/c/123/0",
        "https://t.me/+invite/1",
        "https://t.me/ofertas_publicas/1?single=1",
        "https://t.me/ofertas_publicas/1#fragment",
    ],
)
def test_telegram_message_link_parser_rejects_ambiguous_or_noncanonical_urls(url: str) -> None:
    with pytest.raises(ValueError, match="TELEGRAM_MESSAGE_LINK_INVALID"):
        parse_telegram_message_link(url)


@pytest.mark.asyncio
async def test_one_shot_reader_fetches_exact_allowlisted_private_message_without_writes() -> None:
    entity = object()
    client = FakeReadOnlyClient(
        resolved={
            -1001234567890: FakeResolvedChannel(
                reference=-1001234567890,
                chat_id=-1001234567890,
                entity=entity,
            )
        },
        message=FakeMessage(77, own=False, text="oferta"),
    )
    reader = TelegramOneShotReader(client, source_channels=("-1001234567890",))

    message = await reader.fetch(TelegramMessageReference(message_id=77, chat_id=-1001234567890))

    assert message.message_id == 77
    assert message.channel_id == "-1001234567890"
    assert client.lifecycle == ["connect", "authorized", "disconnect"]
    assert client.resolutions == [-1001234567890]
    assert client.fetches == [(entity, 77)]


@pytest.mark.asyncio
async def test_one_shot_reader_rejects_public_username_outside_allowlist_before_resolution() -> (
    None
):
    client = FakeReadOnlyClient(resolved={}, message=None)
    reader = TelegramOneShotReader(client, source_channels=("canal_permitido",))

    with pytest.raises(ValueError, match="TELEGRAM_SOURCE_NOT_ALLOWLISTED"):
        await reader.fetch(TelegramMessageReference(message_id=9, username="outro_canal"))

    assert client.resolutions == []
    assert client.fetches == []
    assert client.lifecycle == ["connect", "authorized", "disconnect"]


@pytest.mark.asyncio
async def test_one_shot_reader_accepts_allowlisted_public_username_case_insensitively() -> None:
    entity = object()
    client = FakeReadOnlyClient(
        resolved={
            "ofertas_publicas": FakeResolvedChannel(
                reference="ofertas_publicas",
                chat_id=-1002223334445,
                entity=entity,
            )
        },
        message=FakeMessage(91, own=False),
    )
    reader = TelegramOneShotReader(client, source_channels=("@Ofertas_Publicas",))

    message = await reader.fetch(
        TelegramMessageReference(message_id=91, username="OFERTAS_PUBLICAS")
    )

    assert message.channel_id == "-1002223334445"
    assert client.resolutions == ["ofertas_publicas"]
    assert client.fetches == [(entity, 91)]


@pytest.mark.asyncio
async def test_telethon_read_only_adapter_uses_get_messages_without_write_surfaces() -> None:
    entity = object()

    class RawClient:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def connect(self) -> None:
            self.calls.append("connect")

        async def disconnect(self) -> None:
            self.calls.append("disconnect")

        async def is_user_authorized(self) -> bool:
            self.calls.append("authorized")
            return True

        async def get_entity(self, reference: str | int) -> object:
            self.calls.append(("get_entity", reference))
            return entity

        async def get_messages(self, target: object, *, ids: int) -> FakeMessage:
            self.calls.append(("get_messages", target, ids))
            return FakeMessage(ids, own=False)

        async def send_message(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("send called")

        async def edit_message(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("edit called")

        async def forward_messages(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("forward called")

        async def send_read_acknowledge(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("mark-read called")

    raw = RawClient()
    client = TelethonReadOnlyClient(raw, peer_id=lambda _entity: -1001234567890)

    await client.connect()
    assert await client.is_user_authorized()
    channel = await client.resolve_channel(-1001234567890)
    message = await client.get_message(channel.entity, 77)
    await client.disconnect()

    assert message is not None and message.id == 77
    assert raw.calls == [
        "connect",
        "authorized",
        ("get_entity", -1001234567890),
        ("get_messages", entity, 77),
        "disconnect",
    ]


@pytest.mark.asyncio
async def test_explicit_session_authorization_connects_signs_in_and_disconnects_only() -> None:
    class AuthorizationClient:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def connect(self) -> None:
            self.calls.append("connect")

        async def disconnect(self) -> None:
            self.calls.append("disconnect")

        async def is_user_authorized(self) -> bool:
            self.calls.append("authorized")
            return False

        async def send_code_request(self, phone: str) -> None:
            self.calls.append(("send_code_request", phone))

        async def sign_in(self, **kwargs: str) -> None:
            self.calls.append(("sign_in", kwargs))

        async def run_until_disconnected(self) -> None:
            raise AssertionError("listener must not start during session authorization")

        async def send_message(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("message send must not occur during session authorization")

    client = AuthorizationClient()

    created = await authorize_telegram_session(
        client,
        phone_prompt=lambda _prompt: "+5511999999999",
        secret_prompt=lambda _prompt: "12345",
    )

    assert created is True
    assert client.calls == [
        "connect",
        "authorized",
        ("send_code_request", "+5511999999999"),
        ("sign_in", {"phone": "+5511999999999", "code": "12345"}),
        "disconnect",
    ]


def test_telethon_adapter_reads_entities_and_buttons_without_clicking() -> None:
    class Button:
        url = "https://www.kabum.com.br/produto/123"

        @staticmethod
        def click() -> None:
            raise AssertionError("button click must not be called")

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
