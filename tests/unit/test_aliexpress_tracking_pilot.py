from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from promo_bot import cli


def documented_entry() -> str:
    document = Path(__file__).parents[2] / "docs" / "ALIEXPRESS_TRACKING_PILOT.md"
    return (
        document.read_text(encoding="utf-8").split("$pilotEntry = @'\n", 1)[1].split("\n'@", 1)[0]
    )


def run_documented_entry(monkeypatch: pytest.MonkeyPatch, env_path: Path, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["pilot", str(env_path), *args])
    with pytest.raises(SystemExit) as error:
        exec(compile(documented_entry(), "documented_pilot_entry", "exec"), {})
    return error.value.code


@pytest.mark.parametrize(
    "file_tracking,override,effective_matches,override_divergent,exit_code",
    [
        ("promo_bot_br", None, True, False, 0),
        ("promo_bot_br", "promo_bot_br", True, False, 0),
        ("other-private-file", None, False, False, 2),
        ("promo_bot_br", "other-private-override", False, True, 2),
        ("promo_bot_br", "", True, True, 2),
        ("other-private-file", "promo_bot_br", True, False, 0),
    ],
)
def test_documented_preflight_checks_effective_tracking_and_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    file_tracking: str,
    override: str | None,
    effective_matches: bool,
    override_divergent: bool,
    exit_code: int,
) -> None:
    env_path = tmp_path / "synthetic.env"
    env_path.write_text(f"ALIEXPRESS_TRACKING_ID={file_tracking}\n", encoding="utf-8")
    monkeypatch.delenv("ALIEXPRESS_TRACKING_ID", raising=False)
    if override is not None:
        monkeypatch.setenv("ALIEXPRESS_TRACKING_ID", override)

    def forbidden_main(_args: object) -> int:
        pytest.fail("preflight must not start the CLI or transports")

    monkeypatch.setattr(cli, "main", forbidden_main)
    assert run_documented_entry(monkeypatch, env_path, "--preflight") == exit_code
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "effective_tracking_matches": effective_matches,
        "override_present": override is not None,
        "override_divergent": override_divergent,
    }
    assert captured.err == ""
    for sensitive in ("promo_bot_br", "other-private-file", "other-private-override"):
        assert sensitive not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("case", ["invalid-setting", "relative", "missing"])
def test_documented_preflight_errors_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    env_path = tmp_path / "synthetic.env"
    if case != "missing":
        env_path.write_text(
            "ALIEXPRESS_TRACKING_ID=promo_bot_br\n"
            + ("DRY_RUN=private-invalid-value\n" if case == "invalid-setting" else ""),
            encoding="utf-8",
        )
    if case == "relative":
        monkeypatch.chdir(tmp_path)
        env_path = Path("synthetic.env")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("ALIEXPRESS_TRACKING_ID", raising=False)
    assert run_documented_entry(monkeypatch, env_path, "--preflight") == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"preflight_ok": False}
    assert captured.err == ""
    assert "private-invalid-value" not in captured.out


def test_documented_execution_uses_same_absolute_env_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_path = tmp_path / "authorized.env"
    env_path.write_text("ALIEXPRESS_TRACKING_ID=promo_bot_br\n", encoding="utf-8")
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (alternate / ".env").write_text("ALIEXPRESS_TRACKING_ID=other-private-file\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("source_channels: []\n", encoding="utf-8")
    monkeypatch.chdir(alternate)
    monkeypatch.delenv("ALIEXPRESS_TRACKING_ID", raising=False)
    called = []

    def fake_main(args: list[str]) -> int:
        loaded = cli.load_settings()
        assert loaded.aliexpress_tracking_id is not None
        assert loaded.aliexpress_tracking_id.get_secret_value() == "promo_bot_br"
        called.append(args)
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    args = ["aliexpress", "shadow-auto-deliver", "--config", str(config_path)]
    assert run_documented_entry(monkeypatch, env_path, *args) == 0
    assert called == [args]
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
