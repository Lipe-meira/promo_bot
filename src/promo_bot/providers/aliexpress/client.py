"""AliExpress client boundary with the productive signing transport gated."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from promo_bot.providers.aliexpress.models import EnrichedAffiliateOffer
from promo_bot.providers.aliexpress.top import (
    AUTHORIZED_OPERATIONS,
    AliExpressTopRequestBuilder,
    PreparedAliExpressTopRequest,
)
from promo_bot.providers.base import ProviderError

SIGNING_CONTRACT_UNAVAILABLE = "ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE"
LIVE_API_DISABLED = "ALIEXPRESS_LIVE_API_DISABLED"


class AliExpressOperationTransport(Protocol):
    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]: ...


class AliExpressPreparedRequestTransport(Protocol):
    async def execute(self, request: PreparedAliExpressTopRequest) -> Mapping[str, Any]: ...


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


class AliExpressAffiliateApiClient:
    """Prepare only allowlisted operations after an explicit live opt-in."""

    __slots__ = ("_live_enabled", "_request_builder", "_transport")

    def __init__(
        self,
        transport: AliExpressPreparedRequestTransport,
        *,
        request_builder: AliExpressTopRequestBuilder,
        live_enabled: bool = False,
    ) -> None:
        self._transport = transport
        self._request_builder = request_builder
        self._live_enabled = live_enabled is True

    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]:
        if operation not in AUTHORIZED_OPERATIONS:
            raise ProviderError("ALIEXPRESS_OPERATION_UNSUPPORTED", retryable=False)
        if not self._live_enabled:
            raise ProviderError(LIVE_API_DISABLED, retryable=False)
        prepared = self._request_builder.prepare(operation, payload)
        return await self._transport.execute(prepared)

    def __repr__(self) -> str:
        return f"AliExpressAffiliateApiClient(live_enabled={self._live_enabled})"

    __str__ = __repr__
