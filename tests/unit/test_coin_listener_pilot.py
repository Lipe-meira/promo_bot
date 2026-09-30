from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from promo_bot.affiliate.aliexpress_shadow_listener import (
    AliExpressShadowMessageProcessor,
    ShadowRunController,
    ShadowRunLimits,
)
from promo_bot.cli import main
from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import Base
from promo_bot.database.repositories import SourceMessageRepository
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.domain.enums import LinkSource
from promo_bot.relay.models import ExtractedLink, IncomingMessage
from promo_bot.relay.queue import DurableRelayQueue

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
SHORT = "https://a.aliexpress.com/_c2uCPBX1"


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_channels: ["-1001234567890"]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "-1009876543210"
      kind: private_channel
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    return path


def _argv(config_path: Path, db_path: Path) -> list[str]:
    return [
        "aliexpress",
        "shadow-auto-deliver",
        "--config",
        str(config_path),
        "--shadow-database",
        str(db_path),
        "--destination",
        "private-test",
        "--include-coin-shorts",
        "--max-messages",
        "1",
        "--run-seconds",
        "1",
        "--max-api-calls",
        "1",
        "--max-links-per-message",
        "1",
        "--max-send-messages",
        "1",
    ]


def _settings(*, coin_gate: bool) -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="synthetic-hash",
        telegram_bot_token="123:synthetic-bot-token",
        aliexpress_app_key="synthetic-key",
        aliexpress_app_secret="synthetic-secret",
        aliexpress_tracking_id="synthetic-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_auto_delivery_enabled=True,
        aliexpress_coin_short_shadow_enabled=coin_gate,
        dry_run=True,
    )


def test_coin_pilot_gate_closes_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _config_file(tmp_path)
    db_path = tmp_path / "pilot.sqlite3"
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=False))

    async def forbidden_runtime(*_args: object) -> None:
        pytest.fail("runtime constructed behind a closed gate")

    monkeypatch.setattr("promo_bot.cli.run_aliexpress_shadow_auto_delivery", forbidden_runtime)
    assert main(_argv(config_path, db_path)) == 2
    assert "ALIEXPRESS_COIN_AUTO_PILOT_DISABLED" in capsys.readouterr().err
    assert not db_path.exists()


def test_second_cli_instance_fails_before_runtime_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from promo_bot.affiliate.shadow_listener_lock import ShadowListenerLock

    config_path = _config_file(tmp_path)
    db_path = tmp_path / "pilot.sqlite3"
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=True))

    async def forbidden_runtime(*_args: object) -> None:
        pytest.fail("runtime constructed for a second instance")

    monkeypatch.setattr("promo_bot.cli.run_aliexpress_shadow_auto_delivery", forbidden_runtime)
    with ShadowListenerLock(db_path):
        assert main(_argv(config_path, db_path)) == 2
    assert "SHADOW_LISTENER_ALREADY_ACTIVE" in capsys.readouterr().err
    assert not db_path.exists()


@pytest.mark.parametrize(
    "canonical_tracking_state,error_code",
    [
        ("exact", None),
        ("absent", "ALIEXPRESS_TRACKING_UNCONFIRMED"),
        ("invalid", "ALIEXPRESS_TRACKING_RESPONSE_INVALID"),
        ("divergent", "ALIEXPRESS_TRACKING_MISMATCH"),
    ],
)
def test_real_entrypoint_coin_short_sends_once_without_resolving_or_replaying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    canonical_tracking_state: str,
    error_code: str | None,
) -> None:
    config_path = _config_file(tmp_path)
    db_path = tmp_path / "pilot.sqlite3"
    content = f"Oferta R$ 10\nCupom da origem\n{SHORT}"
    generated = "https://s.click.aliexpress.com/e/_Zy98Xw7"
    canonical = "https://www.aliexpress.com/item/1005000000000001.html"
    canonical_source = "https://pt.aliexpress.com/item/1005000000000001.html"
    canonical_generated = "https://s.click.aliexpress.com/e/_Aa12Bb3"
    requests: list[httpx.Request] = []
    sends: list[str] = []
    canonical_sends: list[str] = []

    class Message:
        id = 75
        date = NOW
        raw_text = content
        out = False
        buttons = None
        media = None

        @staticmethod
        def get_entities_text() -> list[tuple[object, str]]:
            return []

    class CanonicalMessage(Message):
        id = 76
        raw_text = f"Oferta canônica\n{canonical}"

    class Listener:
        def __init__(self) -> None:
            self.tasks: list[asyncio.Task[None]] = []

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)

        async def is_user_authorized(self) -> bool:
            return True

        async def get_entity(self, _reference: object) -> object:
            return SimpleNamespace(id=1234567890)

        def add_event_handler(self, callback: object, _builder: object) -> None:
            async def emit(message: object) -> None:
                await callback(SimpleNamespace(chat_id=-1001234567890, message=message))

            self.tasks.extend(
                asyncio.create_task(emit(message)) for message in (Message(), CanonicalMessage())
            )

        def remove_event_handler(self, _callback: object, _builder: object) -> None:
            return None

    class CanonicalBot:
        def __init__(self, _token: str) -> None:
            pass

        async def __aenter__(self) -> CanonicalBot:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_chat(self, _chat_id: str) -> object:
            return SimpleNamespace(
                id=-1009876543210, type="channel", username=None, active_usernames=()
            )

        async def send_text(self, _chat_id: str, text: str) -> str:
            canonical_sends.append(text)
            return "78"

    class CoinBot:
        def __init__(self, _token: str) -> None:
            pass

        async def __aenter__(self) -> CoinBot:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def inspect_private_channel(self, _chat_id: str) -> object:
            return SimpleNamespace(
                id=-1009876543210,
                type="channel",
                username=None,
                active_usernames=(),
                bot_membership_status="administrator",
                can_post_messages=True,
            )

        async def send_text(self, _chat_id: str, text: str) -> str:
            sends.append(text)
            return "77"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        form = parse_qs(request.content.decode())
        assert request.url.params["method"] == "aliexpress.affiliate.link.generate"
        source = form["source_values"][0]
        assert source in {SHORT, canonical_source}
        returned_tracking: dict[str, object] = {"tracking_id": "synthetic-tracking"}
        if source == canonical_source:
            if canonical_tracking_state == "absent":
                returned_tracking = {}
            elif canonical_tracking_state == "invalid":
                returned_tracking = {"tracking_id": 123}
            elif canonical_tracking_state == "divergent":
                returned_tracking = {"tracking_id": "other-synthetic-tracking"}
        return httpx.Response(
            200,
            json={
                "code": "0",
                "aliexpress_affiliate_link_generate_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "result": {
                            "promotion_links": [
                                {
                                    "promotion_link": (
                                        generated if source == SHORT else canonical_generated
                                    ),
                                    **(
                                        {}
                                        if source == SHORT
                                        else {"source_value": canonical_source}
                                    ),
                                }
                            ],
                            **returned_tracking,
                        },
                    }
                },
            },
            request=request,
        )

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=True))
    monkeypatch.setattr("promo_bot.cli.build_telegram_user_client", lambda *_a, **_k: Listener())
    monkeypatch.setattr(
        "promo_bot.cli.build_offline_safe_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
        ),
    )
    monkeypatch.setattr("promo_bot.cli.ShadowBotTransport", CanonicalBot)
    monkeypatch.setattr("promo_bot.telegram.coin_shadow_bot.CoinShadowBotTransport", CoinBot)
    monkeypatch.setattr(
        "promo_bot.cli.AliExpressShortLinkResolver",
        lambda **_kwargs: pytest.fail("pilot constructed a redirect resolver"),
    )
    monkeypatch.setattr(
        "promo_bot.telegram.monitor.utils.get_peer_id", lambda _entity: -1001234567890
    )

    async def seed_crashed_source() -> None:
        await upgrade_database_async(shadow_database_url(db_path))
        database = create_affiliate_shadow_database(db_path)
        try:
            old_message = IncomingMessage(
                platform="telegram",
                message_id=74,
                channel_id="-1001234567890",
                occurred_at=NOW,
                original_text=SHORT,
                links=(ExtractedLink(SHORT, LinkSource.TEXT, 0),),
            )
            persisted = await DurableRelayQueue(
                database, TelegramRelayConfig()
            ).persist_without_enqueue(old_message)
            earlier = datetime.now(UTC) - timedelta(minutes=10)
            async with database.session() as session:
                assert (
                    await SourceMessageRepository(session).claim(
                        persisted.internal_id,
                        now=earlier,
                        lease_until=earlier + timedelta(minutes=5),
                        max_attempts=1,
                    )
                    is not None
                )
        finally:
            await database.dispose()

    asyncio.run(seed_crashed_source())

    argv = _argv(config_path, db_path)
    for flag in ("--max-messages", "--max-api-calls", "--max-send-messages"):
        argv[argv.index(flag) + 1] = "2"
    assert main(argv) == 0
    captured = capsys.readouterr()
    first = json.loads(captured.out)
    assert first["rejection_codes"] == ([] if error_code is None else [error_code])
    assert first["api_calls"] == 2
    expected_sends = 2 if error_code is None else 1
    assert first["send_messages"] == first["deliveries_sent"] == expected_sends, first
    assert sends == [content.replace(SHORT, generated)]
    assert canonical_sends == (
        [CanonicalMessage.raw_text.replace(canonical, canonical_generated)]
        if error_code is None
        else []
    )
    assert len(requests) == 2
    for forbidden in (
        SHORT,
        generated,
        canonical,
        canonical_generated,
        "synthetic-tracking",
        "other-synthetic-tracking",
        "Cupom da origem",
    ):
        assert forbidden not in captured.out
        assert forbidden not in captured.err
        assert forbidden not in caplog.text
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM deals").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM deliveries").fetchone() == (0,)
        assert connection.execute(
            "SELECT processing_status,error_code FROM source_messages WHERE message_id='74'"
        ).fetchone() == ("FAILED_PERMANENT", "SHADOW_PILOT_OUTCOME_UNCERTAIN")

    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["api_calls"] == second["send_messages"] == second["deliveries_sent"] == 0
    assert len(requests) == 2
    assert len(sends) == 1
    assert len(canonical_sends) == (1 if error_code is None else 0)


@pytest.mark.parametrize(
    ("text", "urls"),
    [
        (f"{SHORT}\nhttps://example.com/other", (SHORT, "https://example.com/other")),
        (
            "https://a.aliexpress.com/_c2uCPBX1?extra=1",
            ("https://a.aliexpress.com/_c2uCPBX1?extra=1",),
        ),
        (
            "https://s.click.aliexpress.com/e/_Ab12Cd3#fragment",
            ("https://s.click.aliexpress.com/e/_Ab12Cd3#fragment",),
        ),
        (SHORT, (SHORT, "https://example.com/other")),
    ],
)
@pytest.mark.asyncio
async def test_coin_like_message_rejects_without_canonical_fallback_or_top(
    tmp_path: Path, text: str, urls: tuple[str, ...]
) -> None:
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewService

    database = create_affiliate_shadow_database(tmp_path / "pilot.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=1, run_seconds=5, max_api_calls=1, max_send_messages=1)
    )

    class ForbiddenCanonical:
        async def process(self, _id: int) -> None:
            pytest.fail("coin candidate entered canonical relay")

        async def convert(self, _id: int) -> None:
            pytest.fail("coin candidate entered canonical conversion")

    class ForbiddenGeneration:
        async def generate(self, _url: str) -> None:
            pytest.fail("invalid coin message called TOP")

    class ForbiddenDelivery:
        async def deliver_automatic(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("invalid coin message sent to Telegram")

    message = IncomingMessage(
        platform="telegram",
        message_id=81,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=text,
        links=tuple(ExtractedLink(url, LinkSource.TEXT, n) for n, url in enumerate(urls)),
    )
    try:
        persisted = await DurableRelayQueue(
            database, TelegramRelayConfig()
        ).persist_without_enqueue(message)
        processor = AliExpressShadowMessageProcessor(
            database,
            ForbiddenCanonical(),
            ForbiddenCanonical(),
            controller,
            clock=lambda: NOW,
            destination="private-test",
            delivery=ForbiddenCanonical(),
            delivery_authorization=object(),
            coin_preview=CoinShadowPreviewService(
                database, ForbiddenGeneration(), app_secret="synthetic-secret", clock=lambda: NOW
            ),
            coin_delivery=ForbiddenDelivery(),
        )
        await processor.process(persisted.internal_id)
        assert controller.api_calls == controller.send_messages == 0
        assert controller.rejected == 1
        async with database.session() as session:
            source = await SourceMessageRepository(session).get(persisted.internal_id)
            assert source is not None
            assert source.processing_status == "FAILED_PERMANENT"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_uncertain_coin_delivery_never_retries_on_same_source(tmp_path: Path) -> None:
    database = create_affiliate_shadow_database(tmp_path / "pilot.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=2, run_seconds=5, max_api_calls=1, max_send_messages=1)
    )

    class ForbiddenCanonical:
        async def process(self, _id: int) -> None:
            pytest.fail("coin message entered canonical relay")

        async def convert(self, _id: int) -> None:
            pytest.fail("coin message entered canonical conversion")

    class Preview:
        calls = 0

        async def prepare(self, _message: IncomingMessage) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(preview_id=3, cache_hit=False)

    class Delivery:
        calls = 0

        async def deliver_automatic(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(status="uncertain", error_code="COIN_SHADOW_SEND_UNCERTAIN")

    preview, delivery = Preview(), Delivery()
    message = IncomingMessage(
        platform="telegram",
        message_id=82,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=SHORT,
        links=(ExtractedLink(SHORT, LinkSource.TEXT, 0),),
    )
    try:
        persisted = await DurableRelayQueue(
            database, TelegramRelayConfig()
        ).persist_without_enqueue(message)
        processor = AliExpressShadowMessageProcessor(
            database,
            ForbiddenCanonical(),
            ForbiddenCanonical(),
            controller,
            clock=lambda: NOW,
            destination="private-test",
            delivery=ForbiddenCanonical(),
            delivery_authorization=object(),
            coin_preview=preview,
            coin_delivery=delivery,
        )
        await processor.process(persisted.internal_id)
        await processor.process(persisted.internal_id)
        assert preview.calls == delivery.calls == 1
        assert controller.deliveries_sent == 0
        async with database.session() as session:
            source = await SourceMessageRepository(session).get(persisted.internal_id)
            assert source is not None
            assert source.processing_status == "FAILED_PERMANENT"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_expired_pilot_processing_lease_becomes_terminal_uncertain(
    tmp_path: Path,
) -> None:
    database = create_affiliate_shadow_database(tmp_path / "pilot.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    message = IncomingMessage(
        platform="telegram",
        message_id=83,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=SHORT,
        links=(ExtractedLink(SHORT, LinkSource.TEXT, 0),),
    )
    try:
        persisted = await DurableRelayQueue(
            database, TelegramRelayConfig()
        ).persist_without_enqueue(message)
        async with database.session() as session:
            repo = SourceMessageRepository(session)
            assert (
                await repo.claim(
                    persisted.internal_id,
                    now=NOW - timedelta(minutes=10),
                    lease_until=NOW - timedelta(minutes=5),
                    max_attempts=1,
                )
                is not None
            )
        async with database.session() as session:
            changed = await SourceMessageRepository(session).expire_pilot_processing(now=NOW)
            assert changed == 1
            source = await SourceMessageRepository(session).get(persisted.internal_id)
            assert source is not None
            assert source.processing_status == "FAILED_PERMANENT"
            assert source.error_code == "SHADOW_PILOT_OUTCOME_UNCERTAIN"
            assert source.next_attempt_at is None
    finally:
        await database.dispose()


def test_second_listener_on_same_database_fails_without_waiting(tmp_path: Path) -> None:
    from promo_bot.affiliate.shadow_listener_lock import ShadowListenerLock

    path = tmp_path / "pilot.sqlite3"
    with ShadowListenerLock(path):
        with pytest.raises(RuntimeError, match="SHADOW_LISTENER_ALREADY_ACTIVE"):
            with ShadowListenerLock(path):
                pytest.fail("second listener entered")
    with ShadowListenerLock(path):
        pass


def test_second_process_on_same_database_fails_before_entering_listener(tmp_path: Path) -> None:
    from promo_bot.affiliate.shadow_listener_lock import ShadowListenerLock

    path = tmp_path / "pilot.sqlite3"
    probe = (
        "from pathlib import Path\n"
        "from promo_bot.affiliate.shadow_listener_lock import ShadowListenerLock\n"
        "import sys\n"
        "try:\n"
        "    with ShadowListenerLock(Path(sys.argv[1])):\n"
        "        print('entered')\n"
        "except RuntimeError:\n"
        "    print('blocked')\n"
    )
    with ShadowListenerLock(path):
        result = subprocess.run(
            [sys.executable, "-c", probe, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "blocked"
    assert result.stderr == ""


@pytest.mark.parametrize("short", [SHORT, "https://s.click.aliexpress.com/e/_Ab12Cd3"])
@pytest.mark.asyncio
async def test_coin_short_bypasses_canonical_relay_and_uses_shared_send_budget(
    tmp_path: Path,
    short: str,
) -> None:
    database = create_affiliate_shadow_database(tmp_path / "pilot.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    controller = ShadowRunController(
        ShadowRunLimits(max_messages=1, run_seconds=5, max_api_calls=1, max_send_messages=1)
    )
    observed: list[str] = []

    class ForbiddenCanonical:
        async def process(self, _source_message_id: int) -> None:
            pytest.fail("coin short reached canonical relay")

        async def convert(self, _source_message_id: int) -> None:
            pytest.fail("coin short reached canonical conversion")

    class FakeCoinPreview:
        async def prepare(self, message: IncomingMessage) -> SimpleNamespace:
            observed.append(message.original_text)
            await controller.before_api_call()
            return SimpleNamespace(preview_id=4, cache_hit=False)

    class FakeCoinDelivery:
        async def deliver_automatic(
            self,
            _preview_id: int,
            _destination: str,
            *,
            authorization: object,
            before_send: object,
        ) -> SimpleNamespace:
            assert authorization is auth
            await before_send()
            return SimpleNamespace(status="sent", error_code=None)

    auth = object()
    message = IncomingMessage(
        platform="telegram",
        message_id=73,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=f"Oferta R$ 10\n{short}",
        links=(ExtractedLink(short, LinkSource.TEXT, 0),),
    )
    relay_config = TelegramRelayConfig(processing_max_attempts=1)
    try:
        persisted = await DurableRelayQueue(database, relay_config).persist_without_enqueue(message)
        processor = AliExpressShadowMessageProcessor(
            database,
            ForbiddenCanonical(),
            ForbiddenCanonical(),
            controller,
            clock=lambda: NOW,
            destination="private-test",
            delivery=ForbiddenCanonical(),
            delivery_authorization=auth,
            coin_preview=FakeCoinPreview(),
            coin_delivery=FakeCoinDelivery(),
        )
        await processor.process(persisted.internal_id)

        assert observed == [message.original_text]
        assert controller.api_calls == 1
        assert controller.send_messages == 1
        assert controller.deliveries_sent == 1
        async with database.session() as session:
            source = await SourceMessageRepository(session).get(persisted.internal_id)
            assert source is not None
            assert source.processing_status == "COMPLETED"
    finally:
        await database.dispose()
