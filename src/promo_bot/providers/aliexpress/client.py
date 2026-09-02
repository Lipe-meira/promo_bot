"""AliExpress client boundary with the productive signing transport gated."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from promo_bot.providers.aliexpress.models import EnrichedAffiliateOffer
from promo_bot.providers.base import ProviderError

SIGNING_CONTRACT_UNAVAILABLE = "ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE"


class AliExpressOperationTransport(Protocol):
    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]: ...


class UnavailableAliExpressOperationTransport:
    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]:
        del operation, payload
        raise ProviderError(SIGNING_CONTRACT_UNAVAILABLE, retryable=False)


class UnavailableAliExpressAffiliateClient:
    """Prevent real provider use until gateway and dotted-method signing are frozen."""

    async def enrich(
        self,
        reference: object,
        *,
        sub_ids: Sequence[str] = (),
    ) -> EnrichedAffiliateOffer:
        del reference, sub_ids
        raise ProviderError(SIGNING_CONTRACT_UNAVAILABLE, retryable=False)
