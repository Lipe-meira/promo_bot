"""Transport-neutral relay values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from promo_bot.domain.enums import LinkSource
from promo_bot.domain.models import ensure_utc


@dataclass(frozen=True, slots=True)
class ExtractedLink:
    url: str
    source: LinkSource
    ordinal: int

    def __post_init__(self) -> None:
        if not self.url.strip():
            raise ValueError("link URL cannot be empty")
        if self.ordinal < 0:
            raise ValueError("link ordinal must be non-negative")

    def as_dict(self) -> dict[str, str | int]:
        return {"url": self.url, "source": self.source.value, "ordinal": self.ordinal}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExtractedLink:
        return cls(
            url=str(value["url"]),
            source=LinkSource(str(value["source"])),
            ordinal=int(value["ordinal"]),
        )


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    platform: str
    message_id: int
    channel_id: str
    occurred_at: datetime
    original_text: str
    links: tuple[ExtractedLink, ...]

    def __post_init__(self) -> None:
        if self.message_id < 1:
            raise ValueError("Telegram message ID must be positive")
        if not self.channel_id:
            raise ValueError("channel ID cannot be empty")
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))

    @property
    def content_hash(self) -> str:
        payload = {
            "text": self.original_text,
            "links": [link.as_dict() for link in self.links],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PersistedMessage:
    internal_id: int
    created: bool
    completed_duplicate: bool
    queued: bool
    content_matches: bool


class RelayProcessingError(Exception):
    def __init__(self, code: str, *, retryable: bool, summary: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.summary = summary
