from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from promo_bot.cli import main
from promo_bot.providers.aliexpress.models import PriceDisplayMode
from promo_bot.relay.formatter import render_ready_aliexpress_deal


def test_aliexpress_status_discloses_only_credential_presence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALIEXPRESS_APP_KEY", "fixture-key-must-not-appear")
    monkeypatch.setenv("ALIEXPRESS_APP_SECRET", "fixture-secret-must-not-appear")
    monkeypatch.setenv("ALIEXPRESS_TRACKING_ID", "fixture-tracking-must-not-appear")

    assert main(["aliexpress", "status", "--config", "config.example.yaml"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["contract_gate"] == "confirmed"
    assert report["live_gate"] == "closed"
    assert report["live_api_enabled"] is False
    assert report["network_call"] is False
    assert report["app_key_configured"] is True
    assert report["app_secret_configured"] is True
    assert report["tracking_id_configured"] is True
    assert "fixture-key" not in output
    assert "fixture-secret" not in output
    assert "fixture-tracking" not in output


def test_aliexpress_status_reports_explicit_live_opt_in_without_calling_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALIEXPRESS_LIVE_API_ENABLED", "true")

    assert main(["aliexpress", "status", "--config", "config.example.yaml"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["contract_gate"] == "confirmed"
    assert report["live_gate"] == "open"
    assert report["live_api_enabled"] is True
    assert report["network_call"] is False


def test_aliexpress_preview_is_network_free_and_non_publishable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "aliexpress",
                "preview",
                "--config",
                "config.example.yaml",
                "--url",
                "https://pt.aliexpress.com/item/1005000000000001.html?spm=removed",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["canonical_url"] == ("https://www.aliexpress.com/item/1005000000000001.html")
    assert report["network_call"] is False
    assert report["ready_deal_created"] is False
    assert report["telegram_delivery"] is False


def test_formatter_distinguishes_range_and_omits_unverified_coupon_claims() -> None:
    rendered = render_ready_aliexpress_deal(
        title="Produto confirmado",
        price_min=Decimal("90"),
        price_max=Decimal("140"),
        price_mode=PriceDisplayMode.RANGE,
        affiliate_link="https://s.click.aliexpress.com/e/fixture",
        verified_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )
    assert "Faixa confirmada: R$ 90,00 a R$ 140,00" in rendered.text
    assert "cupom" not in rendered.text.casefold()
    assert "campanha" not in rendered.text.casefold()
    assert rendered.button_url == "https://s.click.aliexpress.com/e/fixture"
