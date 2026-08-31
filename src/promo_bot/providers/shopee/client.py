"""Explicit gate in place of an assumed Shopee network client."""

from __future__ import annotations

from collections.abc import Sequence

from promo_bot.providers.base import ProviderError
from promo_bot.providers.shopee.models import EnrichedAffiliateOffer, ProviderProductReference


class UnavailableShopeeAffiliateClient:
    """Fail closed until the authenticated official contract is confirmed."""

    async def enrich(
        self,
        reference: ProviderProductReference,
        *,
        sub_ids: Sequence[str] = (),
    ) -> EnrichedAffiliateOffer:
        del reference, sub_ids
        raise ProviderError("SHOPEE_OFFICIAL_CONTRACT_UNAVAILABLE", retryable=False)
