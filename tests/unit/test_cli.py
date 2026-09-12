import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from promo_bot.affiliate.aliexpress_conversion import AliExpressDryRunPreview
from promo_bot.cli import main
from promo_bot.config import EnvironmentSettings
from promo_bot.config.schema import AppConfig
from promo_bot.database.migrations import upgrade_database
from promo_bot.database.repositories import AffiliateShadowPreviewRepository
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.telegram.monitor import TelegramMessageReference, TelegramMonitorRunResult

EXAMPLE_CONFIG = str(Path(__file__).resolve().parents[2] / "config.example.yaml")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_local_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common CLI tests must never consume the operator's local credentials."""
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: EnvironmentSettings(_env_file=None))


def test_validate_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-config", "--config", EXAMPLE_CONFIG]) == 0
    assert '"status": "valid"' in capsys.readouterr().out


def test_doctor_reports_python_312(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--config", EXAMPLE_CONFIG]) == 0
    assert '"python_compatible": true' in capsys.readouterr().out


def test_init_db_uses_migrations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}"

    assert main(["init-db", "--database-url", url]) == 0
    assert '"status": "upgraded"' in capsys.readouterr().out


def test_run_is_controlled_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--config", EXAMPLE_CONFIG]) == 0
    captured = capsys.readouterr()
    assert "dry_run_ready" in captured.err


def test_run_rejects_external_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")

    assert main(["run", "--config", EXAMPLE_CONFIG]) == 2


def test_invalid_secret_setting_does_not_echo_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_like_invalid_value = "private-value-that-must-not-leak"
    monkeypatch.setenv("TELEGRAM_API_ID", secret_like_invalid_value)

    assert main(["validate-config", "--config", EXAMPLE_CONFIG]) == 2
    assert secret_like_invalid_value not in capsys.readouterr().err


def test_send_test_defaults_to_offline_preview(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["send-test"]) == 0
    output = capsys.readouterr().out
    assert '"status": "preview"' in output
    assert '"synthetic": true' in output
    assert "Abrir oferta" in output


def test_send_test_live_requires_local_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT_ID", raising=False)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: EnvironmentSettings(_env_file=None))

    assert main(["send-test", "--live"]) == 2


def test_listen_refuses_publish_without_affiliate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLISH_WITHOUT_AFFILIATE", "true")

    assert main(["listen", "--config", EXAMPLE_CONFIG]) == 2


def test_telegram_session_authorization_is_separate_from_listener_and_bot_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="fixture-api-hash",
        telegram_bot_token=None,
        telegram_target_chat_id=None,
    )
    calls: list[str] = []

    async def fake_authorize(received: EnvironmentSettings) -> bool:
        assert received is settings
        calls.append("authorize-session")
        return True

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_telegram_session_authorization", fake_authorize)

    assert main(["telegram", "authorize-session"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert calls == ["authorize-session"]
    assert report == {
        "listener_started": False,
        "session_created": True,
        "status": "authorized",
        "telegram_delivery": False,
    }


def test_ml_browser_status_is_offline_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: EnvironmentSettings(_env_file=None))

    assert main(["ml-browser", "status", "--config", EXAMPLE_CONFIG]) == 0
    output = capsys.readouterr().out
    assert '"status": "offline_gate"' in output
    assert '"contract_gate": "closed"' in output
    assert '"real_browser_action": false' in output
    assert '"external_disclosure_enabled": false' in output


def test_ml_browser_generate_is_preview_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: EnvironmentSettings(_env_file=None))

    assert (
        main(
            [
                "ml-browser",
                "generate",
                "--config",
                EXAMPLE_CONFIG,
                "--url",
                "https://produto.mercadolivre.com.br/MLB-123456789?utm_source=old",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"status": "preview"' in output
    assert '"affiliate_link_generated": false' in output
    assert '"browser_action": "none"' in output
    assert '"internal_delivery": "blocked"' in output
    assert '"external_disclosure": "blocked"' in output


def test_ml_browser_authorize_stops_at_contract_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: EnvironmentSettings(_env_file=None))

    assert main(["ml-browser", "authorize", "--config", EXAMPLE_CONFIG]) == 2
    assert "MERCADO_LIVRE_LIVE_BROWSER_GATE_CLOSED" in capsys.readouterr().err


def test_aliexpress_convert_preview_is_explicit_and_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: []
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates:
  - "{link_afiliado}"
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret="fixture-secret",
        aliexpress_tracking_id="fixture-tracking",
        aliexpress_live_api_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
    )
    called: list[int] = []

    async def fake_run(settings: EnvironmentSettings, source_message_id: int) -> object:
        del settings
        called.append(source_message_id)
        return AliExpressDryRunPreview(
            source_message_id=source_message_id,
            product_id="1005000000000001",
            variation_key="",
            promotion_link_type=0,
            converted_text="Oferta https://s.click.aliexpress.com/e/fixture",
            affiliate_link="https://s.click.aliexpress.com/e/fixture",
            replacement_count=1,
            cache_hit=False,
        )

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_conversion_preview", fake_run)

    assert (
        main(
            [
                "aliexpress",
                "convert-preview",
                "--config",
                str(config_path),
                "--message-id",
                "42",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert called == [42]
    assert '"status": "preview"' in output
    assert '"telegram_delivery": false' in output
    assert '"database_deal_created": false' in output
    assert "https://s.click.aliexpress.com/e/fixture" in output


def test_aliexpress_convert_preview_requires_separate_live_api_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: []
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates:
  - "{link_afiliado}"
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "promo_bot.cli.load_settings",
        lambda: EnvironmentSettings(_env_file=None, aliexpress_live_api_enabled=False),
    )

    assert (
        main(
            [
                "aliexpress",
                "convert-preview",
                "--config",
                str(config_path),
                "--message-id",
                "42",
            ]
        )
        == 2
    )
    assert "ALIEXPRESS_LIVE_API_DISABLED" in capsys.readouterr().err


def test_conversion_offline_demo_runs_end_to_end_without_settings_or_real_clients(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("offline demonstration must not read settings or build real clients")

    monkeypatch.setattr("promo_bot.cli.load_settings", forbidden)
    monkeypatch.setattr("promo_bot.cli.build_offline_safe_http_client", forbidden)
    assert main(["aliexpress", "convert-preview", "--offline-demo"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["synthetic"] is True
    assert report["evidence_source"] == "MockTransport"
    assert report["network_call"] is False
    assert report["telegram_delivery"] is False
    assert report["mock_request_count"] == 1
    assert report["duplicate_cache_hit"] is True
    assert report["replacement_count"] == 1
    assert "https://s.click.aliexpress.com/e/offline-demo" in report["converted_text"]


def test_aliexpress_telegram_shadow_preview_uses_explicit_message_link_and_shadow_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels:
  - -1001234567890
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates:
  - "{link_afiliado}"
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    shadow_database = tmp_path / "external-shadow.sqlite3"
    settings = EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="fixture-api-hash",
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret="fixture-secret",
        aliexpress_tracking_id="fixture-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )
    calls: list[tuple[TelegramMessageReference, Path]] = []

    async def fake_run(
        received_settings: EnvironmentSettings,
        config: AppConfig,
        reference: TelegramMessageReference,
        database_path: Path,
    ) -> AliExpressDryRunPreview:
        assert received_settings is settings
        assert config.source_channels == ("-1001234567890",)
        calls.append((reference, database_path))
        return AliExpressDryRunPreview(
            source_message_id=1,
            product_id="1005000000000001",
            variation_key="",
            promotion_link_type=0,
            converted_text="Oferta https://s.click.aliexpress.com/e/shadow-fixture",
            affiliate_link="https://s.click.aliexpress.com/e/shadow-fixture",
            replacement_count=1,
            cache_hit=False,
        )

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_telegram_shadow_preview", fake_run)

    assert (
        main(
            [
                "aliexpress",
                "shadow-preview",
                "--config",
                str(config_path),
                "--message-link",
                "https://t.me/c/1234567890/77",
                "--shadow-database",
                str(shadow_database),
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert calls == [
        (
            TelegramMessageReference(message_id=77, chat_id=-1001234567890),
            shadow_database.resolve(),
        )
    ]
    assert output["status"] == "shadow_preview"
    assert output["telegram_chat_id"] == "-1001234567890"
    assert output["telegram_message_id"] == 77
    assert output["telegram_delivery"] is False
    assert output["database_deal_created"] is False
    assert "shadow-fixture" in output["converted_text"]


def test_aliexpress_telegram_shadow_preview_requires_its_own_gate_before_any_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: [-1001234567890]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_enabled=False,
    )

    async def forbidden(*_args: object, **_kwargs: object) -> AliExpressDryRunPreview:
        raise AssertionError("clients must not be built while the shadow gate is closed")

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_telegram_shadow_preview", forbidden)

    assert (
        main(
            [
                "aliexpress",
                "shadow-preview",
                "--config",
                str(config_path),
                "--chat-id=-1001234567890",
                "--message-id",
                "77",
            ]
        )
        == 2
    )
    assert "ALIEXPRESS_TELEGRAM_SHADOW_DISABLED" in capsys.readouterr().err


def test_cli_entrypoint_awaits_shadow_migration_before_external_clients(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: [-1001234567890]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    shadow_database = tmp_path / "shadow.sqlite3"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ALIEXPRESS_", "TELEGRAM_", "PROMO_BOT_"))
        and key
        not in {
            "DRY_RUN",
            "PUBLISH_REAL_DEALS",
            "PUBLISH_WITHOUT_AFFILIATE",
            "SEARCH_ENABLED",
            "COUPON_BROWSER_VERIFICATION",
        }
    }
    environment.update(
        {
            "ALIEXPRESS_APP_KEY": "fixture-key",
            "ALIEXPRESS_APP_SECRET": "fixture-secret",
            "ALIEXPRESS_TRACKING_ID": "fixture-tracking",
            "ALIEXPRESS_LIVE_API_ENABLED": "true",
            "ALIEXPRESS_TELEGRAM_SHADOW_ENABLED": "true",
            "DRY_RUN": "true",
            "PUBLISH_REAL_DEALS": "false",
            "PUBLISH_WITHOUT_AFFILIATE": "false",
            "SEARCH_ENABLED": "false",
            "COUPON_BROWSER_VERIFICATION": "false",
            "PROMO_BOT_RUNTIME_DIR": str(tmp_path / "runtime"),
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    (str(PROJECT_ROOT / "src"), environment.get("PYTHONPATH")),
                )
            ),
            "PYTHONWARNINGS": "default",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from promo_bot.cli import entrypoint; entrypoint()",
            "aliexpress",
            "shadow-preview",
            "--config",
            str(config_path),
            "--message-link",
            "https://t.me/c/1234567890/77",
            "--shadow-database",
            str(shadow_database),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "TELEGRAM_API_ID and TELEGRAM_API_HASH are required" in result.stderr
    assert "asyncio.run() cannot be called from a running event loop" not in result.stderr
    assert "was never awaited" not in result.stderr
    with sqlite3.connect(shadow_database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision is not None


def test_shadow_listener_requires_every_bounded_limit() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "aliexpress",
                "shadow-listen",
                "--max-messages",
                "1",
                "--run-seconds",
                "60",
            ]
        )


def test_shadow_auto_delivery_requires_every_bounded_limit() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "aliexpress",
                "shadow-auto-deliver",
                "--destination",
                "private-test",
                "--max-messages",
                "1",
                "--run-seconds",
                "60",
                "--max-api-calls",
                "1",
                "--max-links-per-message",
                "3",
            ]
        )


def test_shadow_auto_delivery_uses_exclusive_gate_and_reports_bounded_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: [-1001234567890]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "-1009876543210"
      kind: private_channel
templates: ["{link_afiliado}"]
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
    received: list[object] = []

    async def fake_run(*args: object) -> TelegramMonitorRunResult:
        received.extend(args)
        return TelegramMonitorRunResult(
            status="limit_reached",
            stop_reason="max_messages",
            messages_received=1,
            api_calls=1,
            processed=1,
            rejected=0,
            failed=0,
            cache_hits=0,
            previews_created=1,
            rejection_codes=(),
            error_code=None,
            send_messages=1,
            deliveries_sent=1,
        )

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_shadow_auto_delivery", fake_run)

    result = main(
        [
            "aliexpress",
            "shadow-auto-deliver",
            "--config",
            str(config_path),
            "--shadow-database",
            str(tmp_path / "auto.sqlite3"),
            "--destination",
            "private-test",
            "--max-messages",
            "1",
            "--run-seconds",
            "60",
            "--max-api-calls",
            "1",
            "--max-links-per-message",
            "3",
            "--max-send-messages",
            "1",
        ]
    )

    assert result == 0
    assert len(received) == 6
    output = json.loads(capsys.readouterr().out)
    assert output["messages_received"] == 1
    assert output["api_calls"] == 1
    assert output["send_messages"] == 1
    assert output["deliveries_sent"] == 1
    assert output["production_publication"] is False


def test_shadow_listener_uses_separate_gate_and_reports_only_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: [-1001234567890]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="fixture-api-hash",
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret="fixture-secret",
        aliexpress_tracking_id="fixture-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_listener_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )
    received: list[object] = []

    async def fake_run(*args: object) -> TelegramMonitorRunResult:
        received.extend(args)
        return TelegramMonitorRunResult(
            status="timeout",
            stop_reason="timeout",
            messages_received=0,
            api_calls=0,
            processed=0,
            rejected=0,
            failed=0,
            cache_hits=0,
            previews_created=0,
            rejection_codes=(),
            error_code=None,
        )

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_telegram_shadow_listener", fake_run)

    result = main(
        [
            "aliexpress",
            "shadow-listen",
            "--config",
            str(config_path),
            "--shadow-database",
            str(tmp_path / "listener.sqlite3"),
            "--max-messages",
            "1",
            "--run-seconds",
            "60",
            "--max-api-calls",
            "1",
        ]
    )

    assert result == 0
    assert len(received) == 4
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "api_calls": 0,
        "cache_hits": 0,
        "error_code": None,
        "failed": 0,
        "messages_received": 0,
        "previews_created": 0,
        "processed": 0,
        "rejected": 0,
        "rejection_codes": [],
        "status": "timeout",
        "stop_reason": "timeout",
        "telegram_delivery": False,
        "database_deal_created": False,
    }


def test_shadow_listener_closed_gate_prevents_runtime_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source_channels: [-1001234567890]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_listener_enabled=False,
    )

    async def forbidden(*_args: object) -> TelegramMonitorRunResult:
        raise AssertionError("runtime must not be built while the listener gate is closed")

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)
    monkeypatch.setattr("promo_bot.cli.run_aliexpress_telegram_shadow_listener", forbidden)

    result = main(
        [
            "aliexpress",
            "shadow-listen",
            "--config",
            str(config_path),
            "--max-messages",
            "1",
            "--run-seconds",
            "60",
            "--max-api-calls",
            "1",
        ]
    )

    assert result == 2
    assert "ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_DISABLED" in capsys.readouterr().err


def test_shadow_listener_entrypoint_reports_completed_counters_and_queryable_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    product_id = "1005000000000001"
    canonical = f"https://www.aliexpress.com/item/{product_id}.html"
    affiliate = "https://s.click.aliexpress.com/e/cli-listener-fixture"
    config_path = tmp_path / "config.yaml"
    database_path = tmp_path / "entrypoint-shadow.sqlite3"
    config_path.write_text(
        """
source_channels: [-1001234567890]
providers:
  aliexpress:
    enabled: true
    affiliate_mode: official_api
telegram_relay:
  queue_max_size: 1
templates: ["{link_afiliado}"]
affiliate_disclosure: "fixture"
""".strip(),
        encoding="utf-8",
    )
    settings = EnvironmentSettings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="fixture-api-hash",
        aliexpress_app_key="fixture-key",
        aliexpress_app_secret="fixture-secret",
        aliexpress_tracking_id="fixture-tracking",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_listener_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )

    class Message:
        id = 501
        date = datetime(2026, 9, 10, 12, tzinfo=UTC)
        raw_text = f"Oferta {canonical}"
        out = False
        buttons = None

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
                await callback(SimpleNamespace(chat_id=-1001234567890, message=Message()))

            self.handler_tasks.append(asyncio.create_task(emit()))

        def remove_event_handler(self, _callback: object, _builder: object) -> None:
            return None

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
                "request_id": "cli-listener-request",
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
    monkeypatch.setattr(
        "promo_bot.telegram.monitor.utils.get_peer_id",
        lambda _entity: -1001234567890,
    )

    assert (
        main(
            [
                "aliexpress",
                "shadow-listen",
                "--config",
                str(config_path),
                "--shadow-database",
                str(database_path),
                "--max-messages",
                "1",
                "--run-seconds",
                "1",
                "--max-api-calls",
                "1",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["messages_received"] == 1
    assert summary["processed"] == 1
    assert summary["rejected"] == 0
    assert summary["failed"] == 0
    assert summary["cache_hits"] == 0
    assert summary["previews_created"] == 1
    assert summary["api_calls"] == 1
    assert summary["rejection_codes"] == []
    assert summary["error_code"] is None

    assert (
        main(
            [
                "aliexpress",
                "shadow-previews",
                "list",
                "--shadow-database",
                str(database_path),
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["previews"]) == 1
    assert listed["previews"][0]["source_message_id"] == 1


def test_shadow_preview_list_hides_content_and_show_requires_explicit_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "previews.sqlite3"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    upgrade_database(url)
    database = create_affiliate_shadow_database(path)

    async def seed() -> None:
        async with database.session() as session:
            await AffiliateShadowPreviewRepository(session).save_ready(
                provider="aliexpress_official",
                store="aliexpress",
                source_message_id=1,
                affiliate_proof_id=1,
                replacement_count=1,
                cache_hit=False,
                affiliate_host="s.click.aliexpress.com",
                rendered_text="SECRET PREVIEW TEXT",
                affiliate_link="https://s.click.aliexpress.com/e/secret-preview",
                created_at=datetime.now(UTC),
                content_ttl=timedelta(hours=24),
            )

    asyncio.run(seed())
    asyncio.run(database.dispose())
    settings = EnvironmentSettings(_env_file=None, PROMO_BOT_RUNTIME_DIR=tmp_path / "runtime")
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: settings)

    assert (
        main(
            [
                "aliexpress",
                "shadow-previews",
                "list",
                "--shadow-database",
                str(path),
            ]
        )
        == 0
    )
    listed = capsys.readouterr().out
    assert "SECRET PREVIEW TEXT" not in listed
    assert "secret-preview" not in listed
    preview_id = json.loads(listed)["previews"][0]["id"]

    assert (
        main(
            [
                "aliexpress",
                "shadow-previews",
                "show",
                "--preview-id",
                str(preview_id),
                "--shadow-database",
                str(path),
            ]
        )
        == 0
    )
    hidden = capsys.readouterr().out
    assert "SECRET PREVIEW TEXT" not in hidden
    assert "secret-preview" not in hidden

    assert (
        main(
            [
                "aliexpress",
                "shadow-previews",
                "show",
                "--preview-id",
                str(preview_id),
                "--include-content",
                "--shadow-database",
                str(path),
            ]
        )
        == 0
    )
    included = capsys.readouterr().out
    assert "SECRET PREVIEW TEXT" in included
    assert "secret-preview" in included
