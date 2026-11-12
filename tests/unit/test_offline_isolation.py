"""The offline suite must never resolve the operator's real home or runtime directory."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from promo_bot.config import EnvironmentSettings

# Evaluated at collection time, before any per-test fixture redirects the environment.
REAL_HOME = Path.home().resolve()
REAL_RUNTIME = REAL_HOME / ".promo_bot"


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root)


def test_home_and_default_runtime_resolve_inside_the_sandbox() -> None:
    settings = EnvironmentSettings(_env_file=None)

    assert Path.home().resolve() != REAL_HOME
    assert not _inside(settings.resolved_runtime_dir, REAL_RUNTIME)
    assert REAL_RUNTIME.as_posix() not in settings.resolved_database_url
    assert not _inside(settings.resolved_telegram_session_path, REAL_RUNTIME)
    assert "PROMO_BOT_DATABASE_URL" not in os.environ


def test_sandbox_matches_the_documented_default_layout() -> None:
    settings = EnvironmentSettings(_env_file=None)

    assert settings.resolved_runtime_dir == (Path.home() / ".promo_bot").resolve()


def test_tests_can_still_configure_their_own_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'own.sqlite3').as_posix()}"
    monkeypatch.setenv("PROMO_BOT_DATABASE_URL", url)

    assert EnvironmentSettings(_env_file=None).resolved_database_url == url


def test_tests_can_still_configure_their_own_runtime_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROMO_BOT_RUNTIME_DIR", str(tmp_path / "rt"))

    assert EnvironmentSettings(_env_file=None).resolved_runtime_dir == (tmp_path / "rt").resolve()
