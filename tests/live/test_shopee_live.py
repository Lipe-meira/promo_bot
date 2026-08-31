"""Reserved live boundary; intentionally blocked until the official contract is confirmed."""

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skip(reason="Shopee authenticated official contract is not confirmed"),
]


def test_known_product_and_short_link_only() -> None:
    """Will query one known product and shortlink; it must never publish to Telegram."""
    raise AssertionError("live Shopee client is unavailable until the official contract gate opens")
