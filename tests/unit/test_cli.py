from pathlib import Path

import pytest

from promo_bot.cli import main


def test_validate_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-config", "--config", "config.example.yaml"]) == 0
    assert '"status": "valid"' in capsys.readouterr().out


def test_doctor_reports_python_312(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--config", "config.example.yaml"]) == 0
    assert '"python_compatible": true' in capsys.readouterr().out


def test_init_db_uses_migrations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'cli.sqlite3').as_posix()}"

    assert main(["init-db", "--database-url", url]) == 0
    assert '"status": "upgraded"' in capsys.readouterr().out


def test_run_is_controlled_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--config", "config.example.yaml"]) == 0
    captured = capsys.readouterr()
    assert "dry_run_ready" in captured.err


def test_run_rejects_external_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")

    assert main(["run", "--config", "config.example.yaml"]) == 2


def test_invalid_secret_setting_does_not_echo_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_like_invalid_value = "private-value-that-must-not-leak"
    monkeypatch.setenv("TELEGRAM_API_ID", secret_like_invalid_value)

    assert main(["validate-config", "--config", "config.example.yaml"]) == 2
    assert secret_like_invalid_value not in capsys.readouterr().err
