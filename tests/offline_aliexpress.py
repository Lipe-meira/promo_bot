"""Signed wire simulator, physically limited to HTTPX MockTransport."""

from collections.abc import Mapping
from typing import Any

import httpx

from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport


class OfflineSignedAliExpressClient:
    def __init__(
        self,
        transport: AliExpressHttpTransport,
        *,
        request_builder: AliExpressTopRequestBuilder,
        live_enabled: bool = True,
    ):
        assert isinstance(transport.client._transport, httpx.MockTransport)
        self.transport, self.builder, self.enabled = transport, request_builder, live_enabled

    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]:
        assert self.enabled
        return await self.transport.execute(self.builder.prepare(operation, payload))
