from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from promo_bot.security.urls import (
    HopResponse,
    SafeUrlError,
    SafeUrlExpander,
    TransientUrlError,
)

GLOBAL_IP = "93.184.216.34"


@dataclass
class FakeResolver:
    addresses: frozenset[str] = frozenset({GLOBAL_IP})
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def resolve(self, hostname: str, port: int) -> frozenset[str]:
        self.calls.append((hostname, port))
        return self.addresses


@dataclass
class FakeRequester:
    responses: list[HopResponse]
    calls: list[tuple[str, str, frozenset[str]]] = field(default_factory=list)

    async def fetch(self, url: str, *, method: str, allowed_ips: frozenset[str]) -> HopResponse:
        self.calls.append((url, method, allowed_ips))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_validates_every_redirect_and_actual_peer() -> None:
    resolver = FakeResolver()
    requester = FakeRequester(
        [
            HopResponse(302, {"location": "https://www.amazon.com.br/dp/B0ABCDEFGH"}, GLOBAL_IP),
            HopResponse(200, {}, GLOBAL_IP),
        ]
    )
    expander = SafeUrlExpander(resolver=resolver, requester=requester, max_redirects=3)

    result = await expander.expand("https://amzn.to/example")

    assert result.url == "https://www.amazon.com.br/dp/B0ABCDEFGH"
    assert result.redirect_count == 1
    assert resolver.calls == [("amzn.to", 443), ("www.amazon.com.br", 443)]
    assert all(call[2] == frozenset({GLOBAL_IP}) for call in requester.calls)


@pytest.mark.asyncio
async def test_redirect_to_non_allowlisted_domain_fails_before_request() -> None:
    requester = FakeRequester(
        [HopResponse(302, {"location": "https://127.0.0.1/admin"}, GLOBAL_IP)]
    )
    expander = SafeUrlExpander(resolver=FakeResolver(), requester=requester)

    with pytest.raises(SafeUrlError, match="DOMAIN_NOT_ALLOWED"):
        await expander.expand("https://amzn.to/example")

    assert len(requester.calls) == 1


@pytest.mark.asyncio
async def test_https_to_http_redirect_is_rejected_before_destination_request() -> None:
    resolver = FakeResolver()
    requester = FakeRequester(
        [HopResponse(302, {"location": "http://www.amazon.com.br/dp/B0ABCDEFGH"}, GLOBAL_IP)]
    )
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    with pytest.raises(SafeUrlError, match="HTTPS_DOWNGRADE_FORBIDDEN"):
        await expander.expand("https://amzn.to/example")

    assert resolver.calls == [("amzn.to", 443)]
    assert [call[0] for call in requester.calls] == ["https://amzn.to/example"]


@pytest.mark.asyncio
async def test_http_to_https_redirect_is_allowed() -> None:
    resolver = FakeResolver()
    requester = FakeRequester(
        [
            HopResponse(302, {"location": "https://www.amazon.com.br/dp/B0ABCDEFGH"}, GLOBAL_IP),
            HopResponse(200, {}, GLOBAL_IP),
        ]
    )
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    result = await expander.expand("http://amzn.to/example")

    assert result.url == "https://www.amazon.com.br/dp/B0ABCDEFGH"
    assert resolver.calls == [("amzn.to", 80), ("www.amazon.com.br", 443)]


@pytest.mark.asyncio
async def test_relative_redirect_from_https_preserves_https() -> None:
    requester = FakeRequester(
        [
            HopResponse(302, {"location": "/next"}, GLOBAL_IP),
            HopResponse(200, {}, GLOBAL_IP),
        ]
    )
    expander = SafeUrlExpander(resolver=FakeResolver(), requester=requester)

    result = await expander.expand("https://amzn.to/example")

    assert result.url == "https://amzn.to/next"
    assert [call[0] for call in requester.calls] == [
        "https://amzn.to/example",
        "https://amzn.to/next",
    ]


@pytest.mark.asyncio
async def test_dns_non_global_address_is_rejected() -> None:
    expander = SafeUrlExpander(
        resolver=FakeResolver(frozenset({"127.0.0.1"})),
        requester=FakeRequester([]),
    )

    with pytest.raises(SafeUrlError, match="DNS_NON_GLOBAL_ADDRESS"):
        await expander.expand("https://amzn.to/example")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("peer", "code"),
    [(None, "PEER_IP_UNVERIFIED"), ("8.8.8.8", "PEER_IP_DNS_MISMATCH")],
)
async def test_unverified_or_mismatched_peer_fails_closed(peer: str | None, code: str) -> None:
    expander = SafeUrlExpander(
        resolver=FakeResolver(), requester=FakeRequester([HopResponse(200, {}, peer)])
    )

    with pytest.raises(SafeUrlError, match=code):
        await expander.expand("https://amzn.to/example")


@pytest.mark.asyncio
async def test_uses_streamed_get_without_reading_a_response_body() -> None:
    resolver = FakeResolver()
    requester = FakeRequester([HopResponse(200, {}, GLOBAL_IP)])
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    await expander.expand("https://amzn.to/example")

    assert [call[1] for call in requester.calls] == ["GET"]
    assert len(resolver.calls) == 1


@pytest.mark.asyncio
async def test_transient_and_security_http_statuses_are_distinct() -> None:
    transient = SafeUrlExpander(
        resolver=FakeResolver(), requester=FakeRequester([HopResponse(429, {}, GLOBAL_IP)])
    )
    permanent = SafeUrlExpander(
        resolver=FakeResolver(), requester=FakeRequester([HopResponse(403, {}, GLOBAL_IP)])
    )

    with pytest.raises(TransientUrlError, match="HTTP_429"):
        await transient.expand("https://amzn.to/example")
    with pytest.raises(SafeUrlError, match="HTTP_FORBIDDEN_OR_CHALLENGE"):
        await permanent.expand("https://amzn.to/example")


FORBIDDEN_CHARACTER_URLS = [
    pytest.param("https://amzn.to/ab\tc", id="tab"),
    pytest.param("https://amzn.to/ab\nc", id="line-feed"),
    pytest.param("https://amzn.to/ab\r\nc", id="crlf"),
    pytest.param("https://amzn.to/ab\x00c", id="nul"),
    pytest.param("https://amzn.to/ab\x7fc", id="del"),
    pytest.param("https://amzn.to/ab c", id="space-in-path"),
    pytest.param("https://amzn.to /abc", id="space-in-authority"),
    pytest.param("https://amzn.to\t/abc", id="tab-in-authority"),
    pytest.param("https://amzn.to/ab\u202ec", id="bidi-override"),
    pytest.param("https://amzn.to/ab\u200bc", id="zero-width-space"),
    pytest.param("https://amzn.to/ab\u2028c", id="line-separator"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", FORBIDDEN_CHARACTER_URLS)
async def test_control_characters_and_spaces_are_rejected_before_dns_or_request(url: str) -> None:
    # Breaks if: validation runs after urlsplit (which silently drops tabs and newlines), or
    # after DNS resolution, so a malformed input still triggers a lookup or a request.
    resolver = FakeResolver()
    requester = FakeRequester([])
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    with pytest.raises(SafeUrlError) as captured:
        await expander.expand(url)

    assert captured.value.code == "INVALID_URL"
    assert resolver.calls == []
    assert requester.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "code"),
    [
        pytest.param("https://\uff41mzn.to/abc", "INVALID_URL", id="fullwidth-host"),
        pytest.param("https://amzn\u3002to/abc", "INVALID_URL", id="ideographic-full-stop-host"),
        pytest.param("https://\u0455hopee.com.br/x", "INVALID_URL", id="cyrillic-lookalike-host"),
        pytest.param("https://amzn.to/" + "a" * 5000, "INVALID_URL", id="oversized-url"),
        pytest.param("https://amzn.to:0/abc", "PORT_NOT_ALLOWED", id="port-zero"),
        pytest.param("https://amzn.to:8443/abc", "PORT_NOT_ALLOWED", id="port-8443"),
        pytest.param("https://user@amzn.to/abc", "URL_CREDENTIALS_FORBIDDEN", id="userinfo"),
        pytest.param("https://amzn.to@evil.example/", "URL_CREDENTIALS_FORBIDDEN", id="userinfo-2"),
        pytest.param("ftp://amzn.to/abc", "UNSUPPORTED_SCHEME", id="ftp"),
        pytest.param("https://amzn.to.evil.example/abc", "DOMAIN_NOT_ALLOWED", id="host-suffix"),
        pytest.param("https://evilamzn.to/abc", "DOMAIN_NOT_ALLOWED", id="host-prefix"),
    ],
)
async def test_malformed_authority_is_rejected_before_dns_or_request(url: str, code: str) -> None:
    # Breaks if: malformed or non-ASCII authorities, port 0 (treated as the default port) or
    # oversized inputs reach DNS resolution or the network.
    resolver = FakeResolver()
    requester = FakeRequester([])
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    with pytest.raises(SafeUrlError) as captured:
        await expander.expand(url)

    assert captured.value.code == code
    assert resolver.calls == []
    assert requester.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        pytest.param("https://www.amazon.com.br/dp/B0ABCDEFGH\r\nX-Injected: 1", id="crlf"),
        pytest.param("https://www.amazon.com.br/dp/B0ABCDEFGH\tX", id="tab"),
        pytest.param("https://www.amazon.com.br/dp/B0 ABCDEFGH", id="space"),
        pytest.param("https://www.amazon.com.br/dp/B0ABCDEFGH\u202e", id="bidi-override"),
        pytest.param("/dp/B0\nABCDEFGH", id="relative-with-newline"),
    ],
)
async def test_redirect_location_with_control_characters_is_rejected_before_next_hop(
    location: str,
) -> None:
    # Breaks if: the Location header is checked only after urljoin, which strips tabs and
    # newlines and would silently turn a malformed header into a different, valid-looking URL.
    resolver = FakeResolver()
    requester = FakeRequester([HopResponse(302, {"location": location}, GLOBAL_IP)])
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    with pytest.raises(SafeUrlError) as captured:
        await expander.expand("https://amzn.to/example")

    assert captured.value.code == "INVALID_REDIRECT_URL"
    assert resolver.calls == [("amzn.to", 443)]
    assert len(requester.calls) == 1


@pytest.mark.asyncio
async def test_redirect_to_non_ascii_lookalike_host_is_rejected_before_its_dns_lookup() -> None:
    resolver = FakeResolver()
    requester = FakeRequester(
        [HopResponse(302, {"location": "https://\uff41mzn.to/next"}, GLOBAL_IP)]
    )
    expander = SafeUrlExpander(resolver=resolver, requester=requester)

    with pytest.raises(SafeUrlError) as captured:
        await expander.expand("https://amzn.to/example")

    assert captured.value.code == "INVALID_URL"
    assert resolver.calls == [("amzn.to", 443)]
    assert len(requester.calls) == 1


@pytest.mark.asyncio
async def test_legitimate_redirect_with_query_and_non_ascii_path_is_preserved() -> None:
    # Breaks if: hardening becomes so strict that real store redirects (percent-encoded query
    # parameters, raw non-ASCII product slugs) are rejected or rewritten.
    location = "https://www.amazon.com.br/Açaí-Café/dp/B0ABCDEFGH?th=1&psc=1&ref=a%20b"
    requester = FakeRequester(
        [
            HopResponse(302, {"location": location}, GLOBAL_IP),
            HopResponse(200, {}, GLOBAL_IP),
        ]
    )
    expander = SafeUrlExpander(resolver=FakeResolver(), requester=requester)

    result = await expander.expand("https://amzn.to/example")

    assert result.url == location
    assert requester.calls[1][0] == location
