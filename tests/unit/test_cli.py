from pathlib import Path

import pytest

from promo_bot.cli import main
from promo_bot.config import EnvironmentSettings

EXAMPLE_CONFIG = str(Path(__file__).resolve().parents[2] / "config.example.yaml")


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
