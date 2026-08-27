from datetime import UTC, datetime, timedelta

from promo_bot.relay.catchup import select_catch_up_messages
from promo_bot.relay.models import IncomingMessage


def message(message_id: int, occurred_at: datetime) -> IncomingMessage:
    return IncomingMessage("telegram", message_id, "channel", occurred_at, "", ())


def test_catch_up_is_bounded_checkpointed_and_oldest_first() -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=UTC)
    messages = [
        message(5, now - timedelta(minutes=5)),
        message(2, now - timedelta(hours=8)),
        message(4, now - timedelta(minutes=10)),
        message(3, now - timedelta(minutes=15)),
    ]

    selected = select_catch_up_messages(
        messages,
        now=now,
        lookback_hours=6,
        max_messages=2,
        checkpoint_id=2,
    )

    assert [item.message_id for item in selected] == [4, 5]
