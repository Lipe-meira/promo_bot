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
    is_supported_aliexpress_short_input,
)


class FixtureDnsResolver:
    def __init__(self, addresses: Mapping[str, frozenset[str]] | None = None) -> None:
        self.addresses = addresses or {
            "a.aliexpress.com": frozenset({"8.26.56.26"}),
            "s.click.aliexpress.com": frozenset({"8.8.8.8"}),
            "m.aliexpress.com": frozenset({"8.34.34.34"}),
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
async def test_a_aliexpress_input_uses_exact_proven_path_and_rebuilds_clean_url() -> None:
    short = "https://a.aliexpress.com/_Ab12Cd34"
    final = (
        "https://pt.aliexpress.com/item/1005001234567890.html?aff_fcid=foreign&tracking_id=foreign"
    )
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
    assert result.generation_url == "https://pt.aliexpress.com/item/1005001234567890.html"
    assert result.redirect_count == 1
    assert requester.calls == [
        (short, "GET", frozenset({"8.26.56.26"})),
        (final, "GET", frozenset({"1.0.0.1"})),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("short", "short_ip", "final_host", "final_ip"),
    [
        (
            "https://a.aliexpress.com/_Ab12Cd34",
            "8.26.56.26",
            "pt.aliexpress.com",
            "1.0.0.1",
        ),
        (
            "https://s.click.aliexpress.com/e/_ExistingShape",
            "8.8.8.8",
            "www.aliexpress.com",
            "1.1.1.1",
        ),
    ],
)
async def test_proven_shortener_can_cross_mobile_hop_to_canonical_product(
    short: str,
    short_ip: str,
    final_host: str,
    final_ip: str,
) -> None:
    mobile = "https://m.aliexpress.com/redirect-fixture?aff_trace_key=foreign"
    final = f"https://{final_host}/item/1005001234567890.html?tracking_id=foreign"
    dns = FixtureDnsResolver()
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(302, {"location": final}),
            final: AliExpressRedirectHop(200, {}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    result = await resolver.resolve(short)

    assert result == ResolvedAliExpressProduct(
        product_id="1005001234567890",
        variation_key="",
        identity_url="https://www.aliexpress.com/item/1005001234567890.html",
        generation_url="https://pt.aliexpress.com/item/1005001234567890.html",
        redirect_count=2,
    )
    assert requester.calls == [
        (short, "GET", frozenset({short_ip})),
        (mobile, "GET", frozenset({"8.34.34.34"})),
        (final, "GET", frozenset({final_ip})),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "short",
    [
        "https://a.aliexpress.com/_Ab12Cd34",
        "https://s.click.aliexpress.com/e/_ExistingShape",
    ],
)
async def test_mobile_hop_may_be_the_strict_terminal_product(short: str) -> None:
    mobile = (
        "https://m.aliexpress.com/item/1005001234567890.html"
        "?sku_id=200000001&tracking_id=foreign#affiliate-fragment"
    )
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(200, {}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    result = await resolver.resolve(short)

    assert result == ResolvedAliExpressProduct(
        product_id="1005001234567890",
        variation_key="",
        identity_url="https://www.aliexpress.com/item/1005001234567890.html",
        generation_url="https://pt.aliexpress.com/item/1005001234567890.html",
        redirect_count=1,
    )


@pytest.mark.asyncio
async def test_mobile_host_is_not_accepted_as_direct_short_input() -> None:
    dns = FixtureDnsResolver()
    requester = FixtureRequester({})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_SHORT_URL_REQUIRED"):
        await resolver.resolve("https://m.aliexpress.com/item/1005001234567890.html")

    assert dns.calls == []
    assert requester.calls == []


def test_mobile_host_is_not_accepted_as_canonical_product() -> None:
    resolver = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(), requester=FixtureRequester({})
    )

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_REDIRECT_HOST_FORBIDDEN"):
        resolver.resolve_canonical("https://m.aliexpress.com/item/1005001234567890.html")


@pytest.mark.asyncio
async def test_mobile_hop_is_allowed_only_from_proven_shorteners() -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    canonical_hop = "https://pt.aliexpress.com/redirect-fixture"
    mobile = "https://m.aliexpress.com/item/1005001234567890.html"
    dns = FixtureDnsResolver()
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": canonical_hop}),
            canonical_hop: AliExpressRedirectHop(302, {"location": mobile}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    assert captured.value.code == "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN"
    assert [call[0] for call in requester.calls] == [short, canonical_hop]
    assert [call[0] for call in dns.calls] == ["s.click.aliexpress.com", "pt.aliexpress.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/search/fixture.html",
        "/campaign/fixture",
        "/login",
        "/item/abc.html",
        "/item/+123.html",
        "/item/12.3.html",
        "/item/.html",
        "/ITEM/1005001234567890.html",
        "/item/1005001234567890.HTML",
    ],
)
async def test_mobile_terminal_accepts_only_exact_numeric_product_path(path: str) -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    mobile = f"https://m.aliexpress.com{path}"
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(200, {}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_PRODUCT_ID_NOT_FOUND"):
        await resolver.resolve(short)

    assert [call[0] for call in requester.calls] == [short, mobile]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "terminal",
        "terminal_host",
        "path_class",
        "path_segment_count",
        "has_numeric_path_candidate",
        "segment_shapes",
        "segment_length_buckets",
        "has_html_suffix",
    ),
    [
        ("https://m.aliexpress.com/", "m.aliexpress.com", "root", 0, False, (), (), False),
        (
            "https://m.aliexpress.com/item/not-numeric.html?token=SYNTHETIC_SECRET",
            "m.aliexpress.com",
            "item_shape_mismatch",
            2,
            False,
            ("ascii_alpha", "other"),
            ("1_4", "9_16"),
            True,
        ),
        (
            "https://pt.aliexpress.com/product/1005001234567890.html#private",
            "pt.aliexpress.com",
            "product_shape_mismatch",
            2,
            True,
            ("ascii_alpha", "other"),
            ("5_8", "17_32"),
            True,
        ),
        (
            "https://www.aliexpress.com/promo/1005001234567890/details",
            "www.aliexpress.com",
            "numeric_candidate_elsewhere",
            3,
            True,
            ("ascii_alpha", "ascii_numeric", "ascii_alpha"),
            ("5_8", "9_16", "5_8"),
            False,
        ),
        (
            "https://m.aliexpress.com/campaign/private-path",
            "m.aliexpress.com",
            "no_numeric_candidate",
            2,
            False,
            ("ascii_alpha", "ascii_hyphenated"),
            ("5_8", "9_16"),
            False,
        ),
    ],
)
async def test_terminal_2xx_product_rejection_carries_only_bounded_path_facts(
    terminal: str,
    terminal_host: str,
    path_class: str,
    path_segment_count: int,
    has_numeric_path_candidate: bool,
    segment_shapes: tuple[str, ...],
    segment_length_buckets: tuple[str, ...],
    has_html_suffix: bool,
) -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": terminal}),
            terminal: AliExpressRedirectHop(204, {}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    assert captured.value.code == "ALIEXPRESS_PRODUCT_ID_NOT_FOUND"
    diagnostic = getattr(captured.value, "terminal_diagnostic", None)
    assert diagnostic is not None
    assert diagnostic.as_dict() == {
        "terminal_host": terminal_host,
        "status_code": 204,
        "redirect_index": 1,
        "path_class": path_class,
        "path_segment_count": path_segment_count,
        "has_numeric_path_candidate": has_numeric_path_candidate,
        "segment_shapes": segment_shapes,
        "segment_length_buckets": segment_length_buckets,
        "has_html_suffix": has_html_suffix,
        "known_query_keys": (),
        "has_numeric_known_query_candidate": False,
        "link_canonical_class": "absent",
        "content_location_class": "absent",
        "decision_code": "ALIEXPRESS_PRODUCT_ID_NOT_FOUND",
    }
    rendered = repr(captured.value)
    assert "SYNTHETIC_SECRET" not in rendered
    assert "private-path" not in rendered


@pytest.mark.asyncio
async def test_terminal_diagnostic_classifies_query_and_headers_without_using_them() -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    terminal = (
        "https://m.aliexpress.com/product/promo-slug/notnumeric.html"
        "?productId=123456&item_id=not-a-number&tracking_id=SYNTHETIC_QUERY_SECRET"
    )
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": terminal}),
            terminal: AliExpressRedirectHop(
                200,
                {
                    "link": (
                        "<https://pt.aliexpress.com/item/1005001234567890.html?token="
                        "SYNTHETIC_LINK_SECRET>; rel=canonical"
                    ),
                    "content-location": (
                        "https://m.aliexpress.com/item/1005001234567890.html?tracking_id="
                        "SYNTHETIC_CONTENT_SECRET"
                    ),
                },
            ),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    assert captured.value.code == "ALIEXPRESS_PRODUCT_ID_NOT_FOUND"
    diagnostic = captured.value.terminal_diagnostic
    assert diagnostic is not None
    assert diagnostic.as_dict() == {
        "terminal_host": "m.aliexpress.com",
        "status_code": 200,
        "redirect_index": 1,
        "path_class": "product_shape_mismatch",
        "path_segment_count": 3,
        "has_numeric_path_candidate": False,
        "segment_shapes": ("ascii_alpha", "ascii_hyphenated", "other"),
        "segment_length_buckets": ("5_8", "9_16", "9_16"),
        "has_html_suffix": True,
        "known_query_keys": ("item_id", "product_id"),
        "has_numeric_known_query_candidate": True,
        "link_canonical_class": "allowed_product_path",
        "content_location_class": "mobile_product_path",
        "decision_code": "ALIEXPRESS_PRODUCT_ID_NOT_FOUND",
    }
    rendered = repr(captured.value)
    assert "123456" not in rendered
    assert "SYNTHETIC" not in rendered


@pytest.mark.asyncio
async def test_terminal_diagnostic_bounds_segments_query_and_header_classes() -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    segments = ["abc", "123", "a1", "a-b", "%41", "café", "x_y", "a" * 65, "ninth", "tenth"]
    query = "&".join([*(f"ignored{index}=value" for index in range(64)), "productId=123456"])
    terminal = f"https://m.aliexpress.com/{'/'.join(segments)}?{query}"
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": terminal}),
            terminal: AliExpressRedirectHop(
                200,
                {
                    "Link": (
                        "<https://pt.aliexpress.com/item/1.html>; rel=canonical,"
                        "<https://www.aliexpress.com/item/2.html>; rel=canonical"
                    ),
                    "CONTENT-LOCATION": "https://example.invalid/SYNTHETIC_HEADER_SECRET",
                },
            ),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    diagnostic = captured.value.terminal_diagnostic
    assert diagnostic is not None
    assert diagnostic.segment_shapes == (
        "ascii_alpha",
        "ascii_numeric",
        "ascii_alphanumeric",
        "ascii_hyphenated",
        "percent_encoded",
        "unicode",
        "other",
        "ascii_alpha",
    )
    assert diagnostic.segment_length_buckets == (
        "1_4",
        "1_4",
        "1_4",
        "1_4",
        "1_4",
        "1_4",
        "1_4",
        "65_plus",
    )
    assert diagnostic.path_segment_count == 10
    assert diagnostic.known_query_keys == ()
    assert diagnostic.has_numeric_known_query_candidate is False
    assert diagnostic.link_canonical_class == "multiple"
    assert diagnostic.content_location_class == "forbidden_host"
    assert "SYNTHETIC" not in repr(captured.value)


def test_local_canonical_rejection_has_no_network_terminal_diagnostic() -> None:
    resolver = AliExpressShortLinkResolver()

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        resolver.resolve_canonical("https://pt.aliexpress.com/campaign/1005001234567890")

    assert captured.value.code == "ALIEXPRESS_PRODUCT_ID_NOT_FOUND"
    assert getattr(captured.value, "terminal_diagnostic", None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    [
        "https://sub.m.aliexpress.com/item/1005001234567890.html",
        "https://m.aliexpress.com.evil.example/item/1005001234567890.html",
        "https://m-aliexpress.com/item/1005001234567890.html",
        "https://m.aliexpress.com.example/item/1005001234567890.html",
        "https://127.0.0.1/item/1005001234567890.html",
        "https://user@m.aliexpress.com/item/1005001234567890.html",
        "https://m.aliexpress.com:444/item/1005001234567890.html",
    ],
)
async def test_mobile_lookalikes_and_unsafe_authorities_are_rejected_before_dns(
    destination: str,
) -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    dns = FixtureDnsResolver()
    requester = FixtureRequester({short: AliExpressRedirectHop(302, {"location": destination})})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    assert captured.value.code in {
        "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
        "ALIEXPRESS_URL_USERINFO_FORBIDDEN",
        "ALIEXPRESS_URL_PORT_FORBIDDEN",
    }
    assert dns.calls == [("s.click.aliexpress.com", 443)]
    assert len(requester.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_ip",
    ["192.168.1.20", "127.0.0.1", "192.0.2.10", "169.254.10.20"],
)
async def test_mobile_hop_rejects_non_global_dns_before_connect(unsafe_ip: str) -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    mobile = "https://m.aliexpress.com/item/1005001234567890.html"
    dns = FixtureDnsResolver(
        {
            "s.click.aliexpress.com": frozenset({"8.8.8.8"}),
            "m.aliexpress.com": frozenset({unsafe_ip}),
        }
    )
    requester = FixtureRequester({short: AliExpressRedirectHop(302, {"location": mobile})})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_DNS_NON_GLOBAL_ADDRESS"):
        await resolver.resolve(short)

    assert requester.calls == [(short, "GET", frozenset({"8.8.8.8"}))]


@pytest.mark.asyncio
async def test_mobile_hop_rejects_new_destination_before_dns_with_sanitized_diagnostic() -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    mobile = "https://m.aliexpress.com/redirect-fixture?tracking_id=foreign"
    forbidden = "https://outside.example/private?token=SYNTHETIC_SECRET"
    dns = FixtureDnsResolver()
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(302, {"location": forbidden}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    assert captured.value.code == "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN"
    assert captured.value.redirect_diagnostic is not None
    assert captured.value.redirect_diagnostic.as_dict() == {
        "source_host": "m.aliexpress.com",
        "destination_host": "outside.example",
        "redirect_index": 2,
        "destination_scheme": "https",
        "status_code": 302,
        "decision_code": "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
    }
    assert [call[0] for call in dns.calls] == ["s.click.aliexpress.com", "m.aliexpress.com"]
    assert "SYNTHETIC_SECRET" not in repr(captured.value)
    assert "/private" not in repr(captured.value)


@pytest.mark.asyncio
async def test_mobile_hop_rejects_https_downgrade_before_dns() -> None:
    short = "https://a.aliexpress.com/_Ab12Cd34"
    mobile = "https://m.aliexpress.com/redirect-fixture"
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(
                302,
                {"location": "http://pt.aliexpress.com/item/1005001234567890.html"},
            ),
        }
    )
    dns = FixtureDnsResolver()
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_HTTPS_REQUIRED"):
        await resolver.resolve(short)

    assert [call[0] for call in dns.calls] == ["a.aliexpress.com", "m.aliexpress.com"]


@pytest.mark.asyncio
async def test_mobile_hop_participates_in_redirect_loop_detection() -> None:
    first = "https://a.aliexpress.com/_Ab12Cd34"
    mobile = "https://m.aliexpress.com/redirect-fixture"
    requester = FixtureRequester(
        {
            first: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(302, {"location": first}),
        }
    )
    resolver = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(), requester=requester, max_redirects=5
    )

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_REDIRECT_LOOP"):
        await resolver.resolve(first)

    assert [call[0] for call in requester.calls] == [first, mobile]


@pytest.mark.asyncio
async def test_mobile_hop_remains_subject_to_redirect_limit() -> None:
    first = "https://s.click.aliexpress.com/e/_ExistingShape"
    mobile = "https://m.aliexpress.com/redirect-fixture"
    final = "https://pt.aliexpress.com/item/1005001234567890.html"
    requester = FixtureRequester(
        {
            first: AliExpressRedirectHop(302, {"location": mobile}),
            mobile: AliExpressRedirectHop(302, {"location": final}),
        }
    )
    resolver = AliExpressShortLinkResolver(
        resolver=FixtureDnsResolver(), requester=requester, max_redirects=1
    )

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_TOO_MANY_REDIRECTS"):
        await resolver.resolve(first)

    assert [call[0] for call in requester.calls] == [first, mobile]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://a.aliexpress.com/_Ab12Cd3",
        "https://a.aliexpress.com/_Ab12Cd345",
        "https://a.aliexpress.com/e/_Ab12Cd34",
        "https://a.aliexpress.com/_Ab12-Cd3",
        "https://a.aliexpress.com/_Ab12Cd34/",
        "https://a.aliexpress.com/_Ab12Cd34?tracking=foreign",
        "https://a.aliexpress.com/_Ab12Cd34#fragment",
        "https://a.aliexpress.com.evil.example/_Ab12Cd34",
    ],
)
async def test_a_aliexpress_input_rejects_every_unproven_shape_before_network(url: str) -> None:
    dns = FixtureDnsResolver()
    requester = FixtureRequester({})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected):
        await resolver.resolve(url)

    assert dns.calls == []
    assert requester.calls == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://s.click.aliexpress.com/e/_ExistingShape", True),
        ("https://a.aliexpress.com/_Ab12Cd34", True),
        ("https://a.aliexpress.com/_Ab12Cd3", False),
        ("https://a.aliexpress.com/_Ab12Cd345", False),
        ("https://a.aliexpress.com/_Ab12Cd34?tracking=foreign", False),
        ("https://a.aliexpress.com/_Ab12Cd34#fragment", False),
        ("https://a.aliexpress.com.evil.example/_Ab12Cd34", False),
    ],
)
def test_supported_short_input_contract_is_exact(url: str, expected: bool) -> None:
    assert is_supported_aliexpress_short_input(url) is expected


@pytest.mark.asyncio
async def test_a_aliexpress_input_rejects_external_redirect_before_second_request() -> None:
    short = "https://a.aliexpress.com/_Ab12Cd34"
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(
                302,
                {"location": "https://aliexpress.com.evil.example/item/1.html"},
            )
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_REDIRECT_HOST_FORBIDDEN"):
        await resolver.resolve(short)

    assert len(requester.calls) == 1


@pytest.mark.asyncio
async def test_redirect_rejection_carries_only_normalized_hop_diagnostics() -> None:
    short = "https://A.ALIEXPRESS.COM/_Ab12Cd34"
    destination = "https://BÜCHER.example/private?token=SYNTHETIC_SECRET"
    requester = FixtureRequester({short: AliExpressRedirectHop(302, {"location": destination})})
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    diagnostic = getattr(captured.value, "redirect_diagnostic", None)
    assert diagnostic is not None
    assert diagnostic.as_dict() == {
        "source_host": "a.aliexpress.com",
        "destination_host": "xn--bcher-kva.example",
        "redirect_index": 1,
        "destination_scheme": "https",
        "status_code": 302,
        "decision_code": "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
    }
    rendered = repr(captured.value)
    assert "SYNTHETIC_SECRET" not in rendered
    assert "/private" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "destination_host", "destination_scheme", "decision_code"),
    [
        (
            "https://127.0.0.1/private",
            "[IP_LITERAL]",
            "https",
            "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
        ),
        (
            "mailto:fixture@example.com",
            "[MISSING_HOST]",
            "mailto",
            "ALIEXPRESS_HTTPS_REQUIRED",
        ),
        (
            "https://bad host.example/private",
            "[INVALID_HOST]",
            "https",
            "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
        ),
        (
            f"https://{'a' * 254}.example/private",
            "[INVALID_HOST]",
            "https",
            "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
        ),
        (
            f"https://{'.'.join(('a' * 63, 'b' * 63, 'c' * 63, 'd' * 61))}/private",
            ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61)),
            "https",
            "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
        ),
        (
            "https://[",
            "[INVALID_HOST]",
            "https",
            "ALIEXPRESS_URL_INVALID",
        ),
        *[
            (
                f"https://a{control}.example/private",
                "[INVALID_HOST]",
                "https",
                "ALIEXPRESS_URL_INVALID",
            )
            for control in ("\u00ad", "\u200b", "\u2060", "\ufeff")
        ],
    ],
)
async def test_redirect_diagnostics_use_stable_host_markers(
    location: str,
    destination_host: str,
    destination_scheme: str,
    decision_code: str,
) -> None:
    short = "https://a.aliexpress.com/_Ab12Cd34"
    requester = FixtureRequester({short: AliExpressRedirectHop(302, {"location": location})})
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(short)

    diagnostic = getattr(captured.value, "redirect_diagnostic", None)
    assert diagnostic is not None
    assert diagnostic.destination_host == destination_host
    assert diagnostic.destination_scheme == destination_scheme
    assert diagnostic.decision_code == decision_code
    assert diagnostic.redirect_index == 1
    assert diagnostic.status_code == 302


@pytest.mark.asyncio
async def test_redirect_diagnostic_identifies_the_rejected_hop_without_urls() -> None:
    first = "https://a.aliexpress.com/_Ab12Cd34"
    second = "https://pt.aliexpress.com/redirect-fixture"
    rejected = "https://BÜCHER.example/private?token=SYNTHETIC_SECRET"
    requester = FixtureRequester(
        {
            first: AliExpressRedirectHop(301, {"location": second}),
            second: AliExpressRedirectHop(307, {"location": rejected}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected) as captured:
        await resolver.resolve(first)

    diagnostic = captured.value.redirect_diagnostic
    assert diagnostic is not None
    assert diagnostic.as_dict() == {
        "source_host": "pt.aliexpress.com",
        "destination_host": "xn--bcher-kva.example",
        "redirect_index": 2,
        "destination_scheme": "https",
        "status_code": 307,
        "decision_code": "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
    }
    assert [call[0] for call in requester.calls] == [first, second]


@pytest.mark.asyncio
async def test_redirect_to_a_aliexpress_with_unproven_path_is_rejected_before_request() -> None:
    short = "https://s.click.aliexpress.com/e/_ExistingShape"
    unproven = "https://a.aliexpress.com/e/_Unproven"
    requester = FixtureRequester(
        {
            short: AliExpressRedirectHop(
                302,
                {"location": unproven},
            ),
            unproven: AliExpressRedirectHop(200, {}),
        }
    )
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_SHORT_URL_REQUIRED"):
        await resolver.resolve(short)

    assert len(requester.calls) == 1


@pytest.mark.asyncio
async def test_a_aliexpress_input_rejects_private_dns_before_request() -> None:
    short = "https://a.aliexpress.com/_Ab12Cd34"
    dns = FixtureDnsResolver({"a.aliexpress.com": frozenset({"192.168.1.20"})})
    requester = FixtureRequester({})
    resolver = AliExpressShortLinkResolver(resolver=dns, requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_DNS_NON_GLOBAL_ADDRESS"):
        await resolver.resolve(short)

    assert requester.calls == []


@pytest.mark.asyncio
async def test_a_aliexpress_input_detects_self_redirect_loop_after_one_request() -> None:
    short = "https://a.aliexpress.com/_Ab12Cd34"
    requester = FixtureRequester({short: AliExpressRedirectHop(302, {"location": short})})
    resolver = AliExpressShortLinkResolver(resolver=FixtureDnsResolver(), requester=requester)

    with pytest.raises(AliExpressShortLinkRejected, match="ALIEXPRESS_REDIRECT_LOOP"):
        await resolver.resolve(short)

    assert len(requester.calls) == 1


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


@pytest.mark.asyncio
async def test_mobile_hop_transport_pins_ip_but_preserves_mobile_host_for_tls_and_http() -> None:
    stream = HttpFixtureStream("8.34.34.34")
    delegate = FixtureNetworkBackend({"8.34.34.34": stream})
    transport = PinnedAliExpressHttpTransport(
        "m.aliexpress.com",
        frozenset({"8.34.34.34"}),
        network_delegate=delegate,
    )
    requester = AliExpressPinnedHttpRequester(
        timeout_seconds=2,
        transport_factory=lambda hostname, allowed_ips: transport,
    )

    response = await requester.fetch(
        "https://m.aliexpress.com/redirect-fixture",
        method="GET",
        allowed_ips=frozenset({"8.34.34.34"}),
    )

    assert response.status_code == 302
    assert delegate.calls == [("8.34.34.34", 443, 2)]
    assert stream.sni_hostnames == ["m.aliexpress.com"]
    wire = b"".join(stream.writes)
    assert b"Host: m.aliexpress.com" in wire
    assert b"8.34.34.34" not in wire
