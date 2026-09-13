from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from telethon.tl.types import MessageEntityBold, MessageEntityUrl

from promo_bot.cli import main
from promo_bot.config import EnvironmentSettings
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.shadow import shadow_database_url


def test_real_cli_entrypoint_finishes_after_preview_is_sent_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    product_id = "1005000000000001"
    canonical = f"https://pt.aliexpress.com/item/{product_id}.html"
    affiliate = "https://s.click.aliexpress.com/e/auto-fixture"
    source = "-1001234567890"
    target = "-1009876543210"
    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "auto-shadow.sqlite3"
    config_path.write_text(
        f"""
source_channels: ["{source}"]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "{target}"
      kind: private_channel
telegram_relay:
  queue_max_size: 1
templates: ["{{link_afiliado}}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="fixture-api-hash",
        telegram_bot_token="123:fixture-token",
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret="fixture-secret",
        aliexpress_tracking_id="fixture-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_auto_delivery_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )

    class Message:
        id = 601
        date = datetime(2026, 9, 12, 12, tzinfo=UTC)
        raw_text = f"🔥 Oferta\n{canonical}\nAmazon preservada https://amazon.com.br/dp/B0ABCDEFGH"
        out = False
        buttons = None
        media = None

        @staticmethod
        def get_entities_text() -> list[tuple[object, str]]:
            return []

    class TelethonLikeClient:
        def __init__(self) -> None:
            self.handler_tasks: list[asyncio.Task[None]] = []

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            pending = [task for task in self.handler_tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        async def is_user_authorized(self) -> bool:
            return True

        async def get_entity(self, _reference: str | int) -> object:
            return SimpleNamespace(id=1234567890)

        def add_event_handler(self, callback: Any, _builder: object) -> None:
            async def emit() -> None:
                await asyncio.sleep(0)
                await callback(SimpleNamespace(chat_id=int(source), message=Message()))

            self.handler_tasks.append(asyncio.create_task(emit()))

        def remove_event_handler(self, _callback: object, _builder: object) -> None:
            return None

    class FakeBotTransport:
        sent: ClassVar[list[tuple[str, str]]] = []

        def __init__(self, _token: str) -> None:
            pass

        async def __aenter__(self) -> FakeBotTransport:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_chat(self, chat_id: str) -> object:
            return SimpleNamespace(
                id=int(chat_id),
                type="channel",
                username=None,
                active_usernames=(),
            )

        async def send_text(self, chat_id: str, text: str) -> str:
            self.sent.append((chat_id, text))
            return "77"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "aliexpress_affiliate_link_generate_response": {
                    "resp_result": {
                        "result": {
                            "total_result_count": "1",
                            "promotion_links": [
                                {
                                    "promotion_link": affiliate,
                                    "source_value": canonical,
                                }
                            ],
                        },
                        "resp_code": "200",
                        "resp_msg": "success",
                    }
                },
                "request_id": "auto-fixture-request",
            },
            request=request,
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        follow_redirects=False,
    )
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "promo_bot.cli.build_telegram_user_client",
        lambda *_args, **_kwargs: TelethonLikeClient(),
    )
    monkeypatch.setattr("promo_bot.cli.build_offline_safe_http_client", lambda: http_client)
    monkeypatch.setattr("promo_bot.cli.ShadowBotTransport", FakeBotTransport)
    monkeypatch.setattr(
        "promo_bot.telegram.monitor.utils.get_peer_id",
        lambda _entity: int(source),
    )

    result = main(
        [
            "aliexpress",
            "shadow-auto-deliver",
            "--config",
            str(config_path),
            "--shadow-database",
            str(database_path),
            "--destination",
            "private-test",
            "--max-messages",
            "1",
            "--run-seconds",
            "1",
            "--max-api-calls",
            "1",
            "--max-links-per-message",
            "3",
            "--max-send-messages",
            "1",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["messages_received"] == 1
    assert summary["processed"] == 1
    assert summary["previews_created"] == 1
    assert summary["api_calls"] == 1
    assert summary["send_messages"] == 1
    assert summary["deliveries_sent"] == 1
    assert summary["production_publication"] is False
    assert len(FakeBotTransport.sent) == 1
    assert FakeBotTransport.sent[0][0] == target
    assert affiliate in FakeBotTransport.sent[0][1]
    assert "https://amazon.com.br/dp/B0ABCDEFGH" in FakeBotTransport.sent[0][1]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM affiliate_shadow_previews").fetchone() == (
            1,
        )
        assert connection.execute(
            "SELECT state,attempt_count FROM affiliate_shadow_deliveries"
        ).fetchone() == ("sent", 1)
        assert connection.execute("SELECT COUNT(*) FROM deals").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM deliveries").fetchone() == (0,)


def test_real_cli_entrypoint_recognizes_terminal_legacy_message_without_resend(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_url = "https://s.click.aliexpress.com/e/legacy-fixture"
    visible_text = f"🔥 Oferta em negrito\n{source_url}"
    source = "-1001234567890"
    target = "-1009876543210"
    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "legacy-auto-shadow.sqlite3"
    config_path.write_text(
        f"""
source_channels: ["{source}"]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "{target}"
      kind: private_channel
telegram_relay:
  queue_max_size: 1
templates: ["{{link_afiliado}}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="fixture-api-hash",
        telegram_bot_token="123:fixture-token",
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret="fixture-secret",
        aliexpress_tracking_id="fixture-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_auto_delivery_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )
    links = [{"url": source_url, "source": "TEXT", "ordinal": 0}]
    old_payload = json.dumps(
        {"text": visible_text, "links": links},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    old_hash = hashlib.sha256(old_payload).hexdigest()
    asyncio.run(upgrade_database_async(shadow_database_url(database_path)))
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    expires = now + timedelta(hours=24)
    now_value = now.isoformat()
    expires_value = expires.isoformat()
    destination_key = hashlib.sha256(f"telegram:{target}".encode()).hexdigest()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO source_messages "
            "(id,platform,message_id,channel_id,occurred_at,original_text,links,"
            "surface_metadata,content_hash,processing_status,attempt_count,error_code,created_at,"
            "updated_at) VALUES "
            "(1,'telegram','701',?,?,?,?,?,?,'FAILED_PERMANENT',1,"
            "'CONTENT_HASH_MISMATCH',?,?)",
            (
                source,
                now_value,
                visible_text,
                json.dumps(links),
                json.dumps({"legacy_unknown": True}),
                old_hash,
                now_value,
                now_value,
            ),
        )
        connection.execute(
            "INSERT INTO affiliate_shadow_previews "
            "(id,source_message_id,affiliate_proof_id,provider,store,status,replacement_count,"
            "cache_hit,affiliate_host,rendered_text,affiliate_link,content_expires_at,created_at,"
            "updated_at) VALUES "
            "(2,1,999,'aliexpress_official','aliexpress','READY',1,0,"
            "'s.click.aliexpress.com',NULL,NULL,?,?,?)",
            (expires_value, now_value, now_value),
        )
        connection.execute(
            "INSERT INTO affiliate_shadow_deliveries "
            "(preview_id,source_message_id,destination_key,state,attempt_count,finished_at,"
            "created_at,updated_at) VALUES (2,1,?,'sent',1,?,?,?)",
            (destination_key, now_value, now_value, now_value),
        )

    class Message:
        id = 701
        date = now
        raw_text = visible_text
        out = False
        buttons = None
        media = None

        @staticmethod
        def get_entities_text() -> list[tuple[object, str]]:
            return [
                (MessageEntityBold(offset=0, length=20), "Oferta em negrito"),
                (MessageEntityUrl(offset=21, length=len(source_url)), source_url),
            ]

    class TelethonLikeClient:
        def __init__(self) -> None:
            self.handler_tasks: list[asyncio.Task[None]] = []

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            pending = [task for task in self.handler_tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        async def is_user_authorized(self) -> bool:
            return True

        async def get_entity(self, _reference: str | int) -> object:
            return SimpleNamespace(id=1234567890)

        def add_event_handler(self, callback: Any, _builder: object) -> None:
            async def emit() -> None:
                await asyncio.sleep(0)
                await callback(SimpleNamespace(chat_id=int(source), message=Message()))

            self.handler_tasks.append(asyncio.create_task(emit()))

        def remove_event_handler(self, _callback: object, _builder: object) -> None:
            return None

    class NoSendBotTransport:
        def __init__(self, _token: str) -> None:
            pass

        async def __aenter__(self) -> NoSendBotTransport:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_chat(self, _chat_id: str) -> object:
            raise AssertionError("legacy duplicate must not reach Bot API")

        async def send_text(self, _chat_id: str, _text: str) -> str:
            raise AssertionError("legacy duplicate must not be sent")

    async def no_api(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"legacy duplicate must not call API: {request.method}")

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(no_api),
        trust_env=False,
        follow_redirects=False,
    )
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "promo_bot.cli.build_telegram_user_client",
        lambda *_args, **_kwargs: TelethonLikeClient(),
    )
    monkeypatch.setattr("promo_bot.cli.build_offline_safe_http_client", lambda: http_client)
    monkeypatch.setattr("promo_bot.cli.ShadowBotTransport", NoSendBotTransport)
    monkeypatch.setattr(
        "promo_bot.telegram.monitor.utils.get_peer_id",
        lambda _entity: int(source),
    )

    result = main(
        [
            "aliexpress",
            "shadow-auto-deliver",
            "--config",
            str(config_path),
            "--shadow-database",
            str(database_path),
            "--destination",
            "private-test",
            "--max-messages",
            "1",
            "--run-seconds",
            "1",
            "--max-api-calls",
            "1",
            "--max-links-per-message",
            "3",
            "--max-send-messages",
            "1",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["messages_received"] == 1
    assert summary["processed"] == 1
    assert summary["rejected"] == 0
    assert summary["rejection_codes"] == []
    assert summary["previews_created"] == 0
    assert summary["api_calls"] == 0
    assert summary["send_messages"] == 0
    assert summary["deliveries_sent"] == 0
    with sqlite3.connect(database_path) as connection:
        stored = connection.execute(
            "SELECT surface_metadata,content_hash,processing_status,error_code "
            "FROM source_messages WHERE id=1"
        ).fetchone()
        assert json.loads(stored[0]) == {"legacy_unknown": True}
        assert stored[1:] == (old_hash, "FAILED_PERMANENT", "CONTENT_HASH_MISMATCH")
        assert connection.execute(
            "SELECT state,attempt_count FROM affiliate_shadow_deliveries"
        ).fetchone() == ("sent", 1)
