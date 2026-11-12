"""Links returned by the official AliExpress API become text inside Telegram messages.

Every validator of such a link must apply the same strict contract: raw characters are checked
before parsing (``urlsplit`` silently drops tabs and newlines), then each URL component is checked
explicitly. Legitimate links, including percent-encoded query parameters, must pass unchanged.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from promo_bot.affiliate.aliexpress_conversion import _is_valid_affiliate_link
from promo_bot.providers.aliexpress.coin_shadow import parse_coin_shadow_link_generate
from promo_bot.providers.aliexpress.parsing import parse_link_generate
from promo_bot.providers.base import ProviderError

CANONICAL_SOURCE = "https://www.aliexpress.com/item/1005000000000001.html"
COIN_SOURCE = "https://s.click.aliexpress.com/e/_Ab12Cd3"
TRACKING = "configured-tracking"
HOST = "https://s.click.aliexpress.com"


def _response(source: str, link: object) -> dict[str, object]:
    return {
        "code": "0",
        "aliexpress_affiliate_link_generate_response": {
            "resp_result": {
                "resp_code": "200",
                "resp_msg": "success",
                "result": {
                    "tracking_id": TRACKING,
                    "promotion_links": [{"source_value": source, "promotion_link": link}],
                },
            }
        },
    }


def canonical_parser(link: object) -> str:
    (mapping,) = parse_link_generate(
        _response(CANONICAL_SOURCE, link),
        requested_source_values=(CANONICAL_SOURCE,),
        expected_tracking_id=TRACKING,
    )
    return mapping.promotion_link


def coin_parser(link: object) -> str:
    return parse_coin_shadow_link_generate(
        _response(COIN_SOURCE, link), sent_source_value=COIN_SOURCE, expected_tracking_id=TRACKING
    ).promotion_link


def stored_link_check(link: object) -> str:
    """The predicate that decides whether a persisted proof may be reused from the cache."""

    if not isinstance(link, str) or not _is_valid_affiliate_link(link):
        raise ProviderError("ALIEXPRESS_STORED_PROMOTION_LINK_INVALID", retryable=False)
    return link


ENTRY_POINTS: list[Callable[[object], str]] = [canonical_parser, coin_parser, stored_link_check]
ENTRY_IDS = ["canonical-parser", "coin-parser", "stored-link-check"]

BAD_LINKS = [
    pytest.param(f"{HOST}/e/_AbCdEfG\nhttps://evil.example/x", id="line-feed-and-second-url"),
    pytest.param(f"{HOST}/e/_AbCdEfG\r\nGanhe premio", id="crlf-and-text"),
    pytest.param(f"{HOST}/e/_AbCdEfG gratis", id="space-and-text"),
    pytest.param(f"{HOST}/e/_AbC\tdEfG", id="tab-inside"),
    pytest.param(f"{HOST}/e/_AbC\x00dEfG", id="nul"),
    pytest.param(f"{HOST}/e/_AbC\x7fdEfG", id="del"),
    pytest.param(f"{HOST}/e/_AbCdEfG\u202e", id="bidi-override"),
    pytest.param(f"{HOST}/e/_AbC\u2066dEfG", id="bidi-isolate"),
    pytest.param(f"{HOST}/e/_AbC\u200bdEfG", id="zero-width-space"),
    pytest.param(f"{HOST}/e/_AbC\u00a0dEfG", id="no-break-space"),
    pytest.param(f"{HOST}/e/_AbC\u2028dEfG", id="line-separator"),
    pytest.param(f"{HOST}/e/_AbCdEfG\ufeff", id="byte-order-mark"),
    pytest.param(f"{HOST}/e/_AbÇdEfG", id="non-ascii-path"),
    pytest.param("https://\u0455.click.aliexpress.com/e/_AbCdEfG", id="cyrillic-lookalike-host"),
    pytest.param(f"{HOST}/e/_AbCdEfG<script>", id="angle-brackets"),
    pytest.param(f'{HOST}/e/_AbCdEfG"onmouseover=', id="double-quote"),
    pytest.param(f"{HOST}/e/_AbCdEfG|pipe", id="pipe"),
    pytest.param("https://s.click.aliexpress.com\\@evil.example/e/_x", id="backslash-authority"),
    pytest.param("https://user@s.click.aliexpress.com/e/_AbCdEfG", id="userinfo"),
    pytest.param("https://@s.click.aliexpress.com/e/_AbCdEfG", id="empty-userinfo"),
    pytest.param("https://user:pass@s.click.aliexpress.com/e/_AbCdEfG", id="password"),
    pytest.param("https://s.click.aliexpress.com:444/e/_AbCdEfG", id="port-444"),
    pytest.param("https://s.click.aliexpress.com:0/e/_AbCdEfG", id="port-zero"),
    pytest.param("http://s.click.aliexpress.com/e/_AbCdEfG", id="http"),
    pytest.param("ftp://s.click.aliexpress.com/e/_AbCdEfG", id="ftp"),
    pytest.param("javascript:alert(1)", id="javascript-scheme"),
    pytest.param("//s.click.aliexpress.com/e/_AbCdEfG", id="scheme-relative"),
    pytest.param("https://s.click.aliexpress.com.evil.example/e/_AbCdEfG", id="host-suffix"),
    pytest.param("https://evil-s.click.aliexpress.com/e/_AbCdEfG", id="host-prefix"),
    pytest.param("https://x.s.click.aliexpress.com/e/_AbCdEfG", id="subdomain"),
    pytest.param("https://s.click.aliexpress.com./e/_AbCdEfG", id="trailing-dot-host"),
    pytest.param("https://example.com/e/_AbCdEfG", id="foreign-host"),
    pytest.param(f"{HOST}/e/_AbCdEfG#fragment", id="fragment"),
    pytest.param(HOST, id="no-path"),
    pytest.param(f"{HOST}/", id="root-path-only"),
    pytest.param(f"{HOST}/e/_AbC%zz", id="malformed-percent-escape"),
    pytest.param(f"{HOST}/e/_AbC%", id="dangling-percent"),
    pytest.param(f"{HOST}/e/_" + "A" * 2100, id="too-long"),
]

GOOD_LINKS = [
    pytest.param(f"{HOST}/e/_AbCdEfG", id="short-link"),
    pytest.param(f"{HOST}/e/fixture-a_b.c~d", id="unreserved-characters"),
    pytest.param(f"{HOST}/e/_AbCdEfG?bz=1&af=a%20b+c&sub=x/y", id="affiliate-query-parameters"),
    pytest.param("https://S.CLICK.ALIEXPRESS.COM/e/_AbCdEfG", id="uppercase-host"),
    pytest.param(f"{HOST}:443/e/_AbCdEfG", id="explicit-https-port"),
    pytest.param(f"{HOST}/e/_AbCdEfG?note=a%0D%0Ab", id="percent-encoded-newline-is-inert"),
]


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=ENTRY_IDS)
@pytest.mark.parametrize("link", BAD_LINKS)
def test_unsafe_or_malformed_link_is_rejected_by_every_validator(
    entry: Callable[[object], str], link: str
) -> None:
    # Breaks if: any validator accepts text outside a strict URL (embedded newline, space,
    # control or bidi characters, extra components), which would reach the Telegram message.
    with pytest.raises(ProviderError) as captured:
        entry(link)

    assert "PROMOTION_LINK_INVALID" in captured.value.code


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=ENTRY_IDS)
@pytest.mark.parametrize("link", GOOD_LINKS)
def test_legitimate_link_is_accepted_and_returned_unchanged(
    entry: Callable[[object], str], link: str
) -> None:
    # Breaks if: validation becomes so strict that it rejects official links, or rewrites the
    # link (which would destroy legitimate affiliate query parameters).
    assert entry(link) == link


@pytest.mark.parametrize("entry", [canonical_parser, coin_parser], ids=ENTRY_IDS[:2])
def test_surrounding_whitespace_is_trimmed_but_never_stored(
    entry: Callable[[object], str],
) -> None:
    # Breaks if: padded API values start being rejected (behavior kept from before), or the
    # padding is kept in the stored link.
    assert entry(f"  {HOST}/e/_AbCdEfG\r\n") == f"{HOST}/e/_AbCdEfG"
