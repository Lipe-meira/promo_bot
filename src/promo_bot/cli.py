"""Phase 1 command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from promo_bot.config import ConfigLoadError, EnvironmentSettings, load_app_config
from promo_bot.database.migrations import upgrade_database
from promo_bot.database.session import Database
from promo_bot.observability import configure_logging
from promo_bot.relay.formatter import render_synthetic_test
from promo_bot.relay.models import RelayProcessingError
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.telegram.bot import SyntheticBotSender
from promo_bot.telegram.monitor import TelegramMonitor

LOGGER = logging.getLogger("promo_bot")


def default_config_path() -> Path:
    local_config = Path("config.yaml")
    return local_config if local_config.exists() else Path("config.example.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promo-bot", description="Local promotion relay")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check the local environment")
    doctor.add_argument("--config", type=Path, default=default_config_path())

    validate = subparsers.add_parser("validate-config", help="validate .env and YAML safely")
    validate.add_argument("--config", type=Path, default=default_config_path())

    init_db = subparsers.add_parser("init-db", help="upgrade the SQLite schema with Alembic")
    init_db.add_argument("--database-url", help=argparse.SUPPRESS)

    run = subparsers.add_parser("run", help="run the Phase 1 controlled dry-run")
    run.add_argument("--config", type=Path, default=default_config_path())

    listen = subparsers.add_parser("listen", help="monitor configured Telegram channels")
    listen.add_argument("--config", type=Path, default=default_config_path())
    listen.add_argument(
        "--authorize",
        action="store_true",
        help="perform the initial interactive Telethon authorization",
    )

    send_test = subparsers.add_parser("send-test", help="preview a synthetic Bot API test message")
    send_test.add_argument(
        "--live",
        action="store_true",
        help="send the fixed synthetic message to TELEGRAM_TARGET_CHAT_ID",
    )
    return parser


def load_settings() -> EnvironmentSettings:
    return EnvironmentSettings()


def safe_validation_message(exc: ValidationError) -> str:
    """Summarize validation failures without input values or documentation URLs."""

    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in exc.errors(include_url=False, include_input=False)
    )


def command_doctor(config_path: Path) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    report = {
        "status": "ok",
        "phase": 2,
        "python": platform.python_version(),
        "python_compatible": sys.version_info[:2] == (3, 12),
        "config_file": str(config_path),
        "config_valid": True,
        "provider_count": len(config.providers),
        "providers_enabled": sum(item.enabled for item in config.providers.values()),
        **settings.safe_summary(),
    }
    if not report["python_compatible"]:
        report["status"] = "error"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


def command_validate_config(config_path: Path) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    result = {
        "status": "valid",
        "config_file": str(config_path),
        "provider_count": len(config.providers),
        "dry_run": settings.dry_run,
        "search_enabled": settings.search_enabled,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def command_init_db(database_url: str | None) -> int:
    settings = load_settings()
    url = database_url or settings.resolved_database_url
    if not url.startswith("sqlite+aiosqlite:///"):
        raise ValueError("only sqlite+aiosqlite database URLs are supported")
    upgrade_database(url)
    print(json.dumps({"status": "upgraded", "database_backend": "sqlite+aiosqlite"}))
    return 0


def command_run(config_path: Path) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    configure_logging(settings.log_level)
    if not settings.dry_run:
        raise ValueError("Phase 1 can run only when DRY_RUN=true")
    LOGGER.info(
        "Phase 1 foundation ready; external integrations are disabled",
        extra={
            "stage": "startup",
            "result": "dry_run_ready",
            "product": f"configured_categories={len(config.categories)}",
        },
    )
    return 0


def command_listen(config_path: Path, *, authorize: bool) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    configure_logging(settings.log_level)
    if not settings.dry_run:
        raise ValueError("Phase 2 listen requires DRY_RUN=true")
    if settings.publish_without_affiliate:
        raise ValueError("Phase 2 forbids PUBLISH_WITHOUT_AFFILIATE=true")
    if settings.publish_real_deals:
        raise ValueError("real deal publication is blocked until the official Shopee contract gate")
    if settings.search_enabled or settings.coupon_browser_verification:
        raise ValueError("Phase 3 search and browser features must remain disabled")
    upgrade_database(settings.resolved_database_url)

    async def run() -> None:
        database = Database(settings.resolved_database_url)
        relay = DurableRelayQueue(database, config.telegram_relay)
        try:
            await TelegramMonitor(settings, config, relay).run(authorize=authorize)
        finally:
            await database.dispose()

    asyncio.run(run())
    return 0


def command_send_test(*, live: bool) -> int:
    rendered = render_synthetic_test()
    if not live:
        print(
            json.dumps(
                {
                    "status": "preview",
                    "synthetic": True,
                    "text": rendered.text,
                    "button": {
                        "label": rendered.button_label,
                        "url": rendered.button_url,
                    },
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    settings = load_settings()
    asyncio.run(SyntheticBotSender(settings).send_test())
    print(json.dumps({"status": "sent", "synthetic": True}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return command_doctor(args.config)
        if args.command == "validate-config":
            return command_validate_config(args.config)
        if args.command == "init-db":
            return command_init_db(args.database_url)
        if args.command == "run":
            return command_run(args.config)
        if args.command == "listen":
            return command_listen(args.config, authorize=args.authorize)
        if args.command == "send-test":
            return command_send_test(live=args.live)
    except ValidationError as exc:
        print(
            json.dumps(
                {"status": "error", "message": safe_validation_message(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except (ConfigLoadError, OSError, RelayProcessingError, RuntimeError, ValueError) as exc:
        print(
            json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    return 2


def entrypoint() -> None:
    raise SystemExit(main())
