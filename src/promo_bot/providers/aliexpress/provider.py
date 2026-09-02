"""Provider wrapper exposing only the internal AliExpress contract."""

from __future__ import annotations

from collections.abc import Sequence

from promo_bot.providers.aliexpress.models import (
    AliExpressProductReference,
    EnrichedAffiliateOffer,
)
from promo_bot.providers.base import StoreProvider


class AliExpressProvider:
    def __init__(
        self,
        client: StoreProvider[AliExpressProductReference, EnrichedAffiliateOffer],
    ) -> None:
        self.client = client

    async def enrich(
        self,
        reference: AliExpressProductReference,
        *,
        sub_ids: Sequence[str] = (),
    ) -> EnrichedAffiliateOffer:
        return await self.client.enrich(reference, sub_ids=sub_ids)
