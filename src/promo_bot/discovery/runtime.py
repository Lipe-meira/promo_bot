"""Runtime assembly and sanitized inspection for manual discovery shadow runs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.models import (
    AliExpressDiscoveryRunModel,
    AliExpressDiscoveryRunResultModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import resolve_shadow_database_path, shadow_database_url
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.scanner import AliExpressDiscoveryScanner, DiscoveryRunSummary
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.discovery import AliExpressProductQueryGateway
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import (
    AliExpressHttpTransport,
    build_offline_safe_http_client,
)


def canonical_discovery_product_url(product_id: str) -> str:
    if (
        not isinstance(product_id, str)
        or not product_id
        or not product_id.isascii()
        or not product_id.isdecimal()
        or int(product_id) <= 0
    ):
        raise ValueError("ALIEXPRESS_DISCOVERY_PRODUCT_ID_INVALID")
    return f"https://pt.aliexpress.com/item/{product_id}.html"


def assert_discovery_gates(settings: EnvironmentSettings, config: AppConfig) -> None:
    provider = config.providers.get("aliexpress")
    if provider is None or not provider.enabled or provider.affiliate_mode != "official_api":
        raise ValueError("ALIEXPRESS_OFFICIAL_PROVIDER_DISABLED")
    if not settings.aliexpress_discovery_shadow_enabled:
        raise ValueError("ALIEXPRESS_DISCOVERY_SHADOW_DISABLED")
    if not settings.aliexpress_live_api_enabled:
        raise ValueError("ALIEXPRESS_LIVE_API_DISABLED")
    if not settings.dry_run or settings.publish_real_deals:
        raise ValueError("ALIEXPRESS_DISCOVERY_SAFETY_GATE_CLOSED")
    _required_secret(settings.aliexpress_app_key, "ALIEXPRESS_APP_KEY")
    _required_secret(settings.aliexpress_app_secret, "ALIEXPRESS_APP_SECRET")
    _required_secret(settings.aliexpress_tracking_id, "ALIEXPRESS_TRACKING_ID")


def resolve_discovery_database_path(
    settings: EnvironmentSettings, explicit_path: Path | None = None
) -> Path:
    selected = explicit_path or (
        settings.resolved_runtime_dir / "shadow" / "aliexpress-discovery.sqlite3"
    )
    return resolve_shadow_database_path(settings, selected)


async def run_aliexpress_discovery_scan(
    settings: EnvironmentSettings,
    *,
    profile_name: str,
    profile: DiscoveryProfile,
    database_path: Path,
) -> DiscoveryRunSummary:
    app_key = _required_secret(settings.aliexpress_app_key, "ALIEXPRESS_APP_KEY")
    app_secret = _required_secret(settings.aliexpress_app_secret, "ALIEXPRESS_APP_SECRET")
    tracking_id = _required_secret(settings.aliexpress_tracking_id, "ALIEXPRESS_TRACKING_ID")
    await upgrade_database_async(shadow_database_url(database_path))
    database = create_affiliate_shadow_database(database_path)
    try:
        async with build_offline_safe_http_client() as http_client:
            client = AliExpressAffiliateApiClient(
                AliExpressHttpTransport(http_client, max_attempts=1, durable_retry=False),
                request_builder=AliExpressTopRequestBuilder(app_key, app_secret),
                live_enabled=settings.aliexpress_live_api_enabled,
            )
            gateway = AliExpressProductQueryGateway(client, tracking_id=tracking_id)
            return await AliExpressDiscoveryScanner(database, gateway).scan(
                profile_name=profile_name,
                profile=profile,
                app_secret=app_secret,
                tracking_id=tracking_id,
            )
    finally:
        await database.dispose()


async def read_discovery_results(
    database_path: Path,
    *,
    run_id: int,
    include_products: bool,
) -> dict[str, Any]:
    if not database_path.exists():
        raise ValueError("ALIEXPRESS_DISCOVERY_DATABASE_NOT_FOUND")
    database = create_affiliate_shadow_database(database_path)
    try:
        async with database.session() as session:
            run = await session.get(AliExpressDiscoveryRunModel, run_id)
            if run is None:
                raise ValueError("ALIEXPRESS_DISCOVERY_RUN_NOT_FOUND")
            products = (
                await session.scalars(
                    select(AliExpressDiscoveryRunResultModel)
                    .where(AliExpressDiscoveryRunResultModel.run_id == run_id)
                    .order_by(
                        AliExpressDiscoveryRunResultModel.total_score.desc(),
                        AliExpressDiscoveryRunResultModel.product_id,
                    )
                )
            ).all()
            classifications = Counter(product.classification for product in products)
            report: dict[str, Any] = {
                "run_id": run.id,
                "state": run.state,
                "stop_reason": run.stop_reason,
                "limits": {
                    "page_size": run.page_size,
                    "max_pages": run.max_pages,
                    "max_results": run.max_results,
                    "max_api_calls": run.max_api_calls,
                },
                "api_call_count": run.api_call_count,
                "cache_hit_count": run.cache_hit_count,
                "page_count": run.page_count,
                "received_count": run.received_count,
                "unique_product_count": run.unique_product_count,
                "snapshot_count": run.snapshot_count,
                "classification_counts": dict(sorted(classifications.items())),
                "error_code": run.error_code,
            }
            if include_products:
                report["products"] = [
                    {
                        "product_id": product.product_id,
                        "canonical_product_url": canonical_discovery_product_url(
                            product.product_id
                        ),
                        "title": product.title,
                        "price_brl": (
                            str(product.target_brl_price)
                            if product.target_brl_price is not None
                            else None
                        ),
                        "score": product.total_score,
                        "classification": product.classification,
                        "origin": product.origin,
                    }
                    for product in products
                ]
            return report
    finally:
        await database.dispose()


def _required_secret(value: SecretStr | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name}_MISSING")
    secret = value.get_secret_value()
    if not secret:
        raise ValueError(f"{name}_MISSING")
    return secret
