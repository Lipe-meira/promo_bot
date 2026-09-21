"""Gated assembly and sanitized inspection of manual SKU refinement."""

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
    AliExpressDiscoverySkuMatchModel,
    AliExpressDiscoverySkuRefinementItemModel,
    AliExpressDiscoverySkuRefinementRunModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from promo_bot.discovery.config import DiscoveryProfile
from promo_bot.discovery.runtime import assert_discovery_gates, resolve_discovery_database_path
from promo_bot.discovery.sku_runner import AliExpressSkuRefinementRunner, SkuRefinementSummary
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.discovery_sku import AliExpressDiscoverySkuGateway
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import (
    AliExpressHttpTransport,
    build_offline_safe_http_client,
)


def assert_sku_refinement_gates(settings: EnvironmentSettings, config: AppConfig) -> None:
    if not settings.aliexpress_sku_dimension_api_confirmed:
        raise ValueError("ALIEXPRESS_SKU_DIMENSION_API_NOT_CONFIRMED")
    if not settings.aliexpress_discovery_sku_shadow_enabled:
        raise ValueError("ALIEXPRESS_DISCOVERY_SKU_SHADOW_DISABLED")
    assert_discovery_gates(settings, config)


def resolve_sku_database_path(settings: EnvironmentSettings, explicit_path: Path | None) -> Path:
    return resolve_discovery_database_path(settings, explicit_path)


async def run_aliexpress_sku_refinement(
    settings: EnvironmentSettings,
    config: AppConfig,
    *,
    source_run_id: int,
    profile_name: str,
    profile: DiscoveryProfile,
    database_path: Path,
) -> SkuRefinementSummary:
    assert_sku_refinement_gates(settings, config)
    if profile.sku_refinement is None:
        raise ValueError("ALIEXPRESS_DISCOVERY_SKU_PROFILE_REQUIRED")
    app_key = _required_secret(settings.aliexpress_app_key, "ALIEXPRESS_APP_KEY")
    app_secret = _required_secret(settings.aliexpress_app_secret, "ALIEXPRESS_APP_SECRET")
    _required_secret(settings.aliexpress_tracking_id, "ALIEXPRESS_TRACKING_ID")
    await upgrade_database_async(shadow_database_url(database_path))
    database = create_affiliate_shadow_database(database_path)
    try:
        async with build_offline_safe_http_client() as http_client:
            client = AliExpressAffiliateApiClient(
                AliExpressHttpTransport(http_client, max_attempts=1, durable_retry=False),
                request_builder=AliExpressTopRequestBuilder(app_key, app_secret),
                live_enabled=settings.aliexpress_live_api_enabled,
            )
            return await AliExpressSkuRefinementRunner(
                database, AliExpressDiscoverySkuGateway(client), app_secret=app_secret
            ).refine(source_run_id, profile_name, profile)
    finally:
        await database.dispose()


async def read_sku_refinement_results(
    database_path: Path, *, run_id: int, include_skus: bool
) -> dict[str, Any]:
    if not database_path.exists():
        raise ValueError("ALIEXPRESS_DISCOVERY_SKU_DATABASE_NOT_FOUND")
    database = create_affiliate_shadow_database(database_path)
    try:
        async with database.session() as session:
            run = await session.get(AliExpressDiscoverySkuRefinementRunModel, run_id)
            if run is None:
                raise ValueError("ALIEXPRESS_DISCOVERY_SKU_RUN_NOT_FOUND")
            items = (
                await session.scalars(
                    select(AliExpressDiscoverySkuRefinementItemModel)
                    .where(AliExpressDiscoverySkuRefinementItemModel.refinement_run_id == run_id)
                    .order_by(AliExpressDiscoverySkuRefinementItemModel.rank_position)
                )
            ).all()
            report: dict[str, Any] = {
                "run_id": run.id,
                "source_run_id": run.source_run_id,
                "state": run.state,
                "stop_reason": run.stop_reason,
                "limits": {
                    "max_refined_products": run.max_refined_products,
                    "max_sku_api_calls": run.max_sku_api_calls,
                },
                "api_call_count": run.api_call_count,
                "cache_hit_count": run.cache_hit_count,
                "refined_count": run.refined_count,
                "snapshot_count": run.snapshot_count,
                "state_counts": dict(sorted(Counter(item.state for item in items).items())),
                "classification_counts": dict(
                    sorted(
                        Counter(
                            item.classification for item in items if item.classification
                        ).items()
                    )
                ),
                "error_code": run.error_code,
            }
            if include_skus:
                expanded: list[dict[str, Any]] = []
                for item in items:
                    alternatives = (
                        await session.scalars(
                            select(AliExpressDiscoverySkuMatchModel)
                            .where(AliExpressDiscoverySkuMatchModel.item_id == item.id)
                            .order_by(AliExpressDiscoverySkuMatchModel.sku_id)
                        )
                    ).all()
                    selected = next(
                        (match for match in alternatives if match.sku_id == item.selected_sku_id),
                        None,
                    )
                    expanded.append(
                        {
                            "product_id": item.product_id,
                            "sku_id": item.selected_sku_id,
                            "attributes": selected.attributes if selected is not None else None,
                            "sale_price_with_tax": (
                                str(item.sale_price_with_tax)
                                if item.sale_price_with_tax is not None
                                else None
                            ),
                            "origin": item.origin,
                            "state": item.state,
                            "classification": item.classification,
                            "rank_position": item.rank_position,
                            "history_median": (
                                str(item.history_median)
                                if item.history_median is not None
                                else None
                            ),
                            "price_drop_percent": (
                                str(item.price_drop_percent)
                                if item.price_drop_percent is not None
                                else None
                            ),
                            "alternatives": [
                                {
                                    "sku_id": match.sku_id,
                                    "attributes": match.attributes,
                                    "sale_price_with_tax": str(match.sale_price_with_tax),
                                }
                                for match in alternatives
                            ],
                        }
                    )
                report["items"] = expanded
            return report
    finally:
        await database.dispose()


def _required_secret(value: SecretStr | None, name: str) -> str:
    if value is None or not value.get_secret_value():
        raise ValueError(f"{name}_MISSING")
    return value.get_secret_value()
