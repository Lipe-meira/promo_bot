from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageEntityUrl

from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected
from promo_bot.domain.enums import LinkSource
from promo_bot.relay.models import ExtractedLink, IncomingMessage, MessageSurfaceMetadata
from promo_bot.relay.parser import extract_links
from promo_bot.telegram.monitor import _adapt_message

A = "https://a.aliexpress.com/_Ab12Cd3"
B = "https://s.click.aliexpress.com/e/_Ef45Gh67"
NOW = datetime(2026, 10, 2, tzinfo=UTC)


def incoming(text, *, links=None, metadata=None):
    return IncomingMessage(
        platform="telegram",
        message_id=1,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=text,
        links=extract_links(text) if links is None else links,
        surface_metadata=metadata or MessageSurfaceMetadata(),
    )


@pytest.mark.parametrize(
    "text,count,distinct", [(A, 1, 1), (f"{A}\n{A}", 2, 1), (f"{A}\n{B}\n{A}", 3, 2)]
)
def test_occurrences_are_not_deduplicated_and_have_exact_python_spans(text, count, distinct):
    from promo_bot.affiliate.coin_shadow_multi import collect_coin_occurrences

    text = "🔥 APP: " + text + ". Cupom XYZ\r\nR$ 10"
    rows = collect_coin_occurrences(incoming(text), max_occurrences=3)
    assert len(rows) == count
    assert len({row.source_value for row in rows}) == distinct
    assert [row.ordinal for row in rows] == list(range(count))
    for row in rows:
        assert text[row.start : row.end] == row.source_value
    assert rows[0].start == 7  # emoji occupies one Python code point


@pytest.mark.parametrize(
    "text,limit",
    [
        (f"{A}\n{B}", 1),
        ("\n".join([A] * 4), 3),
        (f"{A}\nhttps://example.com/x", 3),
        (f"{A}\nhttps://pt.aliexpress.com/item/123.html", 3),
        (A + "?q=1", 3),
        (A + "/", 3),
    ],
)
def test_ineligible_message_fails_before_generation(text, limit):
    from promo_bot.affiliate.coin_shadow_multi import collect_coin_occurrences

    with pytest.raises(CoinShadowPreviewRejected):
        collect_coin_occurrences(incoming(text), max_occurrences=limit)


def test_unaccounted_entity_url_and_hidden_surface_are_rejected():
    from promo_bot.affiliate.coin_shadow_multi import collect_coin_occurrences

    for message in (
        incoming(
            A,
            links=(
                ExtractedLink(A, LinkSource.TEXT, 0),
                ExtractedLink(B, LinkSource.ENTITY_URL, 1),
            ),
        ),
        incoming(A, metadata=MessageSurfaceMetadata(has_hidden_links=True)),
        incoming(A, links=(ExtractedLink(A, LinkSource.ENTITY_TEXT_URL, 0),)),
    ):
        with pytest.raises(CoinShadowPreviewRejected):
            collect_coin_occurrences(message, max_occurrences=3)


@pytest.mark.parametrize("offset_delta,length_delta", [(0, 0), (-1, 0), (0, -1), (0, 1), (-6, 0)])
def test_url_entities_validate_utf16_ranges_before_marking_surface_safe(offset_delta, length_delta):
    text = "🔥 APP " + A
    offset = len("🔥 APP ".encode("utf-16-le")) // 2 + offset_delta
    entity = MessageEntityUrl(offset=offset, length=len(A) + length_delta)
    message = SimpleNamespace(
        raw_text=text,
        media=None,
        buttons=None,
        id=1,
        date=NOW,
        get_entities_text=lambda: [(entity, A)],
    )
    adapted = _adapt_message(message, "-1001234567890")
    assert adapted.surface_metadata.is_safe_plain_text is (offset_delta == length_delta == 0)


@pytest.mark.parametrize(
    "include,limit,coin_gate,code",
    [
        (False, 2, True, "ALIEXPRESS_COIN_MULTI_REQUIRES_COIN_PATH"),
        (True, 4, True, "ALIEXPRESS_SHADOW_AUTO_LINK_LIMIT_INVALID"),
        (True, 2, False, "ALIEXPRESS_COIN_AUTO_PILOT_DISABLED"),
    ],
)
def test_multi_flag_policy_fails_before_transport(
    tmp_path, monkeypatch, capsys, include, limit, coin_gate, code
):
    from promo_bot.cli import main
    from tests.unit.test_coin_listener_pilot import _argv, _config_file, _settings

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=coin_gate))
    monkeypatch.setattr(
        "promo_bot.cli.run_aliexpress_shadow_auto_delivery",
        lambda *_a, **_k: pytest.fail("transport behind invalid flags"),
    )
    argv = _argv(_config_file(tmp_path), tmp_path / "pilot.sqlite3")
    if not include:
        argv.remove("--include-coin-shorts")
    argv[argv.index("--max-links-per-message") + 1] = str(limit)
    assert main([*argv, "--allow-multiple-coin-shorts"]) == 2
    assert code in capsys.readouterr().err
