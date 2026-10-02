"""Strict occurrence contract for the opt-in multi-short shadow path."""

from __future__ import annotations

from dataclasses import dataclass

from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected
from promo_bot.domain.enums import LinkSource
from promo_bot.providers.aliexpress.coin_shadow import validate_coin_short
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.parser import TRAILING_PUNCTUATION, URL_PATTERN


@dataclass(frozen=True, slots=True, repr=False)
class CoinShortOccurrence:
    ordinal: int
    start: int
    end: int
    source_value: str


def collect_coin_occurrences(
    message: IncomingMessage, *, max_occurrences: int
) -> tuple[CoinShortOccurrence, ...]:
    """Validate the entire surface before any generation or operational claim."""
    if not 1 <= max_occurrences <= 3:
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_OCCURRENCE_LIMIT_INVALID")
    if message.platform != "telegram":
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SOURCE_PLATFORM_INVALID")
    if not message.surface_metadata.is_safe_plain_text:
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SURFACE_UNSAFE")
    matches = tuple(URL_PATTERN.finditer(message.original_text))
    if not 1 <= len(matches) <= max_occurrences:
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_VISIBLE_URL_COUNT_INVALID")
    occurrences: list[CoinShortOccurrence] = []
    for ordinal, match in enumerate(matches):
        literal = match.group(0).rstrip(TRAILING_PUNCTUATION)
        try:
            validate_coin_short(literal)
        except ValueError:
            raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_SHORT_INVALID") from None
        occurrences.append(
            CoinShortOccurrence(ordinal, match.start(), match.start() + len(literal), literal)
        )
    visible = {row.source_value for row in occurrences}
    if (
        any(link.source not in {LinkSource.TEXT, LinkSource.ENTITY_URL} for link in message.links)
        or {link.url for link in message.links} != visible
    ):
        raise CoinShadowPreviewRejected("ALIEXPRESS_COIN_URL_SURFACE_MISMATCH")
    return tuple(occurrences)
