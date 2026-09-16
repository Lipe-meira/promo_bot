from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.domain.enums import LinkSource
from promo_bot.relay.models import ExtractedLink, IncomingMessage, MessageSurfaceMetadata

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
SOURCE = "https://s.click.aliexpress.com/e/_Ab12Cd3"
PROMOTION = "https://s.click.aliexpress.com/e/_Zy98Xw7"
TRACKING = "configured-tracking"
SECRET = "fixture-app-secret"


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


def message(text: str, *, metadata: MessageSurfaceMetadata | None = None) -> IncomingMessage:
    return IncomingMessage(
        platform="telegram",
        message_id=77,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=text,
        links=(ExtractedLink(SOURCE, LinkSource.TEXT, 0),),
        surface_metadata=metadata or MessageSurfaceMetadata(),
    )


async def make_service(tmp_path: Path):
    from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewService
    from promo_bot.database.models import Base

    database = create_affiliate_shadow_database(tmp_path / "coin-preview.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    client = ImmediateClient()
    generation = CoinShadowGenerationService(
        database,
        client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: NOW,
    )
    return (
        database,
        client,
        CoinShadowPreviewService(
            database,
            generation,
            app_secret=SECRET,
            clock=lambda: NOW,
        ),
    )


def test_coin_shadow_gate_is_disabled_by_default() -> None:
    settings = EnvironmentSettings(_env_file=None)
    assert settings.aliexpress_coin_short_shadow_enabled is False


@pytest.mark.asyncio
async def test_preview_preserves_text_and_replaces_only_visible_short(tmp_path: Path) -> None:
    database, client, service = await make_service(tmp_path)
    original = f"Cupom e moedas preservados\n{SOURCE}\nFim da oferta."

    preview = await service.prepare(message(original))

    assert preview.rendered_text == original.replace(SOURCE, PROMOTION)
    assert preview.replacement_count == 1
    assert preview.tracking_confirmed is True
    assert preview.attribution_unverified is True
    assert preview.route_preservation_manually_observed is False
    assert client.calls == 1
    await database.dispose()


@pytest.mark.parametrize(
    ("text", "metadata", "code"),
    (
        ("sem link", MessageSurfaceMetadata(), "VISIBLE_URL_COUNT_INVALID"),
        (
            f"{SOURCE} https://example.com/extra",
            MessageSurfaceMetadata(),
            "VISIBLE_URL_COUNT_INVALID",
        ),
        (
            "https://s.click.aliexpress.com/e/_Ab12-Cd",
            MessageSurfaceMetadata(),
            "SHORT_INVALID",
        ),
        (
            SOURCE,
            MessageSurfaceMetadata(has_buttons=True),
            "SURFACE_UNSAFE",
        ),
    ),
)
@pytest.mark.asyncio
async def test_preview_rejects_unproven_message_without_calling_top(
    tmp_path: Path,
    text: str,
    metadata: MessageSurfaceMetadata,
    code: str,
) -> None:
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected

    database, client, service = await make_service(tmp_path)
    with pytest.raises(CoinShadowPreviewRejected, match=f"ALIEXPRESS_COIN_{code}"):
        await service.prepare(message(text, metadata=metadata))
    assert client.calls == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_preview_deduplicates_message_and_reuses_ready_cache(tmp_path: Path) -> None:
    database, client, service = await make_service(tmp_path)
    incoming = message(f"Oferta {SOURCE}")

    first = await service.prepare(incoming)
    second = await service.prepare(incoming)

    assert first.preview_id == second.preview_id
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert client.calls == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_preview_does_not_create_production_records(tmp_path: Path) -> None:
    from promo_bot.database.models import (
        AffiliateCandidateModel,
        DealModel,
        DeliveryModel,
    )

    database, _client, service = await make_service(tmp_path)
    await service.prepare(message(f"Oferta {SOURCE}"))

    async with database.session() as session:
        counts = [
            await session.scalar(select(func.count(model.id)))
            for model in (AffiliateCandidateModel, DealModel, DeliveryModel)
        ]
    assert counts == [0, 0, 0]
    await database.dispose()


def test_coin_shadow_preview_cli_hides_content_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewOutcome
    from promo_bot.cli import main

    config = tmp_path / "config.yaml"
    config.write_text(
        "source_channels: [-1001234567890]\n"
        "providers:\n  aliexpress:\n    enabled: true\n    affiliate_mode: official_api\n"
        "templates: ['{link_afiliado}']\n"
        "affiliate_disclosure: fixture\n",
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_coin_short_shadow_enabled=True,
        aliexpress_live_api_enabled=True,
        aliexpress_app_key="key",
        aliexpress_app_secret=SECRET,
        aliexpress_tracking_id=TRACKING,
        telegram_api_id=123,
        telegram_api_hash="hash",
    )
    outcome = CoinShadowPreviewOutcome(
        preview_id=1,
        evidence_id=2,
        state="READY",
        cache_hit=False,
        correlation_mode="SOURCE_VALUE_EXACT",
        tracking_confirmed=True,
        attribution_unverified=True,
        route_preservation_manually_observed=False,
        replacement_count=1,
        content_expires_at=NOW,
        rendered_text=f"Oferta {PROMOTION}",
    )

    async def fake_run(*_args: object, **_kwargs: object) -> CoinShadowPreviewOutcome:
        return outcome

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_coin_shadow_preview", fake_run)
    result = main(
        [
            "aliexpress",
            "coin-shadow-preview",
            "--config",
            str(config),
            "--message-link",
            "https://t.me/c/1234567890/77",
            "--shadow-database",
            str(tmp_path / "shadow.sqlite3"),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 0
    assert report["content_included"] is False
    assert "rendered_text" not in report
    assert SOURCE not in captured.out + captured.err
    assert PROMOTION not in captured.out + captured.err


def test_coin_shadow_preview_cli_includes_content_only_in_explicit_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewOutcome
    from promo_bot.cli import main

    config = tmp_path / "config.yaml"
    config.write_text(
        "source_channels: [-1001234567890]\n"
        "providers:\n  aliexpress:\n    enabled: true\n    affiliate_mode: official_api\n"
        "templates: ['{link_afiliado}']\n"
        "affiliate_disclosure: fixture\n",
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_coin_short_shadow_enabled=True,
        aliexpress_live_api_enabled=True,
        aliexpress_app_key="key",
        aliexpress_app_secret=SECRET,
        aliexpress_tracking_id=TRACKING,
        telegram_api_id=123,
        telegram_api_hash="hash",
    )
    content = f"Oferta {PROMOTION}"
    outcome = CoinShadowPreviewOutcome(
        preview_id=1,
        evidence_id=2,
        state="READY",
        cache_hit=False,
        correlation_mode="POSITIONAL_SINGLETON",
        tracking_confirmed=True,
        attribution_unverified=True,
        route_preservation_manually_observed=False,
        replacement_count=1,
        content_expires_at=NOW,
        rendered_text=content,
    )

    async def fake_run(*_args: object, **_kwargs: object) -> CoinShadowPreviewOutcome:
        logging.getLogger("promo_bot.coin_fixture").info("sanitized preview ready")
        return outcome

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_coin_shadow_preview", fake_run)
    result = main(
        [
            "aliexpress",
            "coin-shadow-preview",
            "--config",
            str(config),
            "--message-link",
            "https://t.me/c/1234567890/77",
            "--shadow-database",
            str(tmp_path / "shadow.sqlite3"),
            "--include-content",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 0
    assert report["rendered_text"] == content
    assert content not in captured.err
    assert content not in caplog.text
