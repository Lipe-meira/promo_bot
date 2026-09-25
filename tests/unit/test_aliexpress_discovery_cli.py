from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from promo_bot.cli import main
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.discovery.runtime import canonical_discovery_product_url
from promo_bot.discovery.scanner import DiscoveryRunSummary


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


@pytest.mark.parametrize(
    "product_id",
    ("", "0", "-1", "١٢٣", "12x", "1?tracking=old", "1/2"),
)
def test_canonical_product_url_rejects_unvalidated_product_ids(product_id: str) -> None:
    with pytest.raises(ValueError, match="ALIEXPRESS_DISCOVERY_PRODUCT_ID_INVALID"):
        canonical_discovery_product_url(product_id)


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
    caplog: pytest.LogCaptureFixture,
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
    transport_context_count = 0

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
        nonlocal transport_context_count
        transport_context_count += 1
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
    assert "price_notice" not in json.loads(hidden.out)
    assert "private product title" not in hidden.out
    assert "1005000000000001" not in hidden.out
    assert "https://" not in hidden.out
    assert "https://" not in hidden.err

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
    explicit_output = capsys.readouterr()
    explicit = json.loads(explicit_output.out)
    assert "product-level" in explicit["price_notice"]
    assert explicit["products"][0]["product_id"] == "1005000000000001"
    assert explicit["products"][0]["title"] == "private product title"
    canonical_url = explicit["products"][0]["canonical_product_url"]
    assert canonical_url == "https://pt.aliexpress.com/item/1005000000000001.html"
    parsed = urlsplit(canonical_url)
    assert parsed.query == ""
    assert parsed.fragment == ""
    assert "tracking" not in canonical_url.casefold()
    assert "https://" not in explicit_output.err
    assert "https://" not in caplog.text
    assert transport_context_count == 1
    assert len(requests) == 1

    try:
        links_result = main(
            [
                "aliexpress",
                "discovery-results",
                "--run-id",
                str(report["run_id"]),
                "--shadow-database",
                str(database_path),
                "--format",
                "links",
            ]
        )
    except SystemExit as exc:
        links_result = exc.code
    links_output = capsys.readouterr()
    assert links_result == 0
    assert "https://pt.aliexpress.com/item/1005000000000001.html" in links_output.out
    assert "private product title" in links_output.out
    assert "BRL 79.90" in links_output.out
    assert "aliexpress.affiliate.product.query" in links_output.out
    assert "product-level" in links_output.out
    assert "SKU" in links_output.out
    assert "queda histórica" in links_output.out
    assert "https://" not in links_output.err
    assert transport_context_count == 1
    assert len(requests) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM deals").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM deliveries").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM affiliate_candidates").fetchone() == (0,)


@pytest.mark.parametrize(
    ("output_format", "encoding", "expected_title"),
    [
        ("json", "cp1252", ""),
        ("links", "cp1252", "Título \\U0001f680"),
        ("links", "utf-8", "Título 🚀"),
    ],
)
def test_discovery_results_handles_unicode_console_encoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    encoding: str,
    expected_title: str,
) -> None:
    from promo_bot import cli

    title = "Título\n\t🚀"
    report = {
        "run_id": 7,
        "source_operation": "aliexpress.affiliate.hotproduct.query",
        "price_notice": "Preço product-level; não é preço de SKU nem prova de queda histórica.",
        "products": [
            {
                "product_id": "123",
                "canonical_product_url": "https://pt.aliexpress.com/item/123.html",
                "title": title,
                "price_brl": None,
                "source_operation": "aliexpress.affiliate.hotproduct.query",
            }
        ],
    }

    async def fake_results(*_args: object, **_kwargs: object) -> dict[str, object]:
        return report

    async def forbidden_scan(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("results must not run scanner or transport")

    monkeypatch.setattr(cli, "load_settings", lambda: EnvironmentSettings(_env_file=None))
    monkeypatch.setattr(cli, "read_discovery_results", fake_results)
    monkeypatch.setattr(cli, "run_aliexpress_discovery_scan", forbidden_scan)

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding=encoding, errors="strict")
    stderr = io.StringIO()
    args = [
        "aliexpress",
        "discovery-results",
        "--run-id",
        "7",
        "--shadow-database",
        str(tmp_path / "synthetic.sqlite3"),
    ]
    if output_format == "json":
        args.append("--include-products")
    else:
        args.extend(("--format", "links"))
    try:
        with redirect_stdout(stream), redirect_stderr(stderr):
            result = main(args)
    except SystemExit as exc:
        result = exc.code
    stream.flush()
    rendered = buffer.getvalue().decode(encoding)

    assert result == 0
    assert stderr.getvalue() == ""
    assert not (tmp_path / "synthetic.sqlite3").exists()
    if output_format == "json":
        assert json.loads(rendered)["products"][0]["title"] == title
        assert "\\ud83d\\ude80" in rendered
    else:
        assert expected_title in rendered
        assert "indisponível" in rendered
        assert "https://pt.aliexpress.com/item/123.html" in rendered
        assert "product-level" in rendered


def test_non_completed_scan_returns_nonzero_exit_code(
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
        aliexpress_app_key="synthetic-key",
        aliexpress_app_secret="synthetic-secret",
        aliexpress_tracking_id="synthetic-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_discovery_shadow_enabled=True,
        dry_run=True,
        publish_real_deals=False,
    )

    async def fake_scan(*_args: object, **_kwargs: object) -> DiscoveryRunSummary:
        return DiscoveryRunSummary(
            run_id=17,
            state="UNCERTAIN",
            stop_reason="UNCERTAIN",
            api_call_count=1,
            cache_hit_count=0,
            page_count=0,
            received_count=0,
            unique_product_count=0,
            snapshot_count=0,
            error_code="ALIEXPRESS_RETRY_EXHAUSTED",
        )

    async def fake_results(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "run_id": 17,
            "state": "UNCERTAIN",
            "stop_reason": "UNCERTAIN",
            "error_code": "ALIEXPRESS_RETRY_EXHAUSTED",
        }

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_discovery_scan", fake_scan)
    monkeypatch.setattr("promo_bot.cli.read_discovery_results", fake_results)

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

    assert result == 2
    assert json.loads(capsys.readouterr().out)["state"] == "UNCERTAIN"
