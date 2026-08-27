from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from telethon.tl.types import MessageEntityTextUrl  # type: ignore[import-untyped]

from promo_bot.domain.enums import LinkSource
from promo_bot.telegram.monitor import (
    _adapt_message,
    _channel_reference,
    _ensure_external_session_path,
)


def test_session_path_inside_workspace_is_rejected() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    with pytest.raises(ValueError, match="outside the Git workspace"):
        _ensure_external_session_path(repository_root / "runtime" / "monitor.session")


def test_external_session_parent_is_created(tmp_path: Path) -> None:
    path = tmp_path / "telegram" / "monitor.session"

    _ensure_external_session_path(path)

    assert path.parent.is_dir()


def test_numeric_channel_ids_are_passed_to_telethon_as_integers() -> None:
    assert _channel_reference("-100123456") == -100123456
    assert _channel_reference("@configured_channel") == "@configured_channel"


def test_telethon_adapter_reads_entities_and_buttons_without_clicking() -> None:
    class Button:
        url = "https://www.kabum.com.br/produto/123"

    class Message:
        id = 10
        date = datetime(2026, 8, 27, 12, tzinfo=UTC)
        raw_text = "oferta"
        buttons: ClassVar[list[list[Button]]] = [[Button()]]

        @staticmethod
        def get_entities_text() -> list[tuple[object, str]]:
            entity = MessageEntityTextUrl(
                offset=0,
                length=6,
                url="https://www.amazon.com.br/dp/B0ABCDEFGH",
            )
            return [(entity, "oferta")]

    adapted = _adapt_message(Message(), "channel")  # type: ignore[arg-type]

    assert [link.source for link in adapted.links] == [
        LinkSource.ENTITY_TEXT_URL,
        LinkSource.BUTTON,
    ]
