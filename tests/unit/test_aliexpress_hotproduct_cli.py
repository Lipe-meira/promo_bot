from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from promo_bot.cli import main
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.runtime import assert_hotproduct_limits


def _config(path: Path) -> None:
    path.write_text(
        "providers:\n  aliexpress:\n    enabled: true\n    affiliate_mode: official_api\n"
        'templates: ["{link_afiliado}"]\naffiliate_disclosure: "fixture"\n',
        encoding="utf-8",
    )


def _profiles(
    path: Path,
    *,
    page_size: int = 5,
    max_pages: int = 1,
    max_results: int = 10,
    max_api_calls: int = 2,
) -> None:
    path.write_text(
        "version: 1\nprofiles:\n  safe-hot:\n    keywords: [ssd]\n"
        "    category_ids: []\n    ship_to_country: BR\n"
        "    target_currency: BRL\n    target_language: PT\n"
        f"    page_size: {page_size}\n    max_pages: {max_pages}\n"
        f"    max_results: {max_results}\n    max_api_calls: {max_api_calls}\n"
        "    minimum_price_drop_percent: 5\n",
        encoding="utf-8",
    )


def _settings(*, hot_enabled: bool) -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        aliexpress_app_key="synthetic-key",
        aliexpress_app_secret="synthetic-secret",
        aliexpress_tracking_id="synthetic-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_discovery_shadow_enabled=True,
        aliexpress_discovery_hotproduct_shadow_enabled=hot_enabled,
        dry_run=True,
        publish_real_deals=False,
    )


def test_hot_gate_defaults_closed_and_blocks_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        EnvironmentSettings(_env_file=None).aliexpress_discovery_hotproduct_shadow_enabled is False
    )
    config_path, profiles_path = tmp_path / "config.yaml", tmp_path / "profiles.yaml"
    _config(config_path)
    _profiles(profiles_path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(hot_enabled=False))

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("transport constructed behind closed gate")

    monkeypatch.setattr("promo_bot.cli.run_aliexpress_discovery_scan", forbidden)
    assert (
        main(
            [
                "aliexpress",
                "discovery-scan",
                "--source",
                "hotproduct",
                "--config",
                str(config_path),
                "--profiles",
                str(profiles_path),
                "--profile",
                "safe-hot",
                "--shadow-database",
                str(tmp_path / "shadow.sqlite3"),
            ]
        )
        == 2
    )
    assert "ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_DISABLED" in capsys.readouterr().err


def test_hot_oversized_profile_stops_before_transport_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path, profiles_path = tmp_path / "config.yaml", tmp_path / "profiles.yaml"
    database_path = tmp_path / "shadow.sqlite3"
    _config(config_path)
    _profiles(profiles_path, page_size=6)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(hot_enabled=True))

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runtime must not be constructed")

    monkeypatch.setattr("promo_bot.cli.run_aliexpress_discovery_scan", forbidden)
    assert (
        main(
            [
                "aliexpress",
                "discovery-scan",
                "--source",
                "hotproduct",
                "--config",
                str(config_path),
                "--profiles",
                str(profiles_path),
                "--profile",
                "safe-hot",
                "--shadow-database",
                str(database_path),
            ]
        )
        == 2
    )
    assert "ALIEXPRESS_DISCOVERY_HOTPRODUCT_BUDGET_EXCEEDED" in capsys.readouterr().err
    assert not database_path.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (("page_size", 6), ("max_pages", 2), ("max_api_calls", 3), ("max_results", 11)),
)
def test_hot_budget_rejects_oversized_profile(field: str, value: int) -> None:
    fields: dict[str, object] = {
        "keywords": ("ssd",),
        "category_ids": (),
        "ship_to_country": "BR",
        "target_currency": "BRL",
        "target_language": "PT",
        "page_size": 5,
        "max_pages": 1,
        "max_results": 10,
        "max_api_calls": 2,
        "minimum_price_drop_percent": Decimal("5"),
    }
    fields[field] = value
    with pytest.raises(ValueError, match="ALIEXPRESS_DISCOVERY_HOTPRODUCT_BUDGET_EXCEEDED"):
        assert_hotproduct_limits(DiscoveryProfile(**fields))


def test_hot_entrypoint_uses_one_mock_top_and_reports_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path, profiles_path = tmp_path / "config.yaml", tmp_path / "profiles.yaml"
    database_path = tmp_path / "shadow.sqlite3"
    _config(config_path)
    _profiles(profiles_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "aliexpress_affiliate_hotproduct_query_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "result": {
                            "current_record_count": 1,
                            "total_record_count": 1,
                            "products": [
                                {
                                    "product_id": "1005000000000001",
                                    "product_title": "private title",
                                    "target_sale_price": "79.90",
                                    "target_sale_price_currency": "BRL",
                                    "discount": "20%",
                                    "promotion_link": "https://example.invalid/secret",
                                }
                            ],
                        },
                    },
                }
            },
            request=request,
        )

    @asynccontextmanager
    async def fake_http_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
        ) as client:
            yield client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(hot_enabled=True))
    monkeypatch.setattr(
        "promo_bot.discovery.runtime.build_offline_safe_http_client", fake_http_client
    )
    assert (
        main(
            [
                "aliexpress",
                "discovery-scan",
                "--source",
                "hotproduct",
                "--config",
                str(config_path),
                "--profiles",
                str(profiles_path),
                "--profile",
                "safe-hot",
                "--shadow-database",
                str(database_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["source_operation"] == "aliexpress.affiliate.hotproduct.query"
    assert report["api_call_count"] == len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.params["method"] == "aliexpress.affiliate.hotproduct.query"
    for forbidden in ("synthetic-tracking", "private title", "example.invalid", "1005000000000001"):
        assert forbidden not in captured.out + captured.err + caplog.text
    capsys.readouterr()
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
    products = json.loads(capsys.readouterr().out)["products"]
    assert products[0]["source_operation"] == "aliexpress.affiliate.hotproduct.query"
    assert products[0]["origin"] == "LIVE"


def test_source_defaults_to_product_query_without_hot_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, profiles_path = tmp_path / "config.yaml", tmp_path / "profiles.yaml"
    _config(config_path)
    _profiles(profiles_path)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(hot_enabled=False))

    async def fake_run(*_args: object, **kwargs: object) -> object:
        assert kwargs["source_operation"] == "aliexpress.affiliate.product.query"
        raise RuntimeError("reached product-query runtime")

    monkeypatch.setattr("promo_bot.cli.run_aliexpress_discovery_scan", fake_run)
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
                "safe-hot",
                "--shadow-database",
                str(tmp_path / "shadow.sqlite3"),
            ]
        )
        == 2
    )
