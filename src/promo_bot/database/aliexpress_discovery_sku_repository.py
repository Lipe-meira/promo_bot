"""Isolated persistence for manual SKU refinement evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import (
    AliExpressDiscoverySkuCacheItemModel,
    AliExpressDiscoverySkuCacheModel,
    AliExpressDiscoverySkuClaimModel,
    AliExpressDiscoverySkuRefinementRunModel,
)
from promo_bot.providers.aliexpress.contracts import SKU_DETAIL
from promo_bot.providers.aliexpress.discovery_sku import (
    DiscoverySku,
    DiscoverySkuAttribute,
    DiscoverySkuPage,
)

SKU_QUERY_DOMAIN = b"aliexpress-discovery-sku-query-v1"
SKU_CACHE_TTL = timedelta(minutes=15)


class SkuClaimDisposition(StrEnum):
    CALL = "CALL"
    CACHE = "CACHE"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class SkuQueryClaim:
    disposition: SkuClaimDisposition
    lease_token: str | None = None
    cached_page: DiscoverySkuPage | None = None


def sku_query_fingerprint(app_secret: str, *, product_id: str) -> str:
    if not app_secret or re.fullmatch(r"[0-9]+", product_id) is None or int(product_id) <= 0:
        raise ValueError("ALIEXPRESS_DISCOVERY_SKU_FINGERPRINT_INPUT_INVALID")
    message = json.dumps(
        {
            "operation": SKU_DETAIL,
            "product_id": product_id,
            "ship_to_country": "BR",
            "target_currency": "BRL",
            "target_language": "PT",
            "need_deliver_info": False,
            "sku_ids": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    purpose_key = hmac.new(app_secret.encode(), SKU_QUERY_DOMAIN, hashlib.sha256).digest()
    return hmac.new(purpose_key, message.encode(), hashlib.sha256).hexdigest()


class SkuRefinementRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def create_run(
        self,
        *,
        source_run_id: int,
        profile_name: str,
        requirements_fingerprint: str,
        max_refined_products: int,
        max_sku_api_calls: int,
        minimum_drop_percent: Decimal,
        now: datetime,
        lease_until: datetime,
    ) -> int:
        row = AliExpressDiscoverySkuRefinementRunModel(
            source_run_id=source_run_id,
            profile_name=profile_name,
            requirements_fingerprint=requirements_fingerprint,
            state="RUNNING",
            started_at=now,
            lease_token=uuid4().hex,
            lease_until=lease_until,
            max_refined_products=max_refined_products,
            max_sku_api_calls=max_sku_api_calls,
            minimum_drop_percent=minimum_drop_percent,
            api_call_count=0,
            refined_count=0,
            cache_hit_count=0,
            snapshot_count=0,
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def get_run(self, run_id: int) -> AliExpressDiscoverySkuRefinementRunModel | None:
        return await self.session.get(AliExpressDiscoverySkuRefinementRunModel, run_id)

    async def claim_sku(
        self,
        *,
        run_id: int,
        sku_query_fingerprint: str,
        product_id: str,
        now: datetime,
        lease_until: datetime,
    ) -> SkuQueryClaim:
        if re.fullmatch(r"[0-9a-f]{64}", sku_query_fingerprint) is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_SKU_FINGERPRINT_INVALID")
        await self.session.execute(
            update(AliExpressDiscoverySkuRefinementRunModel)
            .where(
                AliExpressDiscoverySkuRefinementRunModel.state == "RUNNING",
                AliExpressDiscoverySkuRefinementRunModel.id != run_id,
                AliExpressDiscoverySkuRefinementRunModel.lease_until <= now,
            )
            .values(
                state="UNCERTAIN",
                finished_at=now,
                lease_token=None,
                lease_until=None,
                stop_reason="UNCERTAIN",
                error_code="ALIEXPRESS_DISCOVERY_SKU_LEASE_EXPIRED",
            )
        )
        await self.session.execute(
            delete(AliExpressDiscoverySkuClaimModel).where(
                AliExpressDiscoverySkuClaimModel.lease_until <= now
            )
        )
        active = await self.session.scalar(
            update(AliExpressDiscoverySkuRefinementRunModel)
            .where(
                AliExpressDiscoverySkuRefinementRunModel.id == run_id,
                AliExpressDiscoverySkuRefinementRunModel.state == "RUNNING",
            )
            .values(lease_until=lease_until)
            .returning(AliExpressDiscoverySkuRefinementRunModel.id)
        )
        if active is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_SKU_RUN_NOT_ACTIVE")
        expired = select(AliExpressDiscoverySkuCacheModel.sku_query_fingerprint).where(
            AliExpressDiscoverySkuCacheModel.expires_at <= now
        )
        await self.session.execute(
            delete(AliExpressDiscoverySkuCacheItemModel).where(
                AliExpressDiscoverySkuCacheItemModel.sku_query_fingerprint.in_(expired)
            )
        )
        await self.session.execute(
            delete(AliExpressDiscoverySkuCacheModel).where(
                AliExpressDiscoverySkuCacheModel.expires_at <= now
            )
        )
        cached = await self.session.get(AliExpressDiscoverySkuCacheModel, sku_query_fingerprint)
        if cached is not None:
            if cached.product_id != product_id:
                raise ValueError("ALIEXPRESS_DISCOVERY_SKU_CACHE_IDENTITY_MISMATCH")
            rows = (
                await self.session.scalars(
                    select(AliExpressDiscoverySkuCacheItemModel)
                    .where(
                        AliExpressDiscoverySkuCacheItemModel.sku_query_fingerprint
                        == sku_query_fingerprint
                    )
                    .order_by(AliExpressDiscoverySkuCacheItemModel.ordinal)
                )
            ).all()
            await self.session.execute(
                update(AliExpressDiscoverySkuRefinementRunModel)
                .where(AliExpressDiscoverySkuRefinementRunModel.id == run_id)
                .values(
                    cache_hit_count=AliExpressDiscoverySkuRefinementRunModel.cache_hit_count + 1
                )
            )
            return SkuQueryClaim(
                SkuClaimDisposition.CACHE,
                cached_page=DiscoverySkuPage(product_id, tuple(_cached_sku(row) for row in rows)),
            )
        lease_token = uuid4().hex
        inserted = await self.session.scalar(
            insert(AliExpressDiscoverySkuClaimModel)
            .values(
                sku_query_fingerprint=sku_query_fingerprint,
                owner_run_id=run_id,
                product_id=product_id,
                lease_token=lease_token,
                claimed_at=now,
                lease_until=lease_until,
            )
            .on_conflict_do_nothing(index_elements=["sku_query_fingerprint"])
            .returning(AliExpressDiscoverySkuClaimModel.sku_query_fingerprint)
        )
        if inserted is None:
            return SkuQueryClaim(SkuClaimDisposition.IN_PROGRESS)
        incremented = await self.session.scalar(
            update(AliExpressDiscoverySkuRefinementRunModel)
            .where(
                AliExpressDiscoverySkuRefinementRunModel.id == run_id,
                AliExpressDiscoverySkuRefinementRunModel.state == "RUNNING",
                AliExpressDiscoverySkuRefinementRunModel.api_call_count
                < AliExpressDiscoverySkuRefinementRunModel.max_sku_api_calls,
            )
            .values(api_call_count=AliExpressDiscoverySkuRefinementRunModel.api_call_count + 1)
            .returning(AliExpressDiscoverySkuRefinementRunModel.id)
        )
        if incremented is None:
            await self.session.execute(
                delete(AliExpressDiscoverySkuClaimModel).where(
                    AliExpressDiscoverySkuClaimModel.sku_query_fingerprint == sku_query_fingerprint,
                    AliExpressDiscoverySkuClaimModel.lease_token == lease_token,
                )
            )
            raise ValueError("ALIEXPRESS_DISCOVERY_SKU_API_BUDGET_EXHAUSTED")
        return SkuQueryClaim(SkuClaimDisposition.CALL, lease_token=lease_token)

    async def finish_sku_success(
        self,
        *,
        run_id: int,
        sku_query_fingerprint: str,
        lease_token: str | None,
        page: DiscoverySkuPage,
        now: datetime,
    ) -> None:
        if lease_token is None or not 1 <= len(page.skus) < 20:
            raise ValueError("ALIEXPRESS_DISCOVERY_SKU_CACHE_INPUT_INVALID")
        claim = await self.session.scalar(
            select(AliExpressDiscoverySkuClaimModel).where(
                AliExpressDiscoverySkuClaimModel.sku_query_fingerprint == sku_query_fingerprint,
                AliExpressDiscoverySkuClaimModel.owner_run_id == run_id,
                AliExpressDiscoverySkuClaimModel.lease_token == lease_token,
                AliExpressDiscoverySkuClaimModel.lease_until > now,
                AliExpressDiscoverySkuClaimModel.product_id == page.product_id,
            )
        )
        if claim is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_SKU_LEASE_LOST")
        self.session.add(
            AliExpressDiscoverySkuCacheModel(
                sku_query_fingerprint=sku_query_fingerprint,
                product_id=page.product_id,
                fetched_at=now,
                expires_at=now + SKU_CACHE_TTL,
                sku_count=len(page.skus),
            )
        )
        await self.session.flush()
        for ordinal, sku in enumerate(page.skus):
            self.session.add(
                AliExpressDiscoverySkuCacheItemModel(
                    sku_query_fingerprint=sku_query_fingerprint,
                    ordinal=ordinal,
                    product_id=sku.product_id,
                    sku_id=sku.sku_id,
                    currency=sku.currency,
                    price_with_tax=sku.price_with_tax,
                    sale_price_with_tax=sku.sale_price_with_tax,
                    discount_percent=sku.discount_percent,
                    attributes=[{"name": a.name, "value": a.value} for a in sku.attributes],
                )
            )
        await self.session.flush()
        await self.session.execute(
            delete(AliExpressDiscoverySkuClaimModel).where(
                AliExpressDiscoverySkuClaimModel.sku_query_fingerprint == sku_query_fingerprint,
                AliExpressDiscoverySkuClaimModel.lease_token == lease_token,
            )
        )


def _cached_sku(row: AliExpressDiscoverySkuCacheItemModel) -> DiscoverySku:
    return DiscoverySku(
        product_id=row.product_id,
        sku_id=row.sku_id,
        currency=row.currency,
        price_with_tax=row.price_with_tax,
        sale_price_with_tax=row.sale_price_with_tax,
        discount_percent=row.discount_percent,
        attributes=tuple(
            DiscoverySkuAttribute(attribute["name"], attribute["value"])
            for attribute in row.attributes
        ),
    )
