from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx
import pytest

from promo_bot.security.aliexpress_short_links import (
    AliExpressPinnedHttpRequester,
    AliExpressRedirectHop,
    AliExpressShortLinkRejected,
    AliExpressShortLinkResolver,
    PinnedAliExpressHttpTransport,
    PinnedAliExpressNetworkBackend,
    ResolvedAliExpressProduct,
)


class FixtureDnsResolver:
    def __init__(self, addresses: Mapping[str, frozenset[str]] | None = None) -> None:
        self.addresses = addresses or {
            "s.click.aliexpress.com": frozenset({"8.8.8.8"}),
            "www.aliexpress.com": frozenset({"1.1.1.1"}),
            "pt.aliexpress.com": frozenset({"1.0.0.1"}),
            "aliexpress.com": frozenset({"9.9.9.9"}),
            "de.aliexpress.com": frozenset({"8.8.4.4"}),
        }
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        self.calls.append((hostname, port))
        return self.addresses.get(hostname, frozenset())


@dataclass
class FixtureRequester:
    responses: dict[str, AliExpressRedirectHop]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str, frozenset[str]]] = []

    async def fetch(
        self,
        url: str,
        *,
        method: str,
        allowed_ips: frozenset[str],
    ) -> AliExpressRedirectHop:
        self.calls.append((url, method, allowed_ips))
        return self.responses[url]


def test_canonical_product_is_cleaned_locally_without_network() -> None:
    dns = FixtureDnsResolver()
    requester = FixtureRequester({})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    result = resolver.resolve_canonical(
        "https://www.aliexpress.com/item/1005001234567890.html"
        "?aff_fcid=foreign&tracking_id=foreign&sku_id=200000001#ignored"
    )

    assert result == ResolvedAliExpressProduct(
        product_id="1005001234567890",
        variation_key="sku_id:200000001",
        identity_url=("https://www.aliexpress.com/item/1005001234567890.html?sku_id=200000001"),
        generation_url=("https://pt.aliexpress.com/item/1005001234567890.html?sku_id=200000001"),
        redirect_count=0,
    )
    assert dns.calls == []
    assert requester.calls == []


@pytest.mark.asyncio
async def test_short_link_follows_validated_redirect_and_rebuilds_clean_url() -> None:
    short = "https://s.click.aliexpress.com/e/_SecretCode"
    final = "https://pt.aliexpress.com/item/1005001234567890.html?spm=foreign&aff_trace_key=foreign"
    dns = FixtureDnsResolver()
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": final}),
            final: AliExpressRedirectHop(200, {}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    result = await resolver.resolve(short)

    assert result.product_id == "1005001234567890"
    assert result.variation_key == ""
    assert result.identity_url == "https://www.aliexpress.com/item/1005001234567890.html"
    assert result.generation_url == "https://pt.aliexpress.com/item/1005001234567890.html"
    assert result.redirect_count == 1
    assert [call[1] for call in requester.calls] == ["GET", "GET"]
    assert requester.calls[0][2] == frozenset({"8.8.8.8"})
    assert requester.calls[1][2] == frozenset({"1.0.0.1"})


@pytest.mark.asyncio
async def test_short_link_detects_redirect_loop_without_third_request() -> None:
    first = "https://s.click.aliexpress.com/e/_One"
    second = "https://www.aliexpress.com/e/_Two"
    requester = FixtureRequester(
        {
            first: AliExpressRedirectHop(302, {"location": second}),
            second: AliExpressRedirectHop(302, {"location": first}),
        }
    )
    resolver = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(), requester=requester, max_redirects=5
    )

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_REDIRECT_LOOP"):
        await resolver.resolve(first)

    assert len(requester.calls) == 2


@pytest.mark.asyncio
async def test_short_link_rejects_https_downgrade_before_requesting_target() -> None:
    short = "https://s.click.aliexpress.com/e/_One"
    requester = FixtureRequester(
        {short: AliExpressRedirectHop(302, {"location": "http://www.aliexpress.com/item/1.html"})}
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_HTTPS_REQUIRED"):
        await resolver.resolve(short)

    assert len(requester.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("https://aliexpress.com.evil.example/item/1.html", "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN"),
        ("https://user@www.aliexpress.com/item/1.html", "ALIEXPRESS_URL_USERINFO_FORBIDDEN"),
        ("https://www.aliexpress.com:444/item/1.html", "ALIEXPRESS_URL_PORT_FORBIDDEN"),
        ("https://127.0.0.1/item/1.html", "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN"),
    ],
)
async def test_short_link_rejects_unsafe_authority_before_second_request(
    location: str, code: str
) -> None:
    short = "https://s.click.aliexpress.com/e/_One"
    requester = FixtureRequester({short: AliExpressRedirectHop(302, {"location": location})})
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match=code):
        await resolver.resolve(short)

    assert len(requester.calls) == 1


@pytest.mark.asyncio
async def test_short_link_rejects_any_non_global_dns_answer_before_request() -> None:
    short = "https://s.click.aliexpress.com/e/_One"
    dns = FixtureDnsResolver({"s.click.aliexpress.com": frozenset({"8.8.8.8", "192.168.1.20"})})
    requester = FixtureRequester({})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_DNS_NON_GLOBAL_ADDRESS"):
        await resolver.resolve(short)

    assert requester.calls == []


@pytest.mark.asyncio
async def test_short_link_enforces_redirect_limit() -> None:
    first = "https://s.click.aliexpress.com/e/_One"
    second = "https://www.aliexpress.com/e/_Two"
    requester = FixtureRequester(
        {
            first: AliExpressRedirectHop(302, {"location": second}),
            second: AliExpressRedirectHop(
                302, {"location": "https://pt.aliexpress.com/item/1005001234567890.html"}
            ),
        }
    )
    resolver = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(), requester=requester, max_redirects=1
    )

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_TOO_MANY_REDIRECTS"):
        await resolver.resolve(first)

    assert len(requester.calls) == 2


def test_resolved_product_and_errors_do_not_reveal_urls_or_codes() -> None:
    secret_url = "https://s.click.aliexpress.com/e/_DoNotReveal"
    result = ResolvedAliExpressProduct(
        product_id="1005001234567890",
        variation_key="",
        identity_url="https://www.aliexpress.com/item/1005001234567890.html",
        generation_url="https://pt.aliexpress.com/item/1005001234567890.html",
        redirect_count=1,
    )
    error = AliExpressShortLinkRejected("ALIEXPRESS_REDIRECT_LOOP")

    assert secret_url not in repr(result)
    assert "pt.aliexpress.com" not in repr(result)
    assert repr(error) == "AliExpressShortLinkRejected(code='ALIEXPRESS_REDIRECT_LOOP')"


class FixtureNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del buffer, timeout

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del ssl_context, server_hostname, timeout
        return self

    def get_extra_info(self, info: str) -> Any:
        return (self.peer, 443) if info == "server_addr" else None


class FixtureNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, streams: Mapping[str, FixtureNetworkStream]) -> None:
        self.streams = streams
        self.calls: list[tuple[str, int, float | None]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        del local_address, socket_options
        self.calls.append((host, port, timeout))
        return self.streams[host]

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("unix sockets must not be used")

    async def sleep(self, seconds: float) -> None:
        del seconds


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_prevalidated_ip_not_hostname() -> None:
    stream = FixtureNetworkStream("8.8.8.8")
    delegate = FixtureNetworkBackend({"8.8.8.8": stream})
    backend = PinnedAliExpressNetworkBackend(
        hostname="s.click.aliexpress.com",
        allowed_ips=frozenset({"8.8.8.8"}),
        delegate=delegate,
    )

    connected = await backend.connect_tcp("s.click.aliexpress.com", 443, timeout=2.0)

    assert connected is stream
    assert delegate.calls == [("8.8.8.8", 443, 2.0)]


@pytest.mark.asyncio
async def test_pinned_backend_closes_and_rejects_unexpected_peer() -> None:
    stream = FixtureNetworkStream("127.0.0.1")
    delegate = FixtureNetworkBackend({"8.8.8.8": stream})
    backend = PinnedAliExpressNetworkBackend(
        hostname="s.click.aliexpress.com",
        allowed_ips=frozenset({"8.8.8.8"}),
        delegate=delegate,
    )

    with pytest.raises(httpcore.ConnectError, match="peer address mismatch"):
        await backend.connect_tcp("s.click.aliexpress.com", 443, timeout=2.0)

    assert stream.closed is True


class ExplodingResponseBody(httpx.AsyncByteStream):
    async def __aiter__(self):  # type: ignore[no-untyped-def]
        raise AssertionError("redirect response body must not be read")
        yield b""  # pragma: no cover


@pytest.mark.asyncio
async def test_pinned_requester_uses_streaming_get_without_sensitive_headers() -> None:
    captured_targets: list[tuple[str, frozenset[str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(
            302,
            headers={"location": "https://pt.aliexpress.com/item/1005001234567890.html"},
            stream=ExplodingResponseBody(),
        )

    def transport_factory(hostname: str, allowed_ips: frozenset[str]) -> httpx.AsyncBaseTransport:
        captured_targets.append((hostname, allowed_ips))
        return httpx.MockTransport(handler)

    requester = AliExpressPinnedHttpRequester(
        timeout_seconds=2.0,
        transport_factory=transport_factory,
    )

    response = await requester.fetch(
        "https://s.click.aliexpress.com/e/_PrivateCode",
        method="GET",
        allowed_ips=frozenset({"8.8.8.8"}),
    )

    assert response.status_code == 302
    assert response.headers["location"].endswith("1005001234567890.html")
    assert captured_targets == [("s.click.aliexpress.com", frozenset({"8.8.8.8"}))]


@pytest.mark.parametrize(
    "host",
    ["aliexpress.com", "www.aliexpress.com", "pt.aliexpress.com", "de.aliexpress.com"],
)
def test_all_existing_canonical_hosts_rebuild_to_pt_aliexpress(host: str) -> None:
    result = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(), requester=FixtureRequester({})
    ).resolve_canonical(f"https://{host}/item/1005001234567890.html?tracking_id=foreign")

    assert result.generation_url == "https://pt.aliexpress.com/item/1005001234567890.html"


class SlowRequester:
    async def fetch(
        self,
        url: str,
        *,
        method: str,
        allowed_ips: frozenset[str],
    ) -> AliExpressRedirectHop:
        del url, method, allowed_ips
        await asyncio.sleep(1)
        return AliExpressRedirectHop(200, {})


@pytest.mark.asyncio
async def test_short_link_has_bounded_total_resolution_timeout() -> None:
    resolver = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(),
        requester=SlowRequester(),
        total_timeout_seconds=0.01,
    )

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_REDIRECT_TIMEOUT"):
        await resolver.resolve("https://s.click.aliexpress.com/e/_One")


class HttpFixtureStream(FixtureNetworkStream):
    def __init__(self, peer: str) -> None:
        super().__init__(peer)
        self.response_sent = False
        self.sni_hostnames: list[str | None] = []
        self.writes: list[bytes] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del max_bytes, timeout
        if self.response_sent:
            return b""
        self.response_sent = True
        return (
            b"HTTP/1.1 302 Found\r\n"
            b"Location: /item/1005001234567890.html\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(buffer)

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del ssl_context, timeout
        self.sni_hostnames.append(server_hostname)
        return self


@pytest.mark.asyncio
async def test_pinned_transport_preserves_original_host_for_sni_and_http_host() -> None:
    stream = HttpFixtureStream("8.8.8.8")
    delegate = FixtureNetworkBackend({"8.8.8.8": stream})
    transport = PinnedAliExpressHttpTransport(
        "s.click.aliexpress.com",
        frozenset({"8.8.8.8"}),
        network_delegate=delegate,
    )
    requester = AliExpressPinnedHttpRequester(
        timeout_seconds=2,
        transport_factory=lambda hostname, allowed_ips: transport,
    )

    response = await requester.fetch(
        "https://s.click.aliexpress.com/e/_PrivateCode",
        method="GET",
        allowed_ips=frozenset({"8.8.8.8"}),
    )

    assert response.status_code == 302
    assert stream.sni_hostnames == ["s.click.aliexpress.com"]
    wire = b"".join(stream.writes)
    assert b"Host: s.click.aliexpress.com" in wire
    assert b"8.8.8.8" not in wire
