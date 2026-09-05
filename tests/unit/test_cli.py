import json
from pathlib import Path

import pytest

from promo_bot.affiliate.aliexpress_conversion import AliExpressDryRunPreview
from promo_bot.cli import main
from promo_bot.config import EnvironmentSettings
from promo_bot.config.schema import AppConfig
from promo_bot.telegram.monitor import TelegramMessageReference

EXAMPLE_CONFIG = str(Path(__file__).resolve().parents[2] / "config.example.yaml")


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
