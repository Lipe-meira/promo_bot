from __future__ import annotations

from typing import ClassVar

import pytest
from telegram.error import BadRequest, TimedOut

from promo_bot.config.settings import EnvironmentSettings
from promo_bot.delivery.service import AmbiguousDelivery
from promo_bot.domain.enums import RelayLinkState
from promo_bot.relay.formatter import (
    SYNTHETIC_TEST_URL,
    render_candidate_dry_run,
    render_synthetic_test,
)
from promo_bot.relay.models import RelayProcessingError
from promo_bot.telegram import bot as bot_module
from promo_bot.telegram.bot import SyntheticBotSender, TelegramDealTransport


class FakeBot:
    outcomes: ClassVar[list[Exception | None]] = []
    sends: ClassVar[int] = 0

    def __init__(self, token: str) -> None:
        assert token == "fixture-token"

    async def __aenter__(self) -> FakeBot:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def send_message(self, **kwargs: object) -> None:
        del kwargs
        type(self).sends += 1
        outcome = type(self).outcomes.pop(0)
        if outcome is not None:
            raise outcome


def settings() -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        telegram_bot_token="fixture-token",
        telegram_target_chat_id="123",
    )


def test_templates_do_not_contain_source_channel_advertising() -> None:
    synthetic = render_synthetic_test()
    candidate = render_candidate_dry_run(
        store="amazon",
        external_product_id="B0ABCDEFGH",
        canonical_url="https://www.amazon.com.br/dp/B0ABCDEFGH",
        state=RelayLinkState.PENDING_AFFILIATE,
    )

    assert synthetic.button_label == "Abrir oferta"
    assert synthetic.button_url == SYNTHETIC_TEST_URL
    assert "sintética" in synthetic.text
    assert "canal" not in candidate.text.casefold()
    assert "afiliado" in candidate.text.casefold()


def test_ready_candidate_cannot_be_rendered_by_phase_two_template() -> None:
    with pytest.raises(ValueError):
        render_candidate_dry_run(
            store="amazon",
            external_product_id="B0ABCDEFGH",
            canonical_url="https://www.amazon.com.br/dp/B0ABCDEFGH",
            state=RelayLinkState.REJECTED,
        )


@pytest.mark.asyncio
async def test_synthetic_bot_send_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBot.outcomes = [TimedOut("timeout"), None]
    FakeBot.sends = 0
    monkeypatch.setattr(bot_module, "Bot", FakeBot)

    async def no_sleep(delay: float) -> None:
        assert delay >= 0

    monkeypatch.setattr(bot_module.asyncio, "sleep", no_sleep)

    await SyntheticBotSender(settings()).send_test()

    assert FakeBot.sends == 2


@pytest.mark.asyncio
async def test_synthetic_bot_does_not_retry_permanent_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBot.outcomes = [BadRequest("bad request")]
    FakeBot.sends = 0
    monkeypatch.setattr(bot_module, "Bot", FakeBot)

    with pytest.raises(RelayProcessingError, match="BOT_PERMANENT_REJECTION"):
        await SyntheticBotSender(settings()).send_test()

    assert FakeBot.sends == 1


@pytest.mark.asyncio
async def test_real_deal_transport_classifies_timeout_as_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBot.outcomes = [TimedOut("timeout")]
    FakeBot.sends = 0
    monkeypatch.setattr(bot_module, "Bot", FakeBot)

    with pytest.raises(AmbiguousDelivery, match="TELEGRAM_DELIVERY_AMBIGUOUS"):
        await TelegramDealTransport(settings()).send(render_synthetic_test())

    assert FakeBot.sends == 1
