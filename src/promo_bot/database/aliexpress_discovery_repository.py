"""Atomic shadow persistence for manual AliExpress product discovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import (
    AliExpressDiscoveryCacheProductModel,
    AliExpressDiscoveryQueryCacheModel,
    AliExpressDiscoveryQueryClaimModel,
    AliExpressDiscoveryRunModel,
)
from promo_bot.providers.aliexpress.discovery import (
    DiscoveryPage,
    DiscoveryProduct,
    ObservedPrice,
)

QUERY_DOMAIN = b"aliexpress-discovery-query-v1"
TRACKING_DOMAIN = b"aliexpress-discovery-tracking-v1"
CACHE_TTL = timedelta(minutes=60)


class DiscoveryRunState(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNCERTAIN = "UNCERTAIN"


class DiscoveryClaimDisposition(StrEnum):
    CALL = "CALL"
    CACHE = "CACHE"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class DiscoveryQueryClaim:
    disposition: DiscoveryClaimDisposition
    lease_token: str | None = None
    cached_products: tuple[DiscoveryProduct, ...] = ()
    current_record_count: int | None = None
    total_record_count: int | None = None


def discovery_query_fingerprint(
    app_secret: str,
    *,
    operation: str,
    keyword: str,
    category_ids: tuple[str, ...],
    ship_to_country: str,
    target_currency: str,
    target_language: str,
    page_no: int,
    page_size: int,
    platform_product_type: str,
) -> str:
    payload = {
        "operation": operation,
        "keyword": keyword,
        "category_ids": sorted(category_ids),
        "ship_to_country": ship_to_country,
        "target_currency": target_currency,
        "target_language": target_language,
        "page_no": page_no,
        "page_size": page_size,
        "platform_product_type": platform_product_type,
    }
    message = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _contextual_fingerprint(app_secret, QUERY_DOMAIN, message)


def discovery_tracking_fingerprint(app_secret: str, tracking_id: str) -> str:
    return _contextual_fingerprint(app_secret, TRACKING_DOMAIN, tracking_id)


def _contextual_fingerprint(app_secret: str, domain: bytes, value: str) -> str:
    if not app_secret or not value:
        raise ValueError("ALIEXPRESS_DISCOVERY_FINGERPRINT_INPUT_REQUIRED")
    purpose_key = hmac.new(app_secret.encode("utf-8"), domain, hashlib.sha256).digest()
    return hmac.new(purpose_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


class DiscoveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def create_run(
        self,
        *,
        profile_name: str,
        profile_fingerprint: str,
        page_size: int,
        max_pages: int,
        max_results: int,
        max_api_calls: int,
        minimum_drop_percent: Any,
        now: datetime,
        lease_until: datetime,
    ) -> int:
        row = AliExpressDiscoveryRunModel(
            profile_name=profile_name,
            profile_fingerprint=profile_fingerprint,
            state=DiscoveryRunState.RUNNING.value,
            started_at=now,
            lease_token=uuid4().hex,
            lease_until=lease_until,
            page_size=page_size,
            max_pages=max_pages,
            max_results=max_results,
            max_api_calls=max_api_calls,
            minimum_drop_percent=minimum_drop_percent,
            api_call_count=0,
            cache_hit_count=0,
            page_count=0,
            received_count=0,
            unique_product_count=0,
            snapshot_count=0,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def get_run(self, run_id: int) -> AliExpressDiscoveryRunModel | None:
        return await self.session.get(AliExpressDiscoveryRunModel, run_id)

    async def claim_query(
        self,
        *,
        run_id: int,
        query_fingerprint: str,
        tracking_fingerprint: str,
        query_ordinal: int,
        page_no: int,
        now: datetime,
        lease_until: datetime,
    ) -> DiscoveryQueryClaim:
        _validate_fingerprints(query_fingerprint, tracking_fingerprint)
        await self._recover_expired(now)
        await self._purge_expired_cache(now)

        cached = await self.session.get(
            AliExpressDiscoveryQueryCacheModel,
            (query_fingerprint, tracking_fingerprint),
        )
        if cached is not None and cached.expires_at > now:
            products = await self._cached_products(query_fingerprint, tracking_fingerprint)
            await self.session.execute(
                update(AliExpressDiscoveryRunModel)
                .where(AliExpressDiscoveryRunModel.id == run_id)
                .values(
                    cache_hit_count=AliExpressDiscoveryRunModel.cache_hit_count + 1,
                    page_count=AliExpressDiscoveryRunModel.page_count + 1,
                    updated_at=now,
                )
            )
            return DiscoveryQueryClaim(
                DiscoveryClaimDisposition.CACHE,
                cached_products=products,
                current_record_count=cached.current_record_count,
                total_record_count=cached.total_record_count,
            )

        lease_token = uuid4().hex
        inserted = await self.session.scalar(
            insert(AliExpressDiscoveryQueryClaimModel)
            .values(
                query_fingerprint=query_fingerprint,
                tracking_fingerprint=tracking_fingerprint,
                owner_run_id=run_id,
                query_ordinal=query_ordinal,
                page_no=page_no,
                lease_token=lease_token,
                claimed_at=now,
                lease_until=lease_until,
            )
            .on_conflict_do_nothing(index_elements=["query_fingerprint", "tracking_fingerprint"])
            .returning(AliExpressDiscoveryQueryClaimModel.query_fingerprint)
        )
        if inserted is None:
            return DiscoveryQueryClaim(DiscoveryClaimDisposition.IN_PROGRESS)

        incremented = await self.session.scalar(
            update(AliExpressDiscoveryRunModel)
            .where(
                AliExpressDiscoveryRunModel.id == run_id,
                AliExpressDiscoveryRunModel.state == DiscoveryRunState.RUNNING.value,
                AliExpressDiscoveryRunModel.api_call_count
                < AliExpressDiscoveryRunModel.max_api_calls,
            )
            .values(
                api_call_count=AliExpressDiscoveryRunModel.api_call_count + 1,
                updated_at=now,
            )
            .returning(AliExpressDiscoveryRunModel.id)
        )
        if incremented is None:
            await self.session.execute(
                delete(AliExpressDiscoveryQueryClaimModel).where(
                    AliExpressDiscoveryQueryClaimModel.query_fingerprint == query_fingerprint,
                    AliExpressDiscoveryQueryClaimModel.tracking_fingerprint == tracking_fingerprint,
                    AliExpressDiscoveryQueryClaimModel.lease_token == lease_token,
                )
            )
            raise ValueError("ALIEXPRESS_DISCOVERY_API_BUDGET_EXHAUSTED")
        return DiscoveryQueryClaim(DiscoveryClaimDisposition.CALL, lease_token=lease_token)

    async def finish_query_success(
        self,
        *,
        run_id: int,
        query_fingerprint: str,
        tracking_fingerprint: str,
        lease_token: str | None,
        page: DiscoveryPage,
        now: datetime,
    ) -> None:
        if lease_token is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_LEASE_TOKEN_REQUIRED")
        claim = await self.session.scalar(
            select(AliExpressDiscoveryQueryClaimModel).where(
                AliExpressDiscoveryQueryClaimModel.query_fingerprint == query_fingerprint,
                AliExpressDiscoveryQueryClaimModel.tracking_fingerprint == tracking_fingerprint,
                AliExpressDiscoveryQueryClaimModel.owner_run_id == run_id,
                AliExpressDiscoveryQueryClaimModel.lease_token == lease_token,
                AliExpressDiscoveryQueryClaimModel.lease_until > now,
            )
        )
        if claim is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_LEASE_LOST")
        self.session.add(
            AliExpressDiscoveryQueryCacheModel(
                query_fingerprint=query_fingerprint,
                tracking_fingerprint=tracking_fingerprint,
                fetched_at=now,
                expires_at=now + CACHE_TTL,
                item_count=len(page.products),
                current_record_count=page.current_record_count,
                total_record_count=page.total_record_count,
            )
        )
        await self.session.flush()
        for ordinal, product in enumerate(page.products):
            self.session.add(
                _cache_product_model(
                    query_fingerprint,
                    tracking_fingerprint,
                    ordinal,
                    product,
                )
            )
        await self.session.execute(
            delete(AliExpressDiscoveryQueryClaimModel).where(
                AliExpressDiscoveryQueryClaimModel.query_fingerprint == query_fingerprint,
                AliExpressDiscoveryQueryClaimModel.tracking_fingerprint == tracking_fingerprint,
                AliExpressDiscoveryQueryClaimModel.lease_token == lease_token,
            )
        )
        await self.session.execute(
            update(AliExpressDiscoveryRunModel)
            .where(AliExpressDiscoveryRunModel.id == run_id)
            .values(
                page_count=AliExpressDiscoveryRunModel.page_count + 1,
                received_count=AliExpressDiscoveryRunModel.received_count + len(page.products),
                updated_at=now,
            )
        )

    async def _recover_expired(self, now: datetime) -> None:
        expired_runs = select(AliExpressDiscoveryQueryClaimModel.owner_run_id).where(
            AliExpressDiscoveryQueryClaimModel.lease_until <= now
        )
        await self.session.execute(
            update(AliExpressDiscoveryRunModel)
            .where(
                AliExpressDiscoveryRunModel.id.in_(expired_runs),
                AliExpressDiscoveryRunModel.state == DiscoveryRunState.RUNNING.value,
            )
            .values(
                state=DiscoveryRunState.UNCERTAIN.value,
                finished_at=now,
                lease_token=None,
                lease_until=None,
                error_code="ALIEXPRESS_DISCOVERY_LEASE_EXPIRED",
                updated_at=now,
            )
        )
        await self.session.execute(
            delete(AliExpressDiscoveryQueryClaimModel).where(
                AliExpressDiscoveryQueryClaimModel.lease_until <= now
            )
        )

    async def _purge_expired_cache(self, now: datetime) -> None:
        expired = select(
            AliExpressDiscoveryQueryCacheModel.query_fingerprint,
            AliExpressDiscoveryQueryCacheModel.tracking_fingerprint,
        ).where(AliExpressDiscoveryQueryCacheModel.expires_at <= now)
        pairs = list((await self.session.execute(expired)).tuples())
        for query_fingerprint, tracking_fingerprint in pairs:
            await self.session.execute(
                delete(AliExpressDiscoveryCacheProductModel).where(
                    AliExpressDiscoveryCacheProductModel.query_fingerprint == query_fingerprint,
                    AliExpressDiscoveryCacheProductModel.tracking_fingerprint
                    == tracking_fingerprint,
                )
            )
            await self.session.execute(
                delete(AliExpressDiscoveryQueryCacheModel).where(
                    AliExpressDiscoveryQueryCacheModel.query_fingerprint == query_fingerprint,
                    AliExpressDiscoveryQueryCacheModel.tracking_fingerprint == tracking_fingerprint,
                )
            )

    async def _cached_products(
        self, query_fingerprint: str, tracking_fingerprint: str
    ) -> tuple[DiscoveryProduct, ...]:
        rows = (
            await self.session.scalars(
                select(AliExpressDiscoveryCacheProductModel)
                .where(
                    AliExpressDiscoveryCacheProductModel.query_fingerprint == query_fingerprint,
                    AliExpressDiscoveryCacheProductModel.tracking_fingerprint
                    == tracking_fingerprint,
                )
                .order_by(AliExpressDiscoveryCacheProductModel.ordinal)
            )
        ).all()
        return tuple(_discovery_product(row) for row in rows)


def _validate_fingerprints(query_fingerprint: str, tracking_fingerprint: str) -> None:
    if len(query_fingerprint) != 64 or len(tracking_fingerprint) != 64:
        raise ValueError("ALIEXPRESS_DISCOVERY_FINGERPRINT_INVALID")


def _cache_product_model(
    query_fingerprint: str,
    tracking_fingerprint: str,
    ordinal: int,
    product: DiscoveryProduct,
) -> AliExpressDiscoveryCacheProductModel:
    return AliExpressDiscoveryCacheProductModel(
        query_fingerprint=query_fingerprint,
        tracking_fingerprint=tracking_fingerprint,
        ordinal=ordinal,
        product_id=product.product_id,
        title=product.title,
        image_url=product.image_url,
        first_category_id=product.first_category_id,
        first_category_name=product.first_category_name,
        second_category_id=product.second_category_id,
        second_category_name=product.second_category_name,
        shop_id=product.shop_id,
        shop_name=product.shop_name,
        target_brl_price=product.target_brl_price,
        observed_prices=[
            {"field": price.field, "amount": str(price.amount), "currency": price.currency}
            for price in product.observed_prices
        ],
        declared_discount_percent=product.declared_discount_percent,
        commission_rate=product.commission_rate,
        hot_product_commission_rate=product.hot_product_commission_rate,
        volume=product.volume,
        completeness_score=product.completeness_score,
        diagnostics=sorted(product.diagnostics),
    )


def _discovery_product(row: AliExpressDiscoveryCacheProductModel) -> DiscoveryProduct:
    observed = tuple(
        ObservedPrice(
            item["field"],
            Decimal(item["amount"]),
            item["currency"],
        )
        for item in row.observed_prices
    )
    return DiscoveryProduct(
        product_id=row.product_id,
        title=row.title,
        image_url=row.image_url,
        first_category_id=row.first_category_id,
        first_category_name=row.first_category_name,
        second_category_id=row.second_category_id,
        second_category_name=row.second_category_name,
        shop_id=row.shop_id,
        shop_name=row.shop_name,
        target_brl_price=row.target_brl_price,
        observed_prices=observed,
        declared_discount_percent=row.declared_discount_percent,
        commission_rate=row.commission_rate,
        hot_product_commission_rate=row.hot_product_commission_rate,
        volume=row.volume,
        completeness_score=row.completeness_score,
        diagnostics=frozenset(row.diagnostics),
    )
