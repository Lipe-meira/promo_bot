from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.domain.enums import LinkSource
from promo_bot.relay.models import ExtractedLink, IncomingMessage, MessageSurfaceMetadata

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
SOURCE = "https://s.click.aliexpress.com/e/_Ab12Cd3"
PROMOTION = "https://s.click.aliexpress.com/e/_Zy98Xw7"
TRACKING = "configured-tracking"
SECRET = "fixture-app-secret"
SOURCE_CHAT = "-1001234567890"
TARGET_CHAT = "-1009876543210"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class ImmediateClient:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, operation: str, payload: dict[str, str]) -> dict[str, object]:
        del operation
        self.calls += 1
        assert payload["source_values"] == SOURCE
        return {
            "code": "0",
            "aliexpress_affiliate_link_generate_response": {
                "resp_result": {
                    "resp_code": "200",
                    "result": {
                        "promotion_links": [{"source_value": SOURCE, "promotion_link": PROMOTION}],
                        "tracking_id": TRACKING,
                    },
                }
            },
        }


@dataclass
class Access:
    id: int = int(TARGET_CHAT)
    type: str = "channel"
    username: str | None = None
    active_usernames: tuple[str, ...] = ()
    bot_membership_status: str = "administrator"
    can_post_messages: bool = True


class FakeTransport:
    def __init__(
        self,
        *,
        access: Access | None = None,
        inspect_error: Exception | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.access = access or Access()
        self.inspect_error = inspect_error
        self.send_error = send_error
        self.inspections: list[str] = []
        self.sends: list[tuple[str, str]] = []

    async def inspect_private_channel(self, chat_id: str) -> Access:
        self.inspections.append(chat_id)
        if self.inspect_error:
            raise self.inspect_error
        return self.access

    async def send_text(self, chat_id: str, text: str) -> str:
        self.sends.append((chat_id, text))
        if self.send_error:
            raise self.send_error
        return "77"


def settings(**updates: object) -> EnvironmentSettings:
    values: dict[str, object] = {
        "_env_file": None,
        "aliexpress_coin_short_shadow_enabled": True,
        "aliexpress_live_api_enabled": True,
        "aliexpress_app_key": "fixture-key",
        "aliexpress_app_secret": SECRET,
        "aliexpress_tracking_id": TRACKING,
        "telegram_api_id": 123,
        "telegram_api_hash": "fixture-hash",
        "telegram_bot_token": "123:fixture-token",
        "dry_run": True,
        "publish_real_deals": False,
        "publish_without_affiliate": False,
        "search_enabled": False,
        "coupon_browser_verification": False,
    }
    values.update(updates)
    return EnvironmentSettings(**values)


def config(
    *,
    alias: str = "private-test",
    target: str = TARGET_CHAT,
    source: str = SOURCE_CHAT,
    kind: str = "private_channel",
) -> AppConfig:
    return AppConfig.model_validate(
        {
            "source_channels": [source],
            "providers": {"aliexpress": {"enabled": True, "affiliate_mode": "official_api"}},
            "telegram_shadow_delivery": {
                "allowed_destinations": {alias: {"chat_id": target, "kind": kind}}
            },
            "templates": ["{link_afiliado}"],
            "affiliate_disclosure": "fixture",
        }
    )


def incoming() -> IncomingMessage:
    return IncomingMessage(
        platform="telegram",
        message_id=77,
        channel_id=SOURCE_CHAT,
        occurred_at=NOW,
        original_text=f"Moedas e cupom\n{SOURCE}\nTexto preservado",
        links=(ExtractedLink(SOURCE, LinkSource.TEXT, 0),),
        surface_metadata=MessageSurfaceMetadata(),
    )


async def make_stack(tmp_path: Path, *, clock: MutableClock | None = None):
    from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService
    from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewService
    from promo_bot.database.models import Base

    active_clock = clock or MutableClock(NOW)
    database = create_affiliate_shadow_database(tmp_path / "coin-delivery.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    client = ImmediateClient()
    generation = CoinShadowGenerationService(
        database,
        client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=active_clock,
    )
    preview_service = CoinShadowPreviewService(
        database,
        generation,
        app_secret=SECRET,
        clock=active_clock,
    )
    preview = await preview_service.prepare(incoming())
    transport = FakeTransport()
    delivery = CoinShadowDeliveryService(
        database,
        transport,
        settings(),
        config(),
        app_secret=SECRET,
        clock=active_clock,
    )
    return database, client, preview_service, preview, transport, delivery


@pytest.mark.asyncio
async def test_private_delivery_checks_membership_posts_once_and_deduplicates(
    tmp_path: Path,
) -> None:
    from promo_bot.database.models import (
        AliExpressCoinShadowDeliveryModel,
        DealModel,
        DeliveryModel,
    )

    database, _client, _preview_service, preview, transport, delivery = await make_stack(tmp_path)
    first = await delivery.deliver(preview.preview_id, "private-test")
    second = await delivery.deliver(preview.preview_id, "private-test")

    assert first.status == "sent"
    assert first.inspection_attempts == 1
    assert first.send_message_attempts == 1
    assert second.error_code == "COIN_SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    assert len(transport.inspections) == len(transport.sends) == 1
    assert transport.sends[0] == (TARGET_CHAT, preview.rendered_text)
    async with database.session() as session:
        row = (await session.execute(select(AliExpressCoinShadowDeliveryModel))).scalar_one()
        assert row.attempt_count == 1
        assert row.state == "sent"
        assert await session.scalar(select(func.count(DealModel.id))) == 0
        assert await session.scalar(select(func.count(DeliveryModel.id))) == 0
    await database.dispose()


@pytest.mark.parametrize(
    ("destination", "app_config", "code"),
    (
        ("unknown", config(), "DESTINATION_REQUIRED"),
        ("renamed", config(alias="renamed"), "DESTINATION_REQUIRED"),
        ("private-test", config(target=SOURCE_CHAT), "DESTINATION_IS_SOURCE"),
        ("private-test", config(source="@public-source"), "NUMERIC_SOURCE_REQUIRED"),
    ),
)
@pytest.mark.asyncio
async def test_delivery_rejects_nonexclusive_or_unprovable_destination_before_network(
    tmp_path: Path,
    destination: str,
    app_config: AppConfig,
    code: str,
) -> None:
    from promo_bot.affiliate.coin_shadow_delivery import (
        CoinShadowDeliveryRejected,
        CoinShadowDeliveryService,
    )

    database, _client, _preview_service, preview, transport, _delivery = await make_stack(tmp_path)
    service = CoinShadowDeliveryService(
        database,
        transport,
        settings(),
        app_config,
        app_secret=SECRET,
        clock=lambda: NOW,
    )
    with pytest.raises(CoinShadowDeliveryRejected, match=f"COIN_SHADOW_{code}"):
        await service.deliver(preview.preview_id, destination)
    assert transport.inspections == transport.sends == []
    await database.dispose()


@pytest.mark.parametrize(
    ("access", "code"),
    (
        (Access(type="supergroup"), "DESTINATION_NOT_PRIVATE_CHANNEL"),
        (Access(username="public_name"), "DESTINATION_NOT_PRIVATE_CHANNEL"),
        (Access(bot_membership_status="left"), "BOT_NOT_CHANNEL_MEMBER"),
        (Access(can_post_messages=False), "BOT_CANNOT_POST"),
    ),
)
@pytest.mark.asyncio
async def test_delivery_requires_private_channel_membership_and_post_permission(
    tmp_path: Path,
    access: Access,
    code: str,
) -> None:
    from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService

    database, _client, _preview_service, preview, _transport, _delivery = await make_stack(tmp_path)
    transport = FakeTransport(access=access)
    report = await CoinShadowDeliveryService(
        database,
        transport,
        settings(),
        config(),
        app_secret=SECRET,
        clock=lambda: NOW,
    ).deliver(preview.preview_id, "private-test")
    assert report.status == "failed_safe"
    assert report.error_code == f"COIN_SHADOW_{code}"
    assert report.send_message_attempts == 0
    assert transport.sends == []
    await database.dispose()


@pytest.mark.asyncio
async def test_ambiguous_send_is_uncertain_and_never_retried(tmp_path: Path, caplog) -> None:
    from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService

    database, _client, _preview_service, preview, _transport, _delivery = await make_stack(tmp_path)
    transport = FakeTransport(send_error=TimeoutError("private payload detail"))
    delivery = CoinShadowDeliveryService(
        database,
        transport,
        settings(),
        config(),
        app_secret=SECRET,
        clock=lambda: NOW,
    )
    first = await delivery.deliver(preview.preview_id, "private-test")
    second = await delivery.deliver(preview.preview_id, "private-test")

    assert first.status == "uncertain"
    assert second.error_code == "COIN_SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    assert len(transport.sends) == 1
    exposed = str(first.sanitized_output()) + caplog.text
    for secret in (SOURCE, PROMOTION, TRACKING, SECRET, TARGET_CHAT, "private payload detail"):
        assert secret not in exposed
    await database.dispose()


@pytest.mark.asyncio
async def test_expired_purge_nulls_preview_but_durable_reservation_blocks_resend(
    tmp_path: Path,
) -> None:
    from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryService
    from promo_bot.database.models import (
        AliExpressCoinShadowDeliveryModel,
        AliExpressCoinShadowPreviewModel,
    )

    clock = MutableClock(NOW)
    database, client, preview_service, preview, transport, delivery = await make_stack(
        tmp_path, clock=clock
    )
    assert (await delivery.deliver(preview.preview_id, "private-test")).status == "sent"

    clock.value = NOW + timedelta(hours=25)
    replacement = await preview_service.prepare(incoming())
    duplicate = await CoinShadowDeliveryService(
        database,
        transport,
        settings(),
        config(),
        app_secret=SECRET,
        clock=clock,
    ).deliver(replacement.preview_id, "private-test")

    assert client.calls == 2
    assert duplicate.error_code == "COIN_SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    assert len(transport.sends) == 1
    async with database.session() as session:
        delivery_row = (
            await session.execute(select(AliExpressCoinShadowDeliveryModel))
        ).scalar_one()
        assert delivery_row.preview_id is None
        assert await session.scalar(select(func.count(AliExpressCoinShadowPreviewModel.id))) == 1
    await database.dispose()


def test_real_main_uses_explicit_settings_fake_runtime_and_never_prints_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from promo_bot.affiliate.coin_shadow_delivery import CoinShadowDeliveryOutcome
    from promo_bot.cli import main

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
source_channels: ["{SOURCE_CHAT}"]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "{TARGET_CHAT}"
      kind: private_channel
templates: ["{{link_afiliado}}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    outcome = CoinShadowDeliveryOutcome(
        delivery_id=1,
        preview_id=2,
        status="sent",
        inspection_attempts=1,
        send_message_attempts=1,
        external_side_effect=True,
    )

    async def fake_runtime(*_args: object, **_kwargs: object) -> CoinShadowDeliveryOutcome:
        return outcome

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings())
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_coin_shadow_auto_delivery", fake_runtime)
    result = main(
        [
            "aliexpress",
            "coin-shadow-auto-deliver",
            "--config",
            str(config_path),
            "--message-link",
            "https://t.me/c/1234567890/77",
            "--shadow-database",
            str(tmp_path / "shadow.sqlite3"),
            "--destination",
            "private-test",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 0
    assert report["status"] == "sent"
    assert report["production_publication"] is False
    assert PROMOTION not in captured.out + captured.err
    assert SOURCE not in captured.out + captured.err
    assert TRACKING not in captured.out + captured.err
