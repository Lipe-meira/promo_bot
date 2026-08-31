"""Shopee provider wrapper that exposes only the internal contract."""

from __future__ import annotations

from collections.abc import Sequence

from promo_bot.providers.base import StoreProvider
from promo_bot.providers.shopee.models import EnrichedAffiliateOffer, ProviderProductReference


class ShopeeProvider:
    def __init__(
        self,
        client: StoreProvider[ProviderProductReference, EnrichedAffiliateOffer],
    ) -> None:
        self.client = client

    async def enrich(
        self,
        reference: ProviderProductReference,
        *,
        sub_ids: Sequence[str] = (),
    ) -> EnrichedAffiliateOffer:
        return await self.client.enrich(reference, sub_ids=sub_ids)
