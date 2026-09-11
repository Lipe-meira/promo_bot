"""Explicit one-shot shadow delivery; no production factory or provider integration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from promo_bot.affiliate.shadow_delivery import (
    ShadowDeliveryRejected,
    ShadowDeliveryService,
    assert_shadow_delivery_gates,
)
from promo_bot.config.loader import load_app_config
from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import resolve_shadow_database_path
from promo_bot.observability.shadow import mute_shadow_payload_logs
from promo_bot.telegram.shadow_bot import ShadowBotTransport


async def run_shadow_delivery(
    settings: EnvironmentSettings, config: AppConfig, path: Path, preview_id: int, destination: str
) -> dict[str, object]:
    # Deliberately require existing, migrated shadow storage. Never initialize the main DB.
    if not path.is_file():
        raise ShadowDeliveryRejected("SHADOW_DATABASE_NOT_FOUND")
    database = create_affiliate_shadow_database(path)
    assert settings.telegram_bot_token is not None
    report: dict[str, object] | None = None
    try:
        async with ShadowBotTransport(settings.telegram_bot_token.get_secret_value()) as transport:
            report = await ShadowDeliveryService(database, transport, settings, config).deliver(
                preview_id, destination, confirm=True
            )
    except Exception:
        if report is None:
            raise
        # Cleanup cannot erase evidence of an attempted/confirmed external side effect.
        report["error_code"] = "SHADOW_CLEANUP_FAILED"
    finally:
        try:
            await database.dispose()
        except Exception:
            if report is None:
                raise
            report["error_code"] = "SHADOW_CLEANUP_FAILED"
    return report


def command_shadow_deliver(
    *,
    load_settings: Callable[[], EnvironmentSettings],
    config_path: Path,
    database_path: Path | None,
    preview_id: int,
    destination: str,
    confirm: bool,
) -> int:
    try:
        with mute_shadow_payload_logs():
            settings = load_settings()
            assert_shadow_delivery_gates(settings, confirm=confirm)
            config = load_app_config(config_path)
            path = resolve_shadow_database_path(settings, database_path)
            report = asyncio.run(
                run_shadow_delivery(settings, config, path, preview_id, destination)
            )
    except KeyboardInterrupt:
        report = {
            "status": "uncertain",
            "error_code": "SHADOW_DELIVERY_INTERRUPTED",
            "external_side_effect": None,
            "get_chat_attempts": None,
            "send_message_attempts": None,
            "production_publication": False,
        }
    except ShadowDeliveryRejected as exc:
        report = {
            "status": "failed_safe",
            "error_code": str(exc),
            "external_side_effect": False,
            "get_chat_attempts": 0,
            "send_message_attempts": 0,
            "production_publication": False,
        }
    except Exception:
        # Neither configuration validation nor SQL/transport errors may echo bound input.
        report = {
            "status": "failed_safe",
            "error_code": "SHADOW_DELIVERY_PREFLIGHT_FAILED",
            "external_side_effect": False,
            "get_chat_attempts": 0,
            "send_message_attempts": 0,
            "production_publication": False,
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "sent" and report.get("error_code") is None else 2
