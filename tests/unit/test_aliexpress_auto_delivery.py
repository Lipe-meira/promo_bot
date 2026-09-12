from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from promo_bot.cli import main
from promo_bot.config import EnvironmentSettings


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
