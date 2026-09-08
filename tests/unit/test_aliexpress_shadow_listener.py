from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionSafety,
    AliExpressMessageConversionService,
)
from promo_bot.affiliate.aliexpress_shadow_listener import (
    AliExpressShadowMessageProcessor,
    ShadowRunController,
    ShadowRunLimits,
)
from promo_bot.config import EnvironmentSettings
from promo_bot.config.schema import AppConfig, TelegramRelayConfig
from promo_bot.database.models import (
    AffiliateShadowPreviewModel,
    Base,
    DealModel,
    DeliveryModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.relay.service import RelayProcessor
from promo_bot.telegram.monitor import TelegramMonitor, TelethonReadOnlyEventClient

NOW = datetime(2026, 9, 8, 15, tzinfo=UTC)
APP_KEY = "listener-app-key"
APP_SECRET = "listener-app-secret"
TRACKING_ID = "listener-tracking"
PRODUCT_ID = "1005000000000001"
CANONICAL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
AFFILIATE_LINK = "https://s.click.aliexpress.com/e/listener-fixture"


class FakeMessage:
    def __init__(self, message_id: int, text: str) -> None:
        self.id = message_id
        self.date = NOW
        self.raw_text = text
        self.out = False
        self.buttons = None

    @staticmethod
    def get_entities_text() -> list[tuple[object, str]]:
        return []


class FakeReadOnlyListenerClient:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.lifecycle: list[str] = []
        self._emissions: list[asyncio.Task[None]] = []

    async def connect(self) -> None:
        self.lifecycle.append("connect")

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, reference: str | int) -> object:
        self.lifecycle.append(f"resolve:{reference}")
        return SimpleNamespace(id=1234567890)

    def add_event_handler(self, callback: Any, _builder: object) -> None:
        async def emit() -> None:
            await asyncio.sleep(0)
            for event in self.events:
                await callback(event)

        self._emissions.append(asyncio.create_task(emit()))

    async def run_until_disconnected(self) -> None:
        raise AssertionError("bounded shadow listener must not wait indefinitely")

    async def iter_messages(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("bounded shadow listener must not perform catch-up")


def link_response() -> dict[str, object]:
    return {
        "code": "0",
        "aliexpress_affiliate_link_generate_response": {
            "resp_result": {
                "result": {
                    "total_result_count": "1",
                    "promotion_links": [
                        {
                            "promotion_link": AFFILIATE_LINK,
                            "source_value": CANONICAL,
                        }
                    ],
                },
                "resp_code": "200",
                "resp_msg": "success",
            }
        },
        "request_id": "listener-fixture-request",
    }


async def build_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    message_id: int,
    text: str,
    handler: Any,
) -> tuple[TelegramMonitor, ShadowRunController, Any, httpx.AsyncClient]:
    database = create_affiliate_shadow_database(tmp_path / "listener.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=1, run_seconds=1, max_api_calls=1)
    )
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        follow_redirects=False,
    )
    api_client = AliExpressAffiliateApiClient(
        AliExpressHttpTransport(
            http_client,
            max_attempts=1,
            durable_retry=True,
            before_send=controller.before_api_call,
        ),
        request_builder=AliExpressTopRequestBuilder(APP_KEY, APP_SECRET),
        live_enabled=True,
    )
    relay_config = TelegramRelayConfig(catch_up_on_start=True, queue_max_size=1)
    conversion = AliExpressMessageConversionService(
        database,
        api_client,
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        tracking_id=TRACKING_ID,
        safety=AliExpressConversionSafety(
            dry_run=True,
            publish_real_deals=False,
            publish_without_affiliate=False,
            search_enabled=False,
        ),
        clock=lambda: NOW,
    )
    processor = AliExpressShadowMessageProcessor(
        database,
        RelayProcessor(database, relay_config, clock=lambda: NOW),
        conversion,
        clock=lambda: NOW,
    )
    relay = DurableRelayQueue(
        database,
        relay_config,
        processor=processor,
        clock=lambda: NOW,
    )
    event = SimpleNamespace(
        chat_id=-1001234567890,
        message=FakeMessage(message_id, text),
    )
    client = FakeReadOnlyListenerClient([event])
    config = AppConfig(
        source_channels=("-1001234567890",),
        telegram_relay=relay_config,
        affiliate_disclosure="fixture",
    )
    monkeypatch.setattr(
        "promo_bot.telegram.monitor.utils.get_peer_id",
        lambda _entity: -1001234567890,
    )
    monitor = TelegramMonitor(
        EnvironmentSettings(_env_file=None),
        config,
        relay,
        client=client,
        clock=lambda: NOW,
    )
    return monitor, controller, database, http_client


@pytest.mark.asyncio
async def test_bounded_shadow_listener_processes_one_new_message_and_one_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=link_response(), request=request)

    monitor, controller, database, http_client = await build_runtime(
        tmp_path,
        monkeypatch,
        message_id=101,
        text=f"Oferta {CANONICAL}",
        handler=handler,
    )
    try:
        result = await monitor.run(authorize=False, bounded=controller)

        assert result.status == "limit_reached"
        assert result.messages_received == 1
        assert result.api_calls == 1
        assert len(requests) == 1
        async with database.session() as session:
            assert await session.scalar(select(func.count(AffiliateShadowPreviewModel.id))) == 1
            assert await session.scalar(select(func.count(DealModel.id))) == 0
            assert await session.scalar(select(func.count(DeliveryModel.id))) == 0
    finally:
        await http_client.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_local_rejection_counts_message_but_not_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=link_response(), request=request)

    monitor, controller, database, http_client = await build_runtime(
        tmp_path,
        monkeypatch,
        message_id=102,
        text="Oferta https://s.click.aliexpress.com/e/unsupported",
        handler=handler,
    )
    try:
        result = await monitor.run(authorize=False, bounded=controller)

        assert result.messages_received == 1
        assert result.api_calls == 0
        assert requests == []
        async with database.session() as session:
            assert await session.scalar(select(func.count(AffiliateShadowPreviewModel.id))) == 0
    finally:
        await http_client.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_affiliate_cache_hit_does_not_consume_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_requests: list[httpx.Request] = []

    async def first_handler(request: httpx.Request) -> httpx.Response:
        first_requests.append(request)
        return httpx.Response(200, json=link_response(), request=request)

    first_monitor, first_controller, first_database, first_http = await build_runtime(
        tmp_path,
        monkeypatch,
        message_id=201,
        text=f"Primeira {CANONICAL}",
        handler=first_handler,
    )
    try:
        first_result = await first_monitor.run(authorize=False, bounded=first_controller)
        assert first_result.api_calls == 1
        assert len(first_requests) == 1
    finally:
        await first_http.aclose()
        await first_database.dispose()

    async def forbidden_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"cache hit attempted HTTP: {request.method}")

    second_monitor, second_controller, database, second_http = await build_runtime(
        tmp_path,
        monkeypatch,
        message_id=202,
        text=f"Segunda {CANONICAL}",
        handler=forbidden_handler,
    )
    try:
        second_result = await second_monitor.run(authorize=False, bounded=second_controller)

        assert second_result.messages_received == 1
        assert second_result.api_calls == 0
        async with database.session() as session:
            result = await session.execute(
                select(AffiliateShadowPreviewModel).where(
                    AffiliateShadowPreviewModel.source_message_id == 2
                )
            )
            preview = result.scalar_one()
            assert preview.cache_hit is True
    finally:
        await second_http.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_bounded_shadow_listener_times_out_normally_without_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected AliExpress request: {request.method}")

    monitor, _controller, database, http_client = await build_runtime(
        tmp_path,
        monkeypatch,
        message_id=103,
        text=f"Oferta {CANONICAL}",
        handler=handler,
    )
    monitor.client = FakeReadOnlyListenerClient([])
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=1, run_seconds=0.01, max_api_calls=1)
    )
    try:
        result = await monitor.run(authorize=False, bounded=controller)

        assert result.status == "timeout"
        assert result.messages_received == 0
        assert result.api_calls == 0
    finally:
        await http_client.aclose()
        await database.dispose()


def test_read_only_listener_client_has_no_write_or_click_surface() -> None:
    raw = SimpleNamespace(
        send_message=object(),
        edit_message=object(),
        forward_messages=object(),
        send_read_acknowledge=object(),
        click=object(),
    )
    client = TelethonReadOnlyEventClient(raw)

    for forbidden in (
        "send_message",
        "edit_message",
        "forward_messages",
        "send_read_acknowledge",
        "click",
    ):
        assert not hasattr(client, forbidden)
