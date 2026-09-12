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
    AffiliateShadowPreviewLinkModel,
    AffiliateShadowPreviewModel,
    Base,
    DealModel,
    DeliveryModel,
    SourceMessageModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.providers.base import ProviderError
from promo_bot.relay.models import PersistedMessage
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
        self._handler_tasks: list[asyncio.Task[None]] = []

    async def connect(self) -> None:
        self.lifecycle.append("connect")

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")
        pending = [task for task in self._handler_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, reference: str | int) -> object:
        self.lifecycle.append(f"resolve:{reference}")
        return SimpleNamespace(id=1234567890)

    def add_event_handler(self, callback: Any, _builder: object) -> None:
        async def emit(event: object) -> None:
            await asyncio.sleep(0)
            await callback(event)

        self.lifecycle.append("handler_added")
        self._handler_tasks.extend(asyncio.create_task(emit(event)) for event in self.events)

    def remove_event_handler(self, _callback: object, _builder: object) -> None:
        self.lifecycle.append("handler_removed")

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
        controller,
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
        assert result.processed == 1
        assert result.rejected == 0
        assert result.failed == 0
        assert result.cache_hits == 0
        assert result.previews_created == 1
        assert result.api_calls == 1
        assert result.error_code is None
        assert len(requests) == 1
        async with database.session() as session:
            assert await session.scalar(select(func.count(AffiliateShadowPreviewModel.id))) == 1
            assert await session.scalar(select(func.count(AffiliateShadowPreviewLinkModel.id))) == 1
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
        assert result.processed == 1
        assert result.rejected == 1
        assert result.failed == 0
        assert result.cache_hits == 0
        assert result.previews_created == 0
        assert result.api_calls == 0
        assert requests == []
        async with database.session() as session:
            assert await session.scalar(select(func.count(AffiliateShadowPreviewModel.id))) == 0
    finally:
        await http_client.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_processing_failure_counts_as_safe_rejection_without_partial_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("sensitive upstream detail", request=request)

    monitor, controller, database, http_client = await build_runtime(
        tmp_path,
        monkeypatch,
        message_id=201,
        text=f"Oferta {CANONICAL}",
        handler=handler,
    )
    try:
        result = await monitor.run(authorize=False, bounded=controller)

        assert result.messages_received == 1
        assert result.processed == 1
        assert result.rejected == 1
        assert result.failed == 1
        assert result.previews_created == 0
        assert result.api_calls == 1
        assert result.rejection_codes == ("ALIEXPRESS_RETRY_EXHAUSTED",)
        assert len(requests) == 1
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
        assert second_result.processed == 1
        assert second_result.rejected == 0
        assert second_result.failed == 0
        assert second_result.cache_hits == 1
        assert second_result.previews_created == 1
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
        assert result.processed == 0
        assert result.rejected == 0
        assert result.failed == 0
        assert result.cache_hits == 0
        assert result.previews_created == 0
        assert result.api_calls == 0
    finally:
        await http_client.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_send_budget_counts_dispatch_and_stops_at_limit() -> None:
    controller = ShadowRunController(
        ShadowRunLimits(
            max_messages=2,
            run_seconds=1,
            max_api_calls=2,
            max_send_messages=1,
        )
    )
    controller.mark_ready()

    await controller.before_send_message()

    assert controller.send_messages == 1
    assert await controller.wait_for_stop() == "max_send_messages"
    with pytest.raises(ProviderError, match="SHADOW_SEND_MESSAGE_LIMIT_REACHED"):
        await controller.before_send_message()


@pytest.mark.asyncio
async def test_two_nearly_simultaneous_events_admit_only_one_without_partial_persistence(
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
        message_id=301,
        text=f"Primeira {CANONICAL}",
        handler=handler,
    )
    monitor.client = FakeReadOnlyListenerClient(
        [
            SimpleNamespace(
                chat_id=-1001234567890,
                message=FakeMessage(301, f"Primeira {CANONICAL}"),
            ),
            SimpleNamespace(
                chat_id=-1001234567890,
                message=FakeMessage(302, f"Segunda {CANONICAL}"),
            ),
        ]
    )
    try:
        result = await monitor.run(authorize=False, bounded=controller)

        assert result.messages_received == 1
        assert result.processed == 1
        assert result.previews_created == 1
        assert result.api_calls == 1
        assert len(requests) == 1
        async with database.session() as session:
            preview_count = await session.scalar(
                select(func.count()).select_from(AffiliateShadowPreviewModel)
            )
            source_count = await session.scalar(
                select(func.count()).select_from(SourceMessageModel)
            )
            assert preview_count == 1
            assert source_count == 1
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


class HangingHandlerRelay:
    def __init__(self) -> None:
        self.persist_started = asyncio.Event()

    async def start(self, *, recover: bool = True) -> None:
        assert recover is False

    async def persist(self, _message: object) -> PersistedMessage:
        self.persist_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def join(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class HangingWorkerRelay:
    async def start(self, *, recover: bool = True) -> None:
        assert recover is False

    async def persist(self, _message: object) -> PersistedMessage:
        return PersistedMessage(1, True, False, True, True)

    async def join(self) -> None:
        await asyncio.Event().wait()

    async def stop(self) -> None:
        return None


class ControlledHandlerRelay:
    def __init__(self) -> None:
        self.persist_started = asyncio.Event()
        self.release = asyncio.Event()
        self.persist_completed = False

    async def start(self, *, recover: bool = True) -> None:
        assert recover is False

    async def persist(self, _message: object) -> PersistedMessage:
        self.persist_started.set()
        await self.release.wait()
        self.persist_completed = True
        return PersistedMessage(1, False, True, False, True)

    async def join(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FailingPersistenceRelay:
    async def start(self, *, recover: bool = True) -> None:
        assert recover is False

    async def persist(self, _message: object) -> PersistedMessage:
        raise RuntimeError("sensitive persistence detail")

    async def join(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class HangingConnectClient(FakeReadOnlyListenerClient):
    async def connect(self) -> None:
        self.lifecycle.append("connect")
        await asyncio.Event().wait()

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")


def build_bounded_monitor_with_relay(
    relay: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> TelegramMonitor:
    config = AppConfig(
        source_channels=("-1001234567890",),
        telegram_relay=TelegramRelayConfig(catch_up_on_start=True),
        affiliate_disclosure="fixture",
    )
    client = FakeReadOnlyListenerClient(
        [
            SimpleNamespace(
                chat_id=-1001234567890,
                message=FakeMessage(401, f"Oferta {CANONICAL}"),
            )
        ]
    )
    monkeypatch.setattr(
        "promo_bot.telegram.monitor.utils.get_peer_id",
        lambda _entity: -1001234567890,
    )
    return TelegramMonitor(
        EnvironmentSettings(_env_file=None),
        config,
        relay,
        client=client,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_run_seconds_bounds_connection_setup_before_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = HangingHandlerRelay()
    monitor = build_bounded_monitor_with_relay(relay, monkeypatch)
    client = HangingConnectClient([])
    monitor.client = client
    controller = ShadowRunController(
        ShadowRunLimits(
            max_messages=1,
            run_seconds=0.01,
            max_api_calls=1,
            shutdown_seconds=0.05,
        )
    )

    result = await asyncio.wait_for(monitor.run(bounded=controller), timeout=0.5)

    assert result is not None
    assert result.status == "timeout"
    assert result.stop_reason == "timeout"
    assert result.messages_received == 0
    assert result.api_calls == 0
    assert client.lifecycle == ["connect", "disconnect"]


@pytest.mark.asyncio
async def test_shutdown_timeout_reports_stuck_handler_without_waiting_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = build_bounded_monitor_with_relay(HangingHandlerRelay(), monkeypatch)
    controller = ShadowRunController(
        ShadowRunLimits(
            max_messages=1,
            run_seconds=1,
            max_api_calls=1,
            shutdown_seconds=0.01,
        )
    )

    result = await asyncio.wait_for(monitor.run(bounded=controller), timeout=0.5)

    assert result.status == "shutdown_timeout"
    assert result.error_code == "TELEGRAM_HANDLER_SHUTDOWN_TIMEOUT"
    assert result.messages_received == 1
    assert result.processed == 0
    assert result.rejected == 1
    assert result.failed == 1
    assert result.previews_created == 0


@pytest.mark.asyncio
async def test_shutdown_timeout_reports_stuck_worker_without_waiting_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = build_bounded_monitor_with_relay(HangingWorkerRelay(), monkeypatch)
    controller = ShadowRunController(
        ShadowRunLimits(
            max_messages=1,
            run_seconds=1,
            max_api_calls=1,
            shutdown_seconds=0.01,
        )
    )

    result = await asyncio.wait_for(monitor.run(bounded=controller), timeout=0.5)

    assert result.status == "shutdown_timeout"
    assert result.error_code == "RELAY_WORKER_SHUTDOWN_TIMEOUT"
    assert result.messages_received == 1
    assert result.processed == 0
    assert result.rejected == 1
    assert result.failed == 1
    assert result.previews_created == 0


@pytest.mark.asyncio
async def test_persistence_failure_is_terminal_rejection_without_partial_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = build_bounded_monitor_with_relay(FailingPersistenceRelay(), monkeypatch)
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=1, run_seconds=1, max_api_calls=1)
    )

    result = await monitor.run(bounded=controller)

    assert result.status == "limit_reached"
    assert result.messages_received == 1
    assert result.processed == 1
    assert result.rejected == 1
    assert result.failed == 1
    assert result.previews_created == 0
    assert result.api_calls == 0
    assert result.rejection_codes == ("TELEGRAM_EVENT_PERSIST_FAILED",)
    assert result.error_code is None


@pytest.mark.asyncio
async def test_external_cancellation_drains_accepted_handler_then_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = ControlledHandlerRelay()
    monitor = build_bounded_monitor_with_relay(relay, monkeypatch)
    controller = ShadowRunController(
        ShadowRunLimits(
            max_messages=2,
            run_seconds=60,
            max_api_calls=1,
            shutdown_seconds=0.2,
        )
    )
    task = asyncio.create_task(monitor.run(bounded=controller))
    await asyncio.wait_for(relay.persist_started.wait(), timeout=0.2)

    task.cancel()

    async def release_handler() -> None:
        await asyncio.sleep(0.01)
        relay.release.set()

    release_task = asyncio.create_task(release_handler())
    with pytest.raises(asyncio.CancelledError):
        await task
    await release_task

    assert relay.persist_completed is True
    client = monitor.client
    assert client.lifecycle.index("handler_removed") < client.lifecycle.index("disconnect")
