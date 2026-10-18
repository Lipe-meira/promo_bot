import asyncio

import httpx
import pytest

from promo_bot.security.seuhardware_short_links import (
    SeuHardwareRejected,
    SeuHardwareShortResolver,
    validate_seuhardware_short,
)
from promo_bot.security.urls import HopResponse
from tests.unit.test_coin_shadow_multi_contract import A
from tests.unit.test_seuhardware_listener import W


class Dns:
    def __init__(self, addresses=("8.8.8.8",)):
        self.addresses = addresses
        self.calls = []

    async def resolve(self, hostname, port):
        self.calls.append((hostname, port))
        return frozenset(self.addresses)


class Headers:
    def __init__(self, *, status=302, location=A, peer="8.8.8.8", error=None):
        self.response = HopResponse(
            status, {} if location is None else {"location": location}, peer
        )
        self.calls = []
        self.error = error

    async def fetch(self, url, *, method, allowed_ips):
        self.calls.append((url, method, allowed_ips))
        if self.error:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    "url",
    [
        W.replace("https:", "http:"),
        W + "?",
        W + "#",
        W + "/",
        W.replace(".com/", ".com:443/"),
        W.replace("//", "//user@"),
        W.replace("seuhardware.com", "www.seuhardware.com"),
        W.replace("seuhardware.com", "seuhardware.com.evil.example"),
        W.replace("seuhardware.com", "SEUHARDWARE.COM"),
        "https://seuhardware.com/s/Ab12Cd",
        "https://seuhardware.com/s/Ab12Cd34",
        "https://seuhardware.com/s/Ab12Cdé",
        W + "\n",
        W + "%20",
        W + "?x=1",
    ],
)
@pytest.mark.asyncio
async def test_strict_wrapper_input_rejects_before_dns_or_http(url):
    dns, headers = Dns(), Headers()
    service = SeuHardwareShortResolver(resolver=dns, requester=headers)
    with pytest.raises(SeuHardwareRejected, match="SEUHARDWARE_SHORT_INVALID"):
        await service.resolve(url)
    assert dns.calls == headers.calls == []


@pytest.mark.parametrize(
    "location",
    [
        None,
        "",
        "/s/Ab12Cd3",
        W,
        "https://seuhardware.com/s/Ef45Gh6",
        "https://example.com/",
        A.replace("https:", "http:"),
        A + "?tracking=foreign",
        A + "#",
        A + "/",
        "https://pt.aliexpress.com/item/123.html",
        "not a URL",
    ],
)
@pytest.mark.asyncio
async def test_invalid_location_is_not_followed(location):
    headers = Headers(location=location)
    service = SeuHardwareShortResolver(resolver=Dns(), requester=headers)
    with pytest.raises(SeuHardwareRejected, match=r"SEUHARDWARE_LOCATION_(MISSING|INVALID)"):
        await service.resolve(W)
    assert len(headers.calls) == 1


@pytest.mark.parametrize("status", [200, 301, 303, 307, 308, 403, 429, 500, 503])
@pytest.mark.asyncio
async def test_only_the_observed_302_contract_is_accepted(status):
    headers = Headers(status=status)
    with pytest.raises(SeuHardwareRejected, match="SEUHARDWARE_STATUS_UNSUPPORTED"):
        await SeuHardwareShortResolver(resolver=Dns(), requester=headers).resolve(W)
    assert len(headers.calls) == 1


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1",),
        ("10.0.0.1",),
        ("::1",),
        ("169.254.169.254",),
        ("8.8.8.8", "192.168.1.1"),
        ("invalid",),
    ],
)
@pytest.mark.asyncio
async def test_non_public_or_invalid_dns_is_rejected_before_get(addresses):
    headers = Headers()
    with pytest.raises(SeuHardwareRejected):
        await SeuHardwareShortResolver(resolver=Dns(addresses), requester=headers).resolve(W)
    assert headers.calls == []


@pytest.mark.parametrize("peer", [None, "127.0.0.1", "1.1.1.1", "invalid"])
@pytest.mark.asyncio
async def test_actual_peer_must_be_public_and_equal_to_the_single_pinned_ip(peer):
    with pytest.raises(SeuHardwareRejected, match="SEUHARDWARE_PEER_IP_"):
        await SeuHardwareShortResolver(resolver=Dns(), requester=Headers(peer=peer)).resolve(W)


@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ReadTimeout("sensitive"), "SEUHARDWARE_TIMEOUT"),
        (httpx.ConnectError("sensitive"), "SEUHARDWARE_RESOLUTION_FAILED"),
        (OSError("sensitive"), "SEUHARDWARE_RESOLUTION_FAILED"),
    ],
)
@pytest.mark.asyncio
async def test_transport_errors_are_sanitized_without_retry(error, code):
    headers = Headers(error=error)
    with pytest.raises(SeuHardwareRejected) as caught:
        await SeuHardwareShortResolver(resolver=Dns(), requester=headers).resolve(W)
    assert str(caught.value) == code and len(headers.calls) == 1


@pytest.mark.asyncio
async def test_total_timeout_also_bounds_dns():
    class SlowDns:
        async def resolve(self, *_args):
            await asyncio.sleep(30)

    headers = Headers()
    with pytest.raises(SeuHardwareRejected, match="SEUHARDWARE_TIMEOUT"):
        await SeuHardwareShortResolver(resolver=SlowDns(), requester=headers).resolve(W)
    assert headers.calls == []


@pytest.mark.asyncio
async def test_size_limit_and_no_body_read_through_real_header_requester(monkeypatch, caplog):
    from promo_bot.security import seuhardware_short_links as module
    from tests.unit.test_aliexpress_short_links import ExplodingResponseBody, FixtureNetworkStream

    calls = []

    async def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(
            302,
            headers={"location": A},
            stream=ExplodingResponseBody(),
            extensions={"network_stream": FixtureNetworkStream("8.8.8.8")},
        )

    monkeypatch.setattr(
        module, "PinnedAliExpressHttpTransport", lambda host, ips: httpx.MockTransport(handle)
    )
    observed = []
    resolver = SeuHardwareShortResolver(
        resolver=Dns(("8.8.8.8", "9.9.9.9")), before_request=lambda: observed.append("dispatch")
    )
    assert await resolver.resolve(W) == A
    assert len(calls) == 1 and observed == ["dispatch"]
    assert W not in caplog.text and A not in caplog.text
    oversized = Headers(location="x" * 16_384)
    with pytest.raises(SeuHardwareRejected, match="SEUHARDWARE_HEADERS_TOO_LARGE"):
        await SeuHardwareShortResolver(resolver=Dns(), requester=oversized).resolve(W)


@pytest.mark.parametrize("peer,allowed", [("8.8.8.8", True), ("127.0.0.1", False)])
@pytest.mark.asyncio
async def test_real_requester_pins_connect_sni_host_and_checks_peer(monkeypatch, peer, allowed):
    from promo_bot.security import seuhardware_short_links as module
    from promo_bot.security.aliexpress_short_links import PinnedAliExpressHttpTransport
    from tests.unit.test_aliexpress_short_links import FixtureNetworkBackend, HttpFixtureStream

    class Stream(HttpFixtureStream):
        async def read(self, *_args, **_kwargs):
            if self.response_sent:
                return b""
            self.response_sent = True
            return (
                "HTTP/1.1 302 Found\r\nLocation: " + A + "\r\nContent-Length: 0\r\n\r\n"
            ).encode()

    stream = Stream(peer)
    network = FixtureNetworkBackend({"8.8.8.8": stream})
    monkeypatch.setattr(
        module,
        "PinnedAliExpressHttpTransport",
        lambda host, ips: PinnedAliExpressHttpTransport(host, ips, network_delegate=network),
    )
    resolver = SeuHardwareShortResolver(resolver=Dns(("8.8.8.8", "9.9.9.9")))
    if allowed:
        assert await resolver.resolve(W) == A
        assert stream.sni_hostnames == ["seuhardware.com"]
        assert b"Host: seuhardware.com" in b"".join(stream.writes)
    else:
        with pytest.raises(SeuHardwareRejected):
            await resolver.resolve(W)
        assert stream.closed
    assert len(network.calls) == 1 and network.calls[0][:2] == ("8.8.8.8", 443)


def test_wrapper_does_not_expand_the_global_allowlist():
    from promo_bot.stores.urls import ALLOWED_NETWORK_HOSTS

    assert "seuhardware.com" not in ALLOWED_NETWORK_HOSTS
    assert validate_seuhardware_short(W) == W


@pytest.mark.asyncio
async def test_real_http_protocol_failure_is_sanitized_without_retry(monkeypatch):
    from promo_bot.security import seuhardware_short_links as module
    from promo_bot.security.aliexpress_short_links import PinnedAliExpressHttpTransport
    from tests.unit.test_aliexpress_short_links import FixtureNetworkBackend, HttpFixtureStream

    class MalformedStream(HttpFixtureStream):
        async def read(self, *_args, **_kwargs):
            if self.response_sent:
                return b""
            self.response_sent = True
            return b"HTTP/1.1 302 Found\r\ninvalid header\r\n\r\n"

    stream = MalformedStream("8.8.8.8")
    network = FixtureNetworkBackend({"8.8.8.8": stream})
    monkeypatch.setattr(
        module,
        "PinnedAliExpressHttpTransport",
        lambda host, ips: PinnedAliExpressHttpTransport(host, ips, network_delegate=network),
    )
    with pytest.raises(SeuHardwareRejected) as caught:
        await SeuHardwareShortResolver(resolver=Dns()).resolve(W)
    assert str(caught.value) == "SEUHARDWARE_RESOLUTION_FAILED"
    assert len(network.calls) == 1 and stream.closed
