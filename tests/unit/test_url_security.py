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
    requester = FakeRequester([HopResponse(302, {"location": "http://127.0.0.1/admin"}, GLOBAL_IP)])
    expander = SafeUrlExpander(resolver=FakeResolver(), requester=requester)

    with pytest.raises(SafeUrlError, match="DOMAIN_NOT_ALLOWED"):
        await expander.expand("https://amzn.to/example")

    assert len(requester.calls) == 1


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
