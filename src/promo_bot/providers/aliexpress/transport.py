"""HTTP mechanics for immutable, pre-signed AliExpress TOP requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

import httpx

from promo_bot.providers.aliexpress.top import PreparedAliExpressTopRequest
from promo_bot.providers.base import ProviderError

TRANSIENT_STATUS = frozenset({429, 502, 503, 504})
ALIEXPRESS_TOP_ORIGIN: Final[str] = "https://api-sg.aliexpress.com"


class AliExpressHttpTransport:
    """Send a prepared TOP request without changing its query or form pairs."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_attempts: int = 3,
        retry_after_max_seconds: float = 300,
        backoff_seconds: float = 1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_after_max_seconds <= 0 or backoff_seconds < 0:
            raise ValueError("retry timing must be non-negative with a positive cap")
        self.client = client
        self.max_attempts = max_attempts
        self.retry_after_max_seconds = retry_after_max_seconds
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    async def execute(self, request: PreparedAliExpressTopRequest) -> Mapping[str, Any]:
        if request.method != "POST" or request.path != "/sync":
            raise ValueError("AliExpress TOP request has an unsupported method or path")
        url = ALIEXPRESS_TOP_ORIGIN + request.relative_url()
        content = request.encoded_form().encode("utf-8")
        headers = {"Content-Type": request.content_type}
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.client.request(
                    request.method,
                    url,
                    content=content,
                    headers=headers,
                    follow_redirects=False,
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
                if attempt == self.max_attempts:
                    raise ProviderError("ALIEXPRESS_RETRY_EXHAUSTED", retryable=False) from None
                await self.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
                continue

            if response.status_code in TRANSIENT_STATUS:
                if attempt == self.max_attempts:
                    raise ProviderError("ALIEXPRESS_RETRY_EXHAUSTED", retryable=False)
                retry_after = _retry_after_seconds(response)
                delay = (
                    min(retry_after, self.retry_after_max_seconds)
                    if retry_after is not None
                    else self.backoff_seconds * (2 ** (attempt - 1))
                )
                await self.sleep(delay)
                continue
            if response.is_error:
                raise ProviderError("ALIEXPRESS_HTTP_PERMANENT", retryable=False)
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError(
                    "ALIEXPRESS_RESPONSE_INCOMPATIBLE",
                    retryable=False,
                    manual_review=True,
                ) from exc
            if not isinstance(body, Mapping):
                raise ProviderError(
                    "ALIEXPRESS_RESPONSE_INCOMPATIBLE",
                    retryable=False,
                    manual_review=True,
                )
            return body
        raise AssertionError("retry loop must return or raise")

    def __repr__(self) -> str:
        return (
            "AliExpressHttpTransport(endpoint=https://api-sg.aliexpress.com/sync, "
            f"max_attempts={self.max_attempts})"
        )

    __str__ = __repr__


def build_offline_safe_http_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(20.0, connect=5.0, read=10.0, write=10.0, pool=5.0)
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=2)
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "promo-affiliate-bot/0.1 (offline-gated AliExpress client)"},
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, parsed)
