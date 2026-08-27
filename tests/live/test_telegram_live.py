"""Explicitly gated real Telegram checks. Never part of the default test run."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from telethon import TelegramClient  # type: ignore[import-untyped]

from promo_bot.config import EnvironmentSettings, load_app_config
from promo_bot.telegram.bot import SyntheticBotSender

pytestmark = [pytest.mark.live, pytest.mark.enable_socket]


def require_live_authorization() -> None:
    if os.getenv("RUN_TELEGRAM_LIVE_TEST") != "1":
        pytest.skip("set RUN_TELEGRAM_LIVE_TEST=1 only after explicit authorization")


@pytest.mark.asyncio
async def test_telethon_can_read_one_configured_channel_without_marking_read() -> None:
    require_live_authorization()
    settings = EnvironmentSettings()
    config = load_app_config(Path("config.yaml"))
    assert settings.telegram_api_id is not None
    assert settings.telegram_api_hash is not None
    assert config.source_channels
    client = TelegramClient(
        str(settings.resolved_telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
    )
    await client.connect()
    try:
        assert await client.is_user_authorized()
        messages = await client.get_messages(config.source_channels[0], limit=1)
        assert len(messages) <= 1
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_bot_api_sends_only_the_fixed_synthetic_message() -> None:
    require_live_authorization()
    await SyntheticBotSender(EnvironmentSettings()).send_test()
