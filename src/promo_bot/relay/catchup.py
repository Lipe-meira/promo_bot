"""Pure bounded catch-up selection used by the Telethon adapter."""

from __future__ import annotations

from datetime import datetime, timedelta

from promo_bot.relay.models import IncomingMessage


def select_catch_up_messages(
    messages: list[IncomingMessage],
    *,
    now: datetime,
    lookback_hours: int,
    max_messages: int,
    checkpoint_id: int | None,
) -> list[IncomingMessage]:
    cutoff = now - timedelta(hours=lookback_hours)
    eligible = [
        message
        for message in messages
        if message.occurred_at >= cutoff
        and (checkpoint_id is None or message.message_id > checkpoint_id)
    ]
    return sorted(eligible, key=lambda item: item.message_id)[-max_messages:]
