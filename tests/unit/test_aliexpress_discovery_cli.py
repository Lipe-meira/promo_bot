from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from promo_bot.cli import main
from promo_bot.config.settings import EnvironmentSettings


def write_config(path: Path) -> None:
    path.write_text(
        """
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )


def write_profiles(path: Path) -> None:
    path.write_text(
        """
version: 1
profiles:
  hardware-gamer-br:
    keywords: ["sensitive-keyword"]
    category_ids: []
    ship_to_country: BR
    target_currency: BRL
    target_language: PT
    page_size: 5
    max_pages: 2
    max_results: 25
    max_api_calls: 4
    minimum_price_drop_percent: 5
""".strip(),
        encoding="utf-8",
    )


def test_discovery_gate_is_closed_by_default() -> None:
    settings = EnvironmentSettings(_env_file=None)

    assert settings.aliexpress_discovery_shadow_enabled is False
    assert settings.safe_summary()["aliexpress_discovery_shadow_enabled"] is False


def test_closed_gate_prevents_runtime_or_transport_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    profiles_path = tmp_path / "profiles.yaml"
    write_config(config_path)
    write_profiles(profiles_path)
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_live_api_enabled=True,
        aliexpress_discovery_shadow_enabled=False,
        dry_run=True,
        publish_real_deals=False,
    )

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runtime must not be created while gate is closed")

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_discovery_scan", forbidden)

    result = main(
        [
            "aliexpress",
            "discovery-scan",
            "--config",
            str(config_path),
            "--profiles",
            str(profiles_path),
            "--profile",
            "hardware-gamer-br",
            "--shadow-database",
            str(tmp_path / "shadow.sqlite3"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "ALIEXPRESS_DISCOVERY_SHADOW_DISABLED" in captured.err
    assert "sensitive-keyword" not in captured.err


def test_real_entrypoint_uses_mock_top_only_and_sanitizes_default_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    profiles_path = tmp_path / "profiles.yaml"
    database_path = tmp_path / "discovery.sqlite3"
    write_config(config_path)
    write_profiles(profiles_path)
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_app_key="synthetic-key",
        aliexpress_app_secret="synthetic-secret",
        aliexpress_tracking_id="synthetic-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_discovery_shadow_enabled=True,
        dry_run=True,
        publish_real_deals=False,
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "aliexpress_affiliate_product_query_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "result": {
                            "current_record_count": 1,
                            "total_record_count": 1,
                            "products": [
                                {
                                    "product_id": "1005000000000001",
                                    "product_title": "private product title",
                                    "target_sale_price": "79.90",
                                    "target_sale_price_currency": "BRL",
                                    "discount": "20%",
                                    "commission_rate": "4",
                                    "lastest_volume": "50",
                                }
                            ],
                        },
                    }
                }
            },
            request=request,
        )

    @asynccontextmanager
    async def fake_http_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            yield client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "promo_bot.discovery.runtime.build_offline_safe_http_client", fake_http_client
    )

    result = main(
        [
            "aliexpress",
            "discovery-scan",
            "--config",
            str(config_path),
            "--profiles",
            str(profiles_path),
            "--profile",
            "hardware-gamer-br",
            "--shadow-database",
            str(database_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert len(requests) == 1
    assert requests[0].method == "POST"
    report = json.loads(captured.out)
    assert report["api_call_count"] == 1
    assert report["unique_product_count"] == 1
    assert report["stop_reason"] == "SHORT_PAGE"
    for forbidden in (
        "sensitive-keyword",
        "synthetic-tracking",
        "private product title",
        "1005000000000001",
        "target_sale_price",
    ):
        assert forbidden not in captured.out
        assert forbidden not in captured.err

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM deals").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM deliveries").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM affiliate_candidates").fetchone() == (0,)

    assert (
        main(
            [
                "aliexpress",
                "discovery-results",
                "--run-id",
                str(report["run_id"]),
                "--shadow-database",
                str(database_path),
            ]
        )
        == 0
    )
    hidden = capsys.readouterr()
    assert "private product title" not in hidden.out
    assert "1005000000000001" not in hidden.out

    assert (
        main(
            [
                "aliexpress",
                "discovery-results",
                "--run-id",
                str(report["run_id"]),
                "--shadow-database",
                str(database_path),
                "--include-products",
            ]
        )
        == 0
    )
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["products"][0]["product_id"] == "1005000000000001"
    assert explicit["products"][0]["title"] == "private product title"
