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

from pydantic import SecretStr, ValidationError

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionSafety,
    AliExpressDryRunPreview,
    AliExpressMessageConversionService,
)
from promo_bot.config import ConfigLoadError, EnvironmentSettings, load_app_config
from promo_bot.database.migrations import upgrade_database
from promo_bot.database.session import Database
from promo_bot.domain.enums import RelayLinkState, Store
from promo_bot.observability import configure_logging
from promo_bot.providers.aliexpress.client import LIVE_API_DISABLED, AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.models import AliExpressProductReference
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import (
    AliExpressHttpTransport,
    build_offline_safe_http_client,
)
from promo_bot.providers.mercadolivre.models import MercadoLivreProductReference
from promo_bot.relay.formatter import render_synthetic_test
from promo_bot.relay.models import RelayProcessingError
from promo_bot.relay.queue import DurableRelayQueue
from promo_bot.stores.urls import canonicalize_store_url
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

    ml_browser = subparsers.add_parser(
        "ml-browser", help="inspect the gated Mercado Livre browser integration"
    )
    ml_actions = ml_browser.add_subparsers(dest="ml_browser_command", required=True)
    ml_status = ml_actions.add_parser("status", help="show offline-safe browser gate status")
    ml_status.add_argument("--config", type=Path, default=default_config_path())
    ml_generate = ml_actions.add_parser(
        "generate", help="preview canonical input without opening a browser"
    )
    ml_generate.add_argument("--config", type=Path, default=default_config_path())
    ml_generate.add_argument("--url", required=True)
    ml_generate.add_argument("--label")
    ml_authorize = ml_actions.add_parser(
        "authorize", help="report the live authorization gate without opening a browser"
    )
    ml_authorize.add_argument("--config", type=Path, default=default_config_path())

    aliexpress = subparsers.add_parser(
        "aliexpress", help="inspect the offline-gated AliExpress Affiliate integration"
    )
    aliexpress_actions = aliexpress.add_subparsers(dest="aliexpress_command", required=True)
    aliexpress_status = aliexpress_actions.add_parser(
        "status", help="show credential presence and contract gate without revealing values"
    )
    aliexpress_status.add_argument("--config", type=Path, default=default_config_path())
    aliexpress_preview = aliexpress_actions.add_parser(
        "preview", help="canonicalize one product without calling AliExpress"
    )
    aliexpress_preview.add_argument("--config", type=Path, default=default_config_path())
    aliexpress_preview.add_argument("--url", required=True)
    aliexpress_convert = aliexpress_actions.add_parser(
        "convert-preview",
        help="generate one inspectable dry-run preview from a persisted message",
    )
    aliexpress_convert.add_argument("--config", type=Path, default=default_config_path())
    conversion_input = aliexpress_convert.add_mutually_exclusive_group(required=True)
    conversion_input.add_argument("--message-id", type=int)
    conversion_input.add_argument(
        "--offline-demo",
        action="store_true",
        help="run synthetic MockTransport preview with an ephemeral database; no .env",
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


def command_ml_browser_status(config_path: Path) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    provider = config.providers.get("mercadolivre")
    browser = provider.browser if provider is not None else None
    print(
        json.dumps(
            {
                "status": "offline_gate",
                "provider_enabled": bool(provider and provider.enabled),
                "affiliate_mode": provider.affiliate_mode if provider else "disabled",
                "browser_enabled": bool(
                    browser and browser.enabled and settings.mercadolivre_browser_enabled
                ),
                "headless": bool(
                    browser and browser.headless and settings.mercadolivre_browser_headless
                ),
                "execution_mode": browser.execution_mode if browser else "collect_only",
                "confirmed_affiliate_host_count": (
                    len(browser.allowed_affiliate_hosts) if browser else 0
                ),
                "registered_label_count": len(browser.registered_labels) if browser else 0,
                "profile_dir": str(settings.resolved_mercadolivre_browser_profile_dir),
                "contract_gate": "closed",
                "real_browser_action": False,
                "internal_delivery_enabled": False,
                "external_disclosure_enabled": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def command_ml_browser_generate(config_path: Path, *, url: str, label: str | None) -> int:
    settings = load_settings()
    if not settings.dry_run or settings.publish_real_deals:
        raise ValueError("Mercado Livre preview requires DRY_RUN=true and PUBLISH_REAL_DEALS=false")
    config = load_app_config(config_path)
    provider = config.providers.get("mercadolivre")
    browser = provider.browser if provider is not None else None
    canonical = canonicalize_store_url(url)
    if (
        canonical.state is not RelayLinkState.PENDING_AFFILIATE
        or canonical.store is not Store.MERCADOLIVRE
        or canonical.external_product_id is None
        or canonical.canonical_url is None
    ):
        raise ValueError("URL is not a canonicalizable Mercado Livre product")
    reference = MercadoLivreProductReference(
        external_product_id=canonical.external_product_id,
        canonical_url=canonical.canonical_url,
    )
    registered_labels = browser.registered_labels if browser else ()
    if label is not None and label not in registered_labels:
        raise ValueError("label must already exist in the configured registered label list")
    print(
        json.dumps(
            {
                "status": "preview",
                "store": Store.MERCADOLIVRE.value,
                "external_product_id": reference.external_product_id,
                "canonical_url": reference.canonical_url,
                "label_selected": label is not None,
                "browser_action": "none",
                "affiliate_link_generated": False,
                "internal_delivery": "blocked",
                "external_disclosure": "blocked",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def command_ml_browser_authorize(config_path: Path) -> int:
    load_app_config(config_path)
    raise ValueError("MERCADO_LIVRE_LIVE_BROWSER_GATE_CLOSED")


def command_aliexpress_status(config_path: Path) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    provider = config.providers.get("aliexpress")
    summary = settings.safe_summary()
    print(
        json.dumps(
            {
                "status": "offline_gate",
                "provider_enabled": bool(provider and provider.enabled),
                "app_key_configured": summary["aliexpress_app_key_configured"],
                "app_secret_configured": summary["aliexpress_app_secret_configured"],
                "tracking_id_configured": summary["aliexpress_tracking_id_configured"],
                "contract_gate": "confirmed",
                "live_gate": "open" if settings.aliexpress_live_api_enabled else "closed",
                "live_api_enabled": settings.aliexpress_live_api_enabled,
                "gate_error_code": None
                if settings.aliexpress_live_api_enabled
                else LIVE_API_DISABLED,
                "network_call": False,
                "affiliate_link_generated": False,
                "ready_deal_created": False,
                "telegram_delivery": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def command_aliexpress_preview(config_path: Path, *, url: str) -> int:
    settings = load_settings()
    if (
        not settings.dry_run
        or settings.publish_real_deals
        or settings.publish_without_affiliate
        or settings.search_enabled
    ):
        raise ValueError("AliExpress preview requires all offline safety flags")
    load_app_config(config_path)
    canonical = canonicalize_store_url(url)
    if (
        canonical.state is not RelayLinkState.PENDING_AFFILIATE
        or canonical.store is not Store.ALIEXPRESS
        or canonical.external_product_id is None
        or canonical.canonical_url is None
    ):
        raise ValueError("URL is not a canonicalizable AliExpress product")
    sku_id = (canonical.variation_key or "").removeprefix("sku_id:") or None
    reference = AliExpressProductReference(
        external_product_id=canonical.external_product_id,
        canonical_url=canonical.canonical_url,
        requested_sku_id=sku_id,
    )
    print(
        json.dumps(
            {
                "status": "preview",
                "store": Store.ALIEXPRESS.value,
                "external_product_id": reference.external_product_id,
                "canonical_url": reference.canonical_url,
                "sku_selected": reference.requested_sku_id is not None,
                "contract_gate": "confirmed",
                "live_gate": "closed",
                "network_call": False,
                "affiliate_link_generated": False,
                "ready_deal_created": False,
                "telegram_delivery": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


async def run_aliexpress_conversion_preview(
    settings: EnvironmentSettings,
    source_message_id: int,
) -> AliExpressDryRunPreview:
    app_key = _required_aliexpress_secret(settings.aliexpress_app_key, "ALIEXPRESS_APP_KEY")
    app_secret = _required_aliexpress_secret(
        settings.aliexpress_app_secret,
        "ALIEXPRESS_APP_SECRET",
    )
    tracking_id = _required_aliexpress_secret(
        settings.aliexpress_tracking_id,
        "ALIEXPRESS_TRACKING_ID",
    )
    database = Database(settings.resolved_database_url)
    try:
        async with build_offline_safe_http_client() as http_client:
            api_client = AliExpressAffiliateApiClient(
                AliExpressHttpTransport(http_client, max_attempts=1, durable_retry=True),
                request_builder=AliExpressTopRequestBuilder(app_key, app_secret),
                live_enabled=settings.aliexpress_live_api_enabled,
            )
            service = AliExpressMessageConversionService(
                database,
                api_client,
                app_key=app_key,
                app_secret=app_secret,
                tracking_id=tracking_id,
                safety=AliExpressConversionSafety(
                    dry_run=settings.dry_run,
                    publish_real_deals=settings.publish_real_deals,
                    publish_without_affiliate=settings.publish_without_affiliate,
                    search_enabled=settings.search_enabled,
                ),
            )
            return await service.convert(source_message_id)
    finally:
        await database.dispose()


def command_aliexpress_convert_preview(config_path: Path, *, source_message_id: int) -> int:
    settings = load_settings()
    config = load_app_config(config_path)
    provider = config.providers.get("aliexpress")
    if provider is None or not provider.enabled or provider.affiliate_mode != "official_api":
        raise ValueError("ALIEXPRESS_OFFICIAL_PROVIDER_DISABLED")
    AliExpressConversionSafety(
        dry_run=settings.dry_run,
        publish_real_deals=settings.publish_real_deals,
        publish_without_affiliate=settings.publish_without_affiliate,
        search_enabled=settings.search_enabled,
    )
    if not settings.aliexpress_live_api_enabled:
        raise ValueError(LIVE_API_DISABLED)
    preview = asyncio.run(run_aliexpress_conversion_preview(settings, source_message_id))
    print(json.dumps(preview.explicit_output(), ensure_ascii=False, sort_keys=True))
    return 0


def _required_aliexpress_secret(value: SecretStr | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name}_MISSING")
    secret = value.get_secret_value()
    if not isinstance(secret, str) or not secret:
        raise ValueError(f"{name}_MISSING")
    return secret


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
        if args.command == "ml-browser":
            if args.ml_browser_command == "status":
                return command_ml_browser_status(args.config)
            if args.ml_browser_command == "generate":
                return command_ml_browser_generate(args.config, url=args.url, label=args.label)
            if args.ml_browser_command == "authorize":
                return command_ml_browser_authorize(args.config)
        if args.command == "aliexpress":
            if args.aliexpress_command == "status":
                return command_aliexpress_status(args.config)
            if args.aliexpress_command == "preview":
                return command_aliexpress_preview(args.config, url=args.url)
            if args.aliexpress_command == "convert-preview":
                if args.offline_demo:
                    from promo_bot.affiliate.aliexpress_demo import run_offline_conversion_demo

                    report = asyncio.run(run_offline_conversion_demo())
                    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                    return 0
                return command_aliexpress_convert_preview(
                    args.config,
                    source_message_id=args.message_id,
                )
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
