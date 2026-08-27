"""Offline parser for links present in Telegram message surfaces."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from promo_bot.domain.enums import LinkSource
from promo_bot.relay.models import ExtractedLink

URL_PATTERN = re.compile(r"https?://[^\s<>\[\]{}\"']+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?)]}"


@dataclass(frozen=True, slots=True)
class EntityUrl:
    url: str
    source: LinkSource
    offset: int = 0


def extract_links(
    text: str,
    *,
    entity_urls: Iterable[EntityUrl] = (),
    button_urls: Iterable[str] = (),
) -> tuple[ExtractedLink, ...]:
    """Return unique HTTP URLs in stable message order."""

    candidates: list[tuple[int, int, str, LinkSource]] = []
    sequence = 0
    for match in URL_PATTERN.finditer(text):
        url = _trim_url(match.group(0))
        if url:
            candidates.append((match.start(), sequence, url, LinkSource.TEXT))
            sequence += 1
    for entity in entity_urls:
        url = _trim_url(entity.url.strip())
        if url:
            candidates.append((min(max(entity.offset, 0), len(text)), sequence, url, entity.source))
            sequence += 1
    button_base = len(text) + 1
    for button_url in button_urls:
        url = _trim_url(button_url.strip())
        if url:
            candidates.append((button_base, sequence, url, LinkSource.BUTTON))
            button_base += 1
            sequence += 1

    seen: set[str] = set()
    extracted: list[ExtractedLink] = []
    for _, _, url, source in sorted(candidates, key=lambda item: (item[0], item[1])):
        normalized_key = url.casefold()
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        extracted.append(ExtractedLink(url=url, source=source, ordinal=len(extracted)))
    return tuple(extracted)


def _trim_url(url: str) -> str:
    while url and url[-1] in TRAILING_PUNCTUATION:
        url = url[:-1]
    return url
