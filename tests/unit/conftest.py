"""Unit-test settings never consume the operator's implicit .env file."""

import pytest

from promo_bot.config import EnvironmentSettings


@pytest.fixture(autouse=True)
def disable_implicit_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit temporary _env_file fixtures still exercise dotenv parsing.
    monkeypatch.setitem(EnvironmentSettings.model_config, "env_file", None)
