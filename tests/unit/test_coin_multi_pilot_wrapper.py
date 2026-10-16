"""Execute only the documented preflight, with synthetic .env and config."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_coin_listener_pilot import _config_file


@pytest.mark.parametrize(
    "override,invalid,allowed",
    [
        (None, False, True),
        ("promo_bot_br", False, True),
        ("other-synthetic", False, False),
        ("", False, False),
        (None, True, False),
    ],
)
def test_documented_file_wrapper_preflight_is_sanitized_and_detects_overrides(
    tmp_path, override, invalid, allowed
):
    document = (
        Path(__file__).parents[2] / "docs" / "ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md"
    ).read_text(encoding="utf-8")
    code = document.split("$pilotEntry = @'\n", 1)[1].split("\n'@", 1)[0]
    script = tmp_path / "synthetic-pilot.py"
    script.write_text(code, encoding="utf-8")
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        "\n".join(
            [
                "ALIEXPRESS_TRACKING_ID=promo_bot_br",
                "ALIEXPRESS_APP_KEY=synthetic-key",
                "ALIEXPRESS_APP_SECRET=synthetic-secret",
                "TELEGRAM_API_ID=" + ("invalid" if invalid else "123"),
                "TELEGRAM_API_HASH=synthetic-hash",
                "TELEGRAM_BOT_TOKEN=123:synthetic-token",
                "DRY_RUN=true",
            ]
        ),
        encoding="utf-8",
    )
    config = _config_file(tmp_path)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("ALIEXPRESS_", "TELEGRAM_", "PUBLISH_", "SEARCH_", "COUPON_", "PROMO_BOT_")
        )
        and key.upper() != "DRY_RUN"
    }
    if override is not None:
        environment["ALIEXPRESS_TRACKING_ID"] = override
    result = subprocess.run(
        [sys.executable, str(script), str(env_file), str(config), "-1001234567890", "--preflight"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (0 if allowed else 2)
    report = json.loads(result.stdout)
    assert report["preflight_ok"] is allowed
    assert result.stderr == ""
    for value in (
        "promo_bot_br",
        "other-synthetic",
        "synthetic-secret",
        "synthetic-token",
        "synthetic-hash",
        "-1001234567890",
    ):
        assert value not in result.stdout + result.stderr
    assert not list(tmp_path.glob("*.sqlite3"))
