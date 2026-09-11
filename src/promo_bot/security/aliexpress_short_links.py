"""Strict, provider-specific resolution for AliExpress short product links."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpcore
import httpx

from promo_bot.domain.enums import Store
from promo_bot.stores.urls import STORE_HOSTS, normalize_hostname

ALIEXPRESS_SHORT_HOST = "s.click.aliexpress.com"
ALIEXPRESS_REDIRECT_HOSTS = STORE_HOSTS[Store.ALIEXPRESS] | {ALIEXPRESS_SHORT_HOST}
ALIEXPRESS_PRODUCT_PATH = re.compile(r"/item/([0-9]+)\.html", re.IGNORECASE)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_URL_LENGTH = 4_096


class AliExpressShortLinkRejected(RuntimeError):
    """Expose only a stable reason code for an unsafe or unusable link."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __repr__(self) -> str:
        return f"AliExpressShortLinkRejected(code={self.code!r})"


@dataclass(frozen=True, slots=True)
class AliExpressRedirectHop:
    status_code: int
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ResolvedAliExpressProduct:
    product_id: str
    variation_key: str
    identity_url: str
    generation_url: str
    redirect_count: int

    def __repr__(self) -> str:
        return (
            "ResolvedAliExpressProduct("
            f"product_id={self.product_id!r}, "
            f"variation_key={self.variation_key!r}, "
            f"redirect_count={self.redirect_count})"
        )


@dataclass(frozen=True, slots=True)
class _ValidatedHopTarget:
    url: str
    hostname: str
    allowed_ips: frozenset[str]


class AliExpressDnsResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> frozenset[str]: ...


class AliExpressHopRequester(Protocol):
    async def fetch(
        self,
        url: str,
        *,
        method: str,
        allowed_ips: frozenset[str],
    ) -> AliExpressRedirectHop: ...


class SystemAliExpressDnsResolver:
    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise AliExpressShortLinkRejected("ALIEXPRESS_DNS_RESOLUTION_FAILED") from exc
        return frozenset(item[4][0].split("%", 1)[0] for item in addresses)


class PinnedAliExpressNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect an approved hostname through one pre-resolved global IP."""

    def __init__(
        self,
        *,
        hostname: str,
        allowed_ips: frozenset[str],
        delegate: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        if not allowed_ips:
            raise ValueError("allowed_ips cannot be empty")
        self.hostname = normalize_hostname(hostname)
        self.allowed_ips = allowed_ips
        self.delegate = delegate or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if normalize_hostname(host) != self.hostname or port != 443:
            raise httpcore.ConnectError("target authority mismatch")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout is not None else None
        last_error: BaseException | None = None
        for address in sorted(self.allowed_ips, key=_ip_sort_key):
            remaining = None if deadline is None else max(0.0, deadline - loop.time())
            if remaining == 0:
                break
            try:
                stream = await self.delegate.connect_tcp(
                    address,
                    port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue
            peer = _stream_peer_ip(stream)
            if peer != address:
                await stream.aclose()
                raise httpcore.ConnectError("peer address mismatch")
            return stream
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectTimeout("prevalidated addresses exhausted")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise httpcore.ConnectError("unix sockets are forbidden")

    async def sleep(self, seconds: float) -> None:
        await self.delegate.sleep(seconds)


class _HttpCoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        async for part in self.stream:
            yield part

    async def aclose(self) -> None:
        await self.stream.aclose()


class PinnedAliExpressHttpTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        hostname: str,
        allowed_ips: frozenset[str],
        *,
        network_delegate: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=PinnedAliExpressNetworkBackend(
                hostname=hostname,
                allowed_ips=allowed_ips,
                delegate=network_delegate,
            ),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.AsyncByteStream):
            raise TypeError("an async request stream is required")
        response = await self.pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_HttpCoreResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.pool.aclose()


class AliExpressPinnedHttpRequester:
    """Fetch only headers through a fresh IP-pinned transport for each hop."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        transport_factory: Callable[[str, frozenset[str]], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.transport_factory = transport_factory or PinnedAliExpressHttpTransport

    async def fetch(
        self,
        url: str,
        *,
        method: str,
        allowed_ips: frozenset[str],
    ) -> AliExpressRedirectHop:
        hostname = normalize_hostname(urlsplit(url).hostname or "")
        transport = self.transport_factory(hostname, allowed_ips)
        try:
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=self.timeout,
                headers={"User-Agent": "promo-bot/0.1 aliexpress-short-link-resolver"},
            ) as client:
                async with client.stream(method, url) as response:
                    return AliExpressRedirectHop(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                    )
        except (httpx.TimeoutException, httpcore.TimeoutException) as exc:
            raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_TIMEOUT") from exc
        except (httpx.TransportError, httpcore.NetworkError) as exc:
            if _has_cause(exc, ssl.SSLCertVerificationError):
                raise AliExpressShortLinkRejected("ALIEXPRESS_TLS_CERTIFICATE_INVALID") from exc
            raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_TRANSPORT_ERROR") from exc


class AliExpressShortLinkResolver:
    def __init__(
        self,
        *,
        resolver: AliExpressDnsResolver | None = None,
        requester: AliExpressHopRequester | None = None,
        timeout_seconds: float = 10.0,
        total_timeout_seconds: float = 30.0,
        max_redirects: int = 5,
    ) -> None:
        if max_redirects < 0 or total_timeout_seconds <= 0:
            raise ValueError("max_redirects cannot be negative")
        self.resolver = resolver or SystemAliExpressDnsResolver()
        self.requester = requester or AliExpressPinnedHttpRequester(timeout_seconds)
        self.max_redirects = max_redirects
        self.total_timeout_seconds = total_timeout_seconds

    def resolve_canonical(self, url: str) -> ResolvedAliExpressProduct:
        return _product_from_url(url, redirect_count=0)

    async def resolve(self, url: str) -> ResolvedAliExpressProduct:
        try:
            async with asyncio.timeout(self.total_timeout_seconds):
                return await self._resolve(url)
        except TimeoutError:
            raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_TIMEOUT") from None

    async def _resolve(self, url: str) -> ResolvedAliExpressProduct:
        _validate_short_input(url)
        current = url
        redirects = 0
        visited: set[str] = set()
        while True:
            key = _loop_key(current)
            if key in visited:
                raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_LOOP")
            visited.add(key)
            target = await self._validated_target(current)
            response = await self.requester.fetch(
                target.url,
                method="GET",
                allowed_ips=target.allowed_ips,
            )
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_LOCATION_MISSING")
                if redirects >= self.max_redirects:
                    raise AliExpressShortLinkRejected("ALIEXPRESS_TOO_MANY_REDIRECTS")
                next_url = urljoin(current, location)
                _validate_hop_syntax(next_url)
                current = next_url
                redirects += 1
                continue
            if not 200 <= response.status_code < 300:
                raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_HTTP_STATUS")
            return _product_from_url(current, redirect_count=redirects)

    async def _validated_target(self, url: str) -> _ValidatedHopTarget:
        hostname = _validate_hop_syntax(url)
        addresses = await self.resolver.resolve(hostname, 443)
        if not addresses:
            raise AliExpressShortLinkRejected("ALIEXPRESS_DNS_EMPTY_RESULT")
        normalized: set[str] = set()
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError as exc:
                raise AliExpressShortLinkRejected("ALIEXPRESS_DNS_INVALID_ADDRESS") from exc
            if not parsed.is_global:
                raise AliExpressShortLinkRejected("ALIEXPRESS_DNS_NON_GLOBAL_ADDRESS")
            normalized.add(str(parsed))
        return _ValidatedHopTarget(url=url, hostname=hostname, allowed_ips=frozenset(normalized))


def _validate_short_input(url: str) -> None:
    hostname = _validate_hop_syntax(url)
    parts = urlsplit(url)
    if hostname != ALIEXPRESS_SHORT_HOST or not parts.path.startswith("/e/"):
        raise AliExpressShortLinkRejected("ALIEXPRESS_SHORT_URL_REQUIRED")


def _validate_hop_syntax(url: str) -> str:
    if len(url) > MAX_URL_LENGTH or any(ord(character) < 32 for character in url):
        raise AliExpressShortLinkRejected("ALIEXPRESS_URL_INVALID")
    if "\\" in url:
        raise AliExpressShortLinkRejected("ALIEXPRESS_URL_INVALID")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise AliExpressShortLinkRejected("ALIEXPRESS_URL_INVALID") from exc
    if parts.scheme.casefold() != "https":
        raise AliExpressShortLinkRejected("ALIEXPRESS_HTTPS_REQUIRED")
    if parts.username is not None or parts.password is not None:
        raise AliExpressShortLinkRejected("ALIEXPRESS_URL_USERINFO_FORBIDDEN")
    if port not in {None, 443}:
        raise AliExpressShortLinkRejected("ALIEXPRESS_URL_PORT_FORBIDDEN")
    hostname = normalize_hostname(parts.hostname) if parts.hostname else ""
    if hostname not in ALIEXPRESS_REDIRECT_HOSTS:
        raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_HOST_FORBIDDEN")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_HOST_FORBIDDEN")
    return hostname


def _product_from_url(url: str, *, redirect_count: int) -> ResolvedAliExpressProduct:
    hostname = _validate_hop_syntax(url)
    if hostname == ALIEXPRESS_SHORT_HOST:
        raise AliExpressShortLinkRejected("ALIEXPRESS_PRODUCT_URL_REQUIRED")
    parts = urlsplit(url)
    match = ALIEXPRESS_PRODUCT_PATH.fullmatch(parts.path)
    if match is None:
        raise AliExpressShortLinkRejected("ALIEXPRESS_PRODUCT_ID_NOT_FOUND")
    product_id = match.group(1)
    sku_values = [
        value
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() in {"sku_id", "skuid"}
    ]
    if sku_values and (len(sku_values) != 1 or not sku_values[0].isdigit()):
        raise AliExpressShortLinkRejected("ALIEXPRESS_VARIATION_AMBIGUOUS")
    sku_id = sku_values[0] if sku_values else None
    query = urlencode({"sku_id": sku_id}) if sku_id else ""
    identity_url = urlunsplit(
        ("https", "www.aliexpress.com", f"/item/{product_id}.html", query, "")
    )
    generation_url = urlunsplit(
        ("https", "pt.aliexpress.com", f"/item/{product_id}.html", query, "")
    )
    return ResolvedAliExpressProduct(
        product_id=product_id,
        variation_key=f"sku_id:{sku_id}" if sku_id else "",
        identity_url=identity_url,
        generation_url=generation_url,
        redirect_count=redirect_count,
    )


def _loop_key(url: str) -> str:
    parts = urlsplit(url)
    hostname = normalize_hostname(parts.hostname) if parts.hostname else ""
    netloc = hostname if parts.port in {None, 443} else f"{hostname}:{parts.port}"
    return urlunsplit((parts.scheme.casefold(), netloc, parts.path, parts.query, ""))


def _stream_peer_ip(stream: httpcore.AsyncNetworkStream) -> str | None:
    value = stream.get_extra_info("server_addr")
    if isinstance(value, tuple) and value:
        return str(value[0]).split("%", 1)[0]
    if isinstance(value, str):
        return value.split("%", 1)[0]
    return None


def _ip_sort_key(value: str) -> tuple[int, int]:
    parsed = ipaddress.ip_address(value)
    return parsed.version, int(parsed)


def _has_cause(error: BaseException, expected: type[BaseException]) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, expected):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
