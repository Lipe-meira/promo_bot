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

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import (
    AliExpressDiscoveryCacheProductModel,
    AliExpressDiscoveryPriceSnapshotModel,
    AliExpressDiscoveryQueryCacheModel,
    AliExpressDiscoveryQueryClaimModel,
    AliExpressDiscoveryRunModel,
    AliExpressDiscoveryRunResultModel,
)
from promo_bot.discovery.ranking import (
    DiscoveryScore,
    HistoricalPrice,
    rank_discovery_product,
)
from promo_bot.providers.aliexpress.contracts import PRODUCT_QUERY
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


@dataclass(frozen=True, slots=True)
class RecordedProduct:
    inserted_unique: bool
    snapshot_created: bool


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

    async def abandon_query(
        self,
        *,
        query_fingerprint: str,
        tracking_fingerprint: str,
        lease_token: str | None,
    ) -> None:
        if lease_token is None:
            return
        await self.session.execute(
            delete(AliExpressDiscoveryQueryClaimModel).where(
                AliExpressDiscoveryQueryClaimModel.query_fingerprint == query_fingerprint,
                AliExpressDiscoveryQueryClaimModel.tracking_fingerprint == tracking_fingerprint,
                AliExpressDiscoveryQueryClaimModel.lease_token == lease_token,
            )
        )

    async def record_product(
        self,
        *,
        run_id: int,
        query_fingerprint: str,
        tracking_fingerprint: str,
        product: DiscoveryProduct,
        origin: str,
        observed_at: datetime,
    ) -> RecordedProduct:
        if origin not in {"LIVE", "CACHE"}:
            raise ValueError("ALIEXPRESS_DISCOVERY_RESULT_ORIGIN_INVALID")
        run = await self.session.get(AliExpressDiscoveryRunModel, run_id)
        if run is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_RUN_NOT_FOUND")
        existing = await self.session.scalar(
            select(AliExpressDiscoveryRunResultModel).where(
                AliExpressDiscoveryRunResultModel.run_id == run_id,
                AliExpressDiscoveryRunResultModel.product_id == product.product_id,
            )
        )
        if existing is not None:
            existing.matched_query_count += 1
            if existing.origin == "LIVE" or origin == "CACHE":
                return RecordedProduct(False, False)
            score = await self._rank_product(run, product, observed_at)
            snapshot = await self._create_snapshot(
                run_id,
                query_fingerprint,
                tracking_fingerprint,
                product,
                observed_at,
            )
            _replace_result(
                existing,
                product,
                "LIVE",
                snapshot.id if snapshot else None,
                score,
            )
            if snapshot is not None:
                run.snapshot_count += 1
            return RecordedProduct(False, snapshot is not None)

        score = await self._rank_product(run, product, observed_at)
        snapshot = None
        if origin == "LIVE":
            snapshot = await self._create_snapshot(
                run_id,
                query_fingerprint,
                tracking_fingerprint,
                product,
                observed_at,
            )
        row = AliExpressDiscoveryRunResultModel(
            run_id=run_id,
            product_id=product.product_id,
            origin=origin,
            snapshot_id=snapshot.id if snapshot else None,
            title=product.title,
            image_url=product.image_url,
            target_brl_price=product.target_brl_price,
            currency="BRL" if product.target_brl_price is not None else None,
            observed_prices=_observed_prices_json(product),
            declared_discount_percent=product.declared_discount_percent,
            commission_rate=product.commission_rate,
            volume=product.volume,
            history_median=score.history_median,
            history_snapshot_count=score.history_snapshot_count,
            price_drop_percent=score.price_drop_percent,
            minimum_drop_percent=run.minimum_drop_percent,
            history_score=score.history_score,
            discount_score=score.discount_score,
            volume_score=score.volume_score,
            commission_score=score.commission_score,
            completeness_score=score.completeness_score,
            total_score=score.total_score,
            classification=score.classification,
            matched_query_count=1,
        )
        self.session.add(row)
        run.unique_product_count += 1
        if snapshot is not None:
            run.snapshot_count += 1
        await self.session.flush()
        return RecordedProduct(True, snapshot is not None)

    async def _rank_product(
        self,
        run: AliExpressDiscoveryRunModel,
        product: DiscoveryProduct,
        observed_at: datetime,
    ) -> DiscoveryScore:
        rows = (
            await self.session.execute(
                select(
                    AliExpressDiscoveryPriceSnapshotModel.price,
                    AliExpressDiscoveryPriceSnapshotModel.observed_at,
                ).where(
                    AliExpressDiscoveryPriceSnapshotModel.product_id == product.product_id,
                    AliExpressDiscoveryPriceSnapshotModel.run_id != run.id,
                    AliExpressDiscoveryPriceSnapshotModel.observed_at
                    >= observed_at - timedelta(days=30),
                    AliExpressDiscoveryPriceSnapshotModel.observed_at < observed_at,
                )
            )
        ).all()
        snapshots = tuple(HistoricalPrice(Decimal(row.price), row.observed_at) for row in rows)
        return rank_discovery_product(
            product,
            snapshots,
            Decimal(run.minimum_drop_percent),
        )

    async def _create_snapshot(
        self,
        run_id: int,
        query_fingerprint: str,
        tracking_fingerprint: str,
        product: DiscoveryProduct,
        observed_at: datetime,
    ) -> AliExpressDiscoveryPriceSnapshotModel | None:
        if product.target_brl_price is None:
            return None
        snapshot = AliExpressDiscoveryPriceSnapshotModel(
            run_id=run_id,
            query_fingerprint=query_fingerprint,
            tracking_fingerprint=tracking_fingerprint,
            product_id=product.product_id,
            price=product.target_brl_price,
            currency="BRL",
            observed_at=observed_at,
            source_operation=PRODUCT_QUERY,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def finish_run(
        self,
        run_id: int,
        *,
        state: DiscoveryRunState,
        now: datetime,
        stop_reason: str,
        error_code: str | None = None,
    ) -> None:
        if state is DiscoveryRunState.RUNNING:
            raise ValueError("ALIEXPRESS_DISCOVERY_TERMINAL_STATE_REQUIRED")
        transitioned = await self.session.scalar(
            update(AliExpressDiscoveryRunModel)
            .where(
                AliExpressDiscoveryRunModel.id == run_id,
                AliExpressDiscoveryRunModel.state == DiscoveryRunState.RUNNING.value,
            )
            .values(
                state=state.value,
                finished_at=now,
                lease_token=None,
                lease_until=None,
                stop_reason=stop_reason,
                error_code=error_code,
                updated_at=now,
            )
            .returning(AliExpressDiscoveryRunModel.id)
        )
        if transitioned is None:
            raise ValueError("ALIEXPRESS_DISCOVERY_RUN_TRANSITION_CONFLICT")

    async def _recover_expired(self, now: datetime) -> None:
        expired_claim_owners = select(AliExpressDiscoveryQueryClaimModel.owner_run_id).where(
            AliExpressDiscoveryQueryClaimModel.lease_until <= now
        )
        await self.session.execute(
            update(AliExpressDiscoveryRunModel)
            .where(
                AliExpressDiscoveryRunModel.state == DiscoveryRunState.RUNNING.value,
                or_(
                    AliExpressDiscoveryRunModel.lease_until <= now,
                    AliExpressDiscoveryRunModel.id.in_(expired_claim_owners),
                ),
            )
            .values(
                state=DiscoveryRunState.UNCERTAIN.value,
                finished_at=now,
                lease_token=None,
                lease_until=None,
                stop_reason=DiscoveryRunState.UNCERTAIN.value,
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
        observed_prices=_observed_prices_json(product),
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


def _observed_prices_json(product: DiscoveryProduct) -> list[dict[str, str]]:
    return [
        {"field": price.field, "amount": str(price.amount), "currency": price.currency}
        for price in product.observed_prices
    ]


def _replace_result(
    row: AliExpressDiscoveryRunResultModel,
    product: DiscoveryProduct,
    origin: str,
    snapshot_id: int | None,
    score: DiscoveryScore,
) -> None:
    row.origin = origin
    row.snapshot_id = snapshot_id
    row.title = product.title
    row.image_url = product.image_url
    row.target_brl_price = product.target_brl_price
    row.currency = "BRL" if product.target_brl_price is not None else None
    row.observed_prices = _observed_prices_json(product)
    row.declared_discount_percent = product.declared_discount_percent
    row.commission_rate = product.commission_rate
    row.volume = product.volume
    row.history_median = score.history_median
    row.history_snapshot_count = score.history_snapshot_count
    row.price_drop_percent = score.price_drop_percent
    row.history_score = score.history_score
    row.discount_score = score.discount_score
    row.volume_score = score.volume_score
    row.commission_score = score.commission_score
    row.completeness_score = score.completeness_score
    row.total_score = score.total_score
    row.classification = score.classification
