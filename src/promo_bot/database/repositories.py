"""Small, explicit repository boundaries for foundation entities."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import ProcessedItemModel, ProductModel


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_external_id(self, store: str, external_id: str) -> ProductModel | None:
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store == store,
                ProductModel.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, product: ProductModel) -> ProductModel:
        self.session.add(product)
        await self.session.flush()
        return product


class ProcessedItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(
        self, store: str, external_product_id: str, variation_key: str = ""
    ) -> ProcessedItemModel | None:
        result = await self.session.execute(
            select(ProcessedItemModel).where(
                ProcessedItemModel.store == store,
                ProcessedItemModel.external_product_id == external_product_id,
                ProcessedItemModel.variation_key == variation_key,
            )
        )
        return result.scalar_one_or_none()

    async def record(
        self,
        *,
        store: str,
        external_product_id: str,
        deal_hash: str,
        variation_key: str = "",
        last_sent_at: datetime | None = None,
        last_price: Decimal | None = None,
        last_coupon: str | None = None,
        cooldown_until: datetime | None = None,
    ) -> ProcessedItemModel:
        item = await self.find(store, external_product_id, variation_key)
        if item is None:
            item = ProcessedItemModel(
                store=store,
                external_product_id=external_product_id,
                variation_key=variation_key,
                deal_hash=deal_hash,
            )
            self.session.add(item)
        item.deal_hash = deal_hash
        item.last_sent_at = last_sent_at
        item.last_price = last_price
        item.last_coupon = last_coupon
        item.cooldown_until = cooldown_until
        await self.session.flush()
        return item
