"""Fail-closed redirect expansion with DNS and peer-address checks."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from promo_bot.stores.urls import ALLOWED_NETWORK_HOSTS, normalize_hostname

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class SafeUrlError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransientUrlError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    url: str
    hostname: str
    port: int
    allowed_ips: frozenset[str]


@dataclass(frozen=True, slots=True)
class HopResponse:
    status_code: int
    headers: Mapping[str, str]
    peer_ip: str | None


@dataclass(frozen=True, slots=True)
class UrlExpansionResult:
    url: str
    redirect_count: int


class DnsResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> frozenset[str]: ...


class HopRequester(Protocol):
    async def fetch(self, url: str, *, method: str, allowed_ips: frozenset[str]) -> HopResponse: ...


class SystemDnsResolver:
    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        loop = asyncio.get_running_loop()
        try:
            addresses = await loop.getaddrinfo(
                hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        except OSError as exc:
            raise TransientUrlError("DNS_RESOLUTION_FAILED") from exc
        return frozenset(item[4][0].split("%", 1)[0] for item in addresses)


class HttpxHopRequester:
    """Make one non-following request and expose the actual TCP peer."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout = httpx.Timeout(timeout_seconds)

    async def fetch(self, url: str, *, method: str, allowed_ips: frozenset[str]) -> HopResponse:
        del allowed_ips  # The caller verifies the peer against this set after the request.
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=self.timeout,
                headers={"User-Agent": "promo-bot/0.1 safe-link-expander"},
            ) as client:
                async with client.stream(method, url) as response:
                    peer_ip = _response_peer_ip(response)
                    return HopResponse(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        peer_ip=peer_ip,
                    )
        except httpx.TimeoutException as exc:
            raise TransientUrlError("HTTP_TIMEOUT") from exc
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise SafeUrlError("HTTP_INVALID_TARGET") from exc
        except httpx.TransportError as exc:
            if _has_cause(exc, ssl.SSLCertVerificationError):
                raise SafeUrlError("TLS_CERTIFICATE_INVALID") from exc
            raise TransientUrlError("HTTP_TRANSPORT_ERROR") from exc


class SafeUrlExpander:
    def __init__(
        self,
        *,
        resolver: DnsResolver | None = None,
        requester: HopRequester | None = None,
        timeout_seconds: float = 10.0,
        max_redirects: int = 5,
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        self.resolver = resolver or SystemDnsResolver()
        self.requester = requester or HttpxHopRequester(timeout_seconds)
        self.max_redirects = max_redirects

    async def expand(self, url: str) -> UrlExpansionResult:
        current = url
        redirects = 0
        while True:
            target = await self._validate_target(current)
            response = await self.requester.fetch(
                target.url, method="GET", allowed_ips=target.allowed_ips
            )
            self._validate_peer(response.peer_ip, target.allowed_ips)
            if response.status_code in TRANSIENT_STATUSES:
                raise TransientUrlError(f"HTTP_{response.status_code}")
            if response.status_code == 403:
                raise SafeUrlError("HTTP_FORBIDDEN_OR_CHALLENGE")
            if 400 <= response.status_code:
                raise SafeUrlError(f"HTTP_{response.status_code}")
            if response.status_code not in REDIRECT_STATUSES:
                return UrlExpansionResult(url=current, redirect_count=redirects)
            location = response.headers.get("location")
            if not location:
                raise SafeUrlError("REDIRECT_WITHOUT_LOCATION")
            if redirects >= self.max_redirects:
                raise SafeUrlError("TOO_MANY_REDIRECTS")
            current = urljoin(current, location)
            redirects += 1

    async def _validate_target(self, url: str) -> ValidatedTarget:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise SafeUrlError("INVALID_URL") from exc
        if parts.scheme.casefold() not in {"http", "https"}:
            raise SafeUrlError("UNSUPPORTED_SCHEME")
        if not parts.hostname:
            raise SafeUrlError("MISSING_HOST")
        if parts.username is not None or parts.password is not None:
            raise SafeUrlError("URL_CREDENTIALS_FORBIDDEN")
        hostname = normalize_hostname(parts.hostname)
        if hostname not in ALLOWED_NETWORK_HOSTS:
            raise SafeUrlError("DOMAIN_NOT_ALLOWED")
        effective_port = port or (443 if parts.scheme.casefold() == "https" else 80)
        if effective_port not in {80, 443}:
            raise SafeUrlError("PORT_NOT_ALLOWED")
        addresses = await self.resolver.resolve(hostname, effective_port)
        if not addresses:
            raise TransientUrlError("DNS_EMPTY_RESULT")
        normalized_addresses: set[str] = set()
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise SafeUrlError("DNS_INVALID_ADDRESS") from exc
            if not parsed.is_global:
                raise SafeUrlError("DNS_NON_GLOBAL_ADDRESS")
            normalized_addresses.add(str(parsed))
        return ValidatedTarget(url, hostname, effective_port, frozenset(normalized_addresses))

    @staticmethod
    def _validate_peer(peer_ip: str | None, allowed_ips: frozenset[str]) -> None:
        if peer_ip is None:
            raise SafeUrlError("PEER_IP_UNVERIFIED")
        try:
            peer = ipaddress.ip_address(peer_ip.split("%", 1)[0])
        except ValueError as exc:
            raise SafeUrlError("PEER_IP_INVALID") from exc
        if not peer.is_global:
            raise SafeUrlError("PEER_IP_NON_GLOBAL")
        if str(peer) not in allowed_ips:
            raise SafeUrlError("PEER_IP_DNS_MISMATCH")


def _response_peer_ip(response: httpx.Response) -> str | None:
    stream: Any = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        return None
    for key in ("server_addr", "peername"):
        value = stream.get_extra_info(key)
        if isinstance(value, tuple) and value:
            return str(value[0])
        if isinstance(value, str):
            return value
    return None


def _has_cause(error: BaseException, expected: type[BaseException]) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, expected):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
