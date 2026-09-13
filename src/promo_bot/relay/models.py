"""Transport-neutral relay values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from promo_bot.domain.enums import LinkSource
from promo_bot.domain.models import ensure_utc


@dataclass(frozen=True, slots=True)
class MessageSurfaceMetadata:
    """Content-free facts used to decide whether plain-text delivery is safe."""

    has_buttons: bool = False
    has_caption: bool = False
    has_custom_emoji: bool = False
    has_hidden_links: bool = False
    has_media: bool = False
    flattened_entity_types: tuple[str, ...] = ()
    unsupported_entity_types: tuple[str, ...] = ()

    @property
    def is_safe_plain_text(self) -> bool:
        return not (
            self.has_buttons
            or self.has_caption
            or self.has_custom_emoji
            or self.has_hidden_links
            or self.has_media
            or self.unsupported_entity_types
        )

    def as_dict(self) -> dict[str, bool | list[str]]:
        return {
            "has_buttons": self.has_buttons,
            "has_caption": self.has_caption,
            "has_custom_emoji": self.has_custom_emoji,
            "has_hidden_links": self.has_hidden_links,
            "has_media": self.has_media,
            "flattened_entity_types": list(self.flattened_entity_types),
            "unsupported_entity_types": list(self.unsupported_entity_types),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MessageSurfaceMetadata:
        return cls(
            has_buttons=bool(value.get("has_buttons", False)),
            has_caption=bool(value.get("has_caption", False)),
            has_custom_emoji=bool(value.get("has_custom_emoji", False)),
            has_hidden_links=bool(value.get("has_hidden_links", False)),
            has_media=bool(value.get("has_media", False)),
            flattened_entity_types=tuple(
                str(item) for item in value.get("flattened_entity_types", [])
            ),
            unsupported_entity_types=tuple(
                str(item) for item in value.get("unsupported_entity_types", [])
            ),
        )


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
    surface_metadata: MessageSurfaceMetadata = field(default_factory=MessageSurfaceMetadata)

    def __post_init__(self) -> None:
        if self.message_id < 1:
            raise ValueError("Telegram message ID must be positive")
        if not self.channel_id:
            raise ValueError("channel ID cannot be empty")
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))

    @property
    def legacy_content_hash(self) -> str:
        payload = {
            "text": self.original_text,
            "links": [link.as_dict() for link in self.links],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def content_hash(self) -> str:
        payload = {
            "text": self.original_text,
            "links": [link.as_dict() for link in self.links],
            "surface_metadata": self.surface_metadata.as_dict(),
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
    legacy_compatible: bool = False
    legacy_rejection_code: str | None = None


class RelayProcessingError(Exception):
    def __init__(self, code: str, *, retryable: bool, summary: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.summary = summary
