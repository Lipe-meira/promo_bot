"""HTTP mechanics independent from the still-gated official signing contract."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from promo_bot.providers.base import ProviderError

TRANSIENT_STATUS = frozenset({429, 502, 503, 504})


class AliExpressRequestSigner(Protocol):
    def sign(self, operation: str, business_parameters: Mapping[str, str]) -> Mapping[str, str]: ...


class AliExpressHttpTransport:
    """Execute already specified operations without owning gateway or signature semantics."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        endpoint: str,
        allowed_endpoint_host: str,
        signer: AliExpressRequestSigner,
        max_attempts: int = 3,
        retry_after_max_seconds: float = 300,
        backoff_seconds: float = 1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        _validate_endpoint(endpoint, allowed_endpoint_host)
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_after_max_seconds <= 0 or backoff_seconds < 0:
            raise ValueError("retry timing must be non-negative with a positive cap")
        self.client = client
        self.endpoint = endpoint
        self.signer = signer
        self.max_attempts = max_attempts
        self.retry_after_max_seconds = retry_after_max_seconds
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    async def execute(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, Any]:
        for attempt in range(1, self.max_attempts + 1):
            signed = self.signer.sign(operation, payload)
            try:
                response = await self.client.post(self.endpoint, data=signed)
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


def _validate_endpoint(endpoint: str, allowed_host: str) -> None:
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError as exc:
        raise ValueError("AliExpress endpoint is malformed") from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.hostname.casefold() != allowed_host.strip().casefold()
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or parts.fragment
    ):
        raise ValueError("AliExpress endpoint must be an explicitly allowed HTTPS URL")


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, parsed)
