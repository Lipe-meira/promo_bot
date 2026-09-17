from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.domain.enums import LinkSource
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.relay.models import ExtractedLink, IncomingMessage, MessageSurfaceMetadata

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
APP_SOURCE = "https://a.aliexpress.com/_c3IQHJ6J"
PROMOTION = "https://s.click.aliexpress.com/e/_Zy98Xw7"
TRACKING = "configured-tracking"
SECRET = "fixture-app-secret"
RAW_MARKER = "raw-response-must-not-leak"
SOURCE_CHAT = "-1001234567890"
TARGET_CHAT = "-1009876543210"


class AppShareTopClient:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, operation: str, payload: dict[str, str]) -> dict[str, object]:
        self.calls += 1
        assert operation == LINK_GENERATE
        assert payload == {
            "ship_to_country": "BR",
            "promotion_link_type": "0",
            "source_values": APP_SOURCE,
            "tracking_id": TRACKING,
        }
        return {
            "code": "0",
            "aliexpress_affiliate_link_generate_response": {
                "resp_result": {
                    "resp_code": "200",
                    "result": {
                        "promotion_links": [{"promotion_link": PROMOTION}],
                        "tracking_id": TRACKING,
                    },
                }
            },
            "request_id": RAW_MARKER,
        }


@dataclass
class Access:
    id: int = int(TARGET_CHAT)
    type: str = "channel"
    username: str | None = None
    active_usernames: tuple[str, ...] = ()
    bot_membership_status: str = "administrator"
    can_post_messages: bool = True


class FakeDeliveryTransport:
    def __init__(self) -> None:
        self.inspections: list[str] = []
        self.sends: list[tuple[str, str]] = []

    async def inspect_private_channel(self, chat_id: str) -> Access:
        self.inspections.append(chat_id)
        return Access()

    async def send_text(self, chat_id: str, text: str) -> str:
        self.sends.append((chat_id, text))
        return "77"


def settings(*, enabled: bool = True) -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        aliexpress_coin_short_shadow_enabled=enabled,
        aliexpress_live_api_enabled=True,
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret=SECRET,
        aliexpress_tracking_id=TRACKING,
        telegram_api_id=123,
        telegram_api_hash="fixture-hash",
        telegram_bot_token="123:fixture-token",
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )


def config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "source_channels": [SOURCE_CHAT],
            "providers": {"aliexpress": {"enabled": True, "affiliate_mode": "official_api"}},
            "telegram_shadow_delivery": {
                "allowed_destinations": {
                    "private-test": {
                        "chat_id": TARGET_CHAT,
                        "kind": "private_channel",
                    }
                }
            },
            "templates": ["{link_afiliado}"],
            "affiliate_disclosure": "fixture",
        }
    )


def incoming(text: str | None = None) -> IncomingMessage:
    original = text or f"Moedas e cupom\n{APP_SOURCE}\nTexto preservado"
    return IncomingMessage(
        platform="telegram",
        message_id=77,
        channel_id=SOURCE_CHAT,
        occurred_at=NOW,
        original_text=original,
        links=(ExtractedLink(APP_SOURCE, LinkSource.TEXT, 0),),
        surface_metadata=MessageSurfaceMetadata(),
    )


async def make_stack(tmp_path: Path):
    from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService
    from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewService
    from promo_bot.database.models import Base

    database = create_affiliate_shadow_database(tmp_path / "app-share.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    top = AppShareTopClient()
    generation = CoinShadowGenerationService(
        database,
        top,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: NOW,
    )
    preview = CoinShadowPreviewService(
        database,
        generation,
        app_secret=SECRET,
        clock=lambda: NOW,
    )
    delivery_transport = FakeDeliveryTransport()
    delivery = CoinShadowDeliveryService(
        database,
        delivery_transport,
        settings(),
        config(),
        app_secret=SECRET,
        clock=lambda: NOW,
    )
    return database, top, preview, delivery_transport, delivery


@pytest.mark.asyncio
async def test_app_share_preview_preserves_literal_uses_cache_and_stays_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from promo_bot.database.models import AffiliateCandidateModel, DealModel, DeliveryModel
    from promo_bot.security.aliexpress_short_links import AliExpressShortLinkResolver

    async def forbidden_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("coin-shadow must not resolve or fetch the short")

    monkeypatch.setattr(AliExpressShortLinkResolver, "resolve", forbidden_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_network)
    caplog.set_level(logging.INFO)
    database, top, preview_service, _transport, _delivery = await make_stack(tmp_path)
    message = incoming()

    first = await preview_service.prepare(message)
    second = await preview_service.prepare(message)

    assert first.rendered_text == message.original_text.replace(APP_SOURCE, PROMOTION)
    assert first.replacement_count == 1
    assert first.correlation_mode == "POSITIONAL_SINGLETON"
    assert first.tracking_confirmed is True
    assert first.attribution_unverified is True
    assert first.route_preservation_manually_observed is False
    assert second.preview_id == first.preview_id
    assert second.cache_hit is True
    assert top.calls == 1
    sanitized_stdout = json.dumps(first.explicit_output(include_content=False))
    for sensitive in (APP_SOURCE, "_c3IQHJ6J", TRACKING, RAW_MARKER, PROMOTION):
        assert sensitive not in sanitized_stdout + caplog.text
    async with database.session() as session:
        counts = [
            await session.scalar(select(func.count(model.id)))
            for model in (AffiliateCandidateModel, DealModel, DeliveryModel)
        ]
    assert counts == [0, 0, 0]
    await database.dispose()


@pytest.mark.asyncio
async def test_app_share_message_must_have_exactly_one_visible_url(tmp_path: Path) -> None:
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected

    database, top, preview_service, _transport, _delivery = await make_stack(tmp_path)
    with pytest.raises(
        CoinShadowPreviewRejected,
        match="ALIEXPRESS_COIN_VISIBLE_URL_COUNT_INVALID",
    ):
        await preview_service.prepare(incoming(f"{APP_SOURCE} https://example.com/extra"))
    assert top.calls == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_app_share_auto_delivery_sends_once_and_duplicate_never_resends(
    tmp_path: Path,
) -> None:
    database, top, preview_service, transport, delivery = await make_stack(tmp_path)
    preview = await preview_service.prepare(incoming())

    first = await delivery.deliver(preview.preview_id, "private-test")
    duplicate = await delivery.deliver(preview.preview_id, "private-test")

    assert first.status == "sent"
    assert first.send_message_attempts == 1
    assert duplicate.error_code == "COIN_SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    assert top.calls == 1
    assert transport.inspections == [TARGET_CHAT]
    assert transport.sends == [(TARGET_CHAT, preview.rendered_text)]
    await database.dispose()


def test_closed_gate_stops_before_any_coin_shadow_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from promo_bot.cli import main

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
source_channels: ["{SOURCE_CHAT}"]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates: ["{{link_afiliado}}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    transport_calls = 0

    async def forbidden_runtime(*_args: object, **_kwargs: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("closed gate reached transport")

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings(enabled=False))
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_coin_shadow_preview", forbidden_runtime)
    result = main(
        [
            "aliexpress",
            "coin-shadow-preview",
            "--config",
            str(config_path),
            "--message-link",
            "https://t.me/c/1234567890/77",
            "--shadow-database",
            str(tmp_path / "shadow.sqlite3"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert transport_calls == 0
    for sensitive in (APP_SOURCE, "_c3IQHJ6J", TRACKING, RAW_MARKER):
        assert sensitive not in captured.out + captured.err
