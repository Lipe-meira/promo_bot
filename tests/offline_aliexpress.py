"""Signed wire simulator, physically limited to HTTPX MockTransport."""

from collections.abc import Mapping
from typing import Any

import httpx

from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport


class OfflineAliExpressHttpTransport(AliExpressHttpTransport):
    """Test-only storage seam; construction is physically limited to MockTransport."""

    def __init__(self, transport: AliExpressHttpTransport):
        assert isinstance(transport.client._transport, httpx.MockTransport)
        super().__init__(
            transport.client,
            max_attempts=transport.max_attempts,
            durable_retry=transport.durable_retry,
            retry_after_max_seconds=transport.retry_after_max_seconds,
            backoff_seconds=transport.backoff_seconds,
            sleep=transport.sleep,
            before_send=transport.before_send,
        )

    async def _validate_generation_storage(self, call: Any) -> None:
        from promo_bot.affiliate.history_context import validate_history_storage

        assert isinstance(self.client._transport, httpx.MockTransport)
        await validate_history_storage(call.database, real=False)


class OfflineSignedAliExpressClient:
    def __init__(
        self,
        transport: AliExpressHttpTransport,
        *,
        request_builder: AliExpressTopRequestBuilder,
        live_enabled: bool = True,
    ):
        assert isinstance(transport.client._transport, httpx.MockTransport)
        self.transport = OfflineAliExpressHttpTransport(transport)
        self.builder, self.enabled = request_builder, live_enabled

    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]:
        assert self.enabled
        return await self.transport.execute(self.builder.prepare(operation, payload))
