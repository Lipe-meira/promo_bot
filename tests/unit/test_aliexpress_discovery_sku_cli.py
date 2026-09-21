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
        "providers:\n  aliexpress:\n    enabled: true\n    affiliate_mode: official_api\n"
        'templates: ["{link_afiliado}"]\naffiliate_disclosure: "fixture"\n',
        encoding="utf-8",
    )


def write_profiles(path: Path) -> None:
    path.write_text(
        """version: 1
profiles:
  hardware-gamer-br:
    keywords: [private-keyword]
    ship_to_country: BR
    target_currency: BRL
    target_language: PT
    page_size: 5
    max_pages: 1
    max_results: 5
    max_api_calls: 1
    minimum_price_drop_percent: 5
    sku_refinement:
      max_refined_products: 1
      max_sku_api_calls: 1
      requirements:
        - dimension: capacity
          property_names: [ROM]
          accepted_values: [1 TB]
""",
        encoding="utf-8",
    )


def test_sku_gates_default_closed_and_block_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.yaml"
    profiles_path = tmp_path / "profiles.yaml"
    write_config(config_path)
    write_profiles(profiles_path)
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_live_api_enabled=True,
        aliexpress_discovery_shadow_enabled=True,
        dry_run=True,
        publish_real_deals=False,
    )
    assert settings.aliexpress_sku_dimension_api_confirmed is False
    assert settings.aliexpress_discovery_sku_shadow_enabled is False
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)

    result = main(
        [
            "aliexpress",
            "discovery-sku-refine",
            "--config",
            str(config_path),
            "--profiles",
            str(profiles_path),
            "--profile",
            "hardware-gamer-br",
            "--run-id",
            "1",
            "--shadow-database",
            str(tmp_path / "shadow.sqlite3"),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "ALIEXPRESS_SKU_DIMENSION_API_NOT_CONFIRMED" in captured.err
    assert "private-keyword" not in captured.err
    assert not (tmp_path / "shadow.sqlite3").exists()


def test_entrypoint_mock_top_sanitizes_sku_default_and_explicit_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path = tmp_path / "config.yaml"
    profiles_path = tmp_path / "profiles.yaml"
    database_path = tmp_path / "shadow.sqlite3"
    write_config(config_path)
    write_profiles(profiles_path)
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_app_key="synthetic-key",
        aliexpress_app_secret="synthetic-secret",
        aliexpress_tracking_id="synthetic-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_discovery_shadow_enabled=True,
        aliexpress_sku_dimension_api_confirmed=True,
        aliexpress_discovery_sku_shadow_enabled=True,
        dry_run=True,
        publish_real_deals=False,
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        method = request.url.params["method"]
        if method == "aliexpress.affiliate.product.query":
            payload = {
                "aliexpress_affiliate_product_query_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "result": {
                            "current_record_count": 1,
                            "total_record_count": 1,
                            "products": [
                                {
                                    "product_id": "1005000000000001",
                                    "product_title": "private-product-title",
                                    "target_sale_price": "194.21",
                                    "target_sale_price_currency": "BRL",
                                }
                            ],
                        },
                    }
                }
            }
        else:
            assert method == "aliexpress.affiliate.product.sku.detail.get"
            payload = {
                "code": "0",
                "result": {
                    "code": "0",
                    "result": {
                        "ae_item_info": {"product_id": "1005000000000001"},
                        "ae_item_sku_info": [
                            {
                                "sku_id": "120000000000001",
                                "currency": "BRL",
                                "sale_price_with_tax": "219.90",
                                "sku_properties": '{"ROM":"1 TB"}',
                            }
                        ],
                    },
                },
            }
        return httpx.Response(200, json=payload, request=request)

    @asynccontextmanager
    async def fake_http_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
        ) as client:
            yield client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr(
        "promo_bot.discovery.runtime.build_offline_safe_http_client", fake_http_client
    )
    monkeypatch.setattr(
        "promo_bot.discovery.sku_runtime.build_offline_safe_http_client", fake_http_client
    )
    assert (
        main(
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
        == 0
    )
    source_run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert (
        main(
            [
                "aliexpress",
                "discovery-sku-refine",
                "--config",
                str(config_path),
                "--profiles",
                str(profiles_path),
                "--profile",
                "hardware-gamer-br",
                "--run-id",
                str(source_run_id),
                "--shadow-database",
                str(database_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["api_call_count"] == 1
    assert report["snapshot_count"] == 1
    for sensitive in (
        "private-keyword",
        "private-product-title",
        "1005000000000001",
        "120000000000001",
        "219.90",
        "synthetic-tracking",
        "synthetic-key",
        "synthetic-secret",
        "1 TB",
        "https://",
    ):
        assert sensitive not in output.out
        assert sensitive not in output.err
        assert sensitive not in caplog.text
    assert len(requests) == 2
    assert all(request.method == "POST" for request in requests)

    args = [
        "aliexpress",
        "discovery-sku-results",
        "--run-id",
        str(report["run_id"]),
        "--shadow-database",
        str(database_path),
    ]
    assert main(args) == 0
    hidden = capsys.readouterr()
    assert "120000000000001" not in hidden.out
    assert "219.90" not in hidden.out
    assert main([*args, "--include-skus"]) == 0
    explicit = capsys.readouterr()
    item = json.loads(explicit.out)["items"][0]
    assert item["product_id"] == "1005000000000001"
    assert item["sku_id"] == "120000000000001"
    assert item["sale_price_with_tax"] == "219.90"
    assert item["attributes"] == [{"name": "ROM", "value": "1 TB"}]
    assert "120000000000001" not in explicit.err
    assert "120000000000001" not in caplog.text
    assert len(requests) == 2
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM deals").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM deliveries").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM affiliate_candidates").fetchone() == (0,)
