"""T3 / F-02: deliveries stuck in SENDING become DELIVERY_AMBIGUOUS, never PENDING or SENT.

A SENDING row whose lease has expired may or may not have reached Telegram (crash, cancellation,
an unexpected transport error, a lost final write). It must stop being claimable, must not be
declared sent, and must stay distinguishable from a definitive failure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from promo_bot.database.delivery_repository import (
    DeliveryRepository,
    DeliveryTransitionConflict,
)
from promo_bot.database.models import DeliveryModel
from promo_bot.delivery.service import DealDeliveryService
from promo_bot.domain.enums import DeliveryState
from promo_bot.relay.formatter import RenderedMessage
from tests.unit.test_delivery_outbox import (
    NOW,
    SuccessfulTransport,
    database_with_delivery,
    enabled_settings,
    publication_context,
    rendered,
)

LEASE = timedelta(minutes=5)
STALE_CODE = "DELIVERY_LEASE_EXPIRED_AMBIGUOUS"


class ExplodingTransport:
    """Fails with an error the service does not classify (no confirmation either way)."""

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, rendered: RenderedMessage) -> str:
        del rendered
        self.calls += 1
        raise RuntimeError("connection reset after the request was written")


def service_at(database, transport, moment: datetime) -> DealDeliveryService:
    return DealDeliveryService(
        database,
        transport,
        enabled_settings(),
        publication_context(),
        lease_minutes=5,
        clock=lambda: moment,
    )


async def claim(database, delivery_id: int, *, now: datetime = NOW) -> None:
    async with database.session() as session:
        claimed = await DeliveryRepository(session).claim(
            delivery_id, now=now, lease_until=now + LEASE, max_attempts=3
        )
        assert claimed is not None


async def snapshot(database, delivery_id: int) -> dict[str, object]:
    async with database.session() as session:
        row = await session.get(DeliveryModel, delivery_id)
        assert row is not None
        return {
            column: getattr(row, column)
            for column in (
                "state",
                "attempt_count",
                "last_attempt_at",
                "next_attempt_at",
                "lease_until",
                "telegram_message_id",
                "sent_at",
                "error_code",
            )
        }


@pytest.mark.asyncio
async def test_interrupted_send_is_made_ambiguous_on_the_next_service_use(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "interrupted.sqlite3")
    await claim(database, delivery_id)  # the process died right after claiming
    transport = SuccessfulTransport()
    later = NOW + LEASE + timedelta(minutes=1)

    assert not await service_at(database, transport, later).send(delivery_id, rendered())
    first = await snapshot(database, delivery_id)
    assert not await service_at(database, transport, later).send(delivery_id, rendered())

    assert transport.calls == 0
    assert first["state"] == DeliveryState.DELIVERY_AMBIGUOUS.value
    assert first["error_code"] == STALE_CODE
    assert first["telegram_message_id"] is None and first["sent_at"] is None
    assert first["lease_until"] is None and first["next_attempt_at"] is None
    # The attempt that may have reached Telegram stays on record.
    assert first["attempt_count"] == 1 and first["last_attempt_at"] == NOW
    assert await snapshot(database, delivery_id) == first
    await database.dispose()


@pytest.mark.asyncio
async def test_unclassified_transport_failure_blocks_resend_after_lease_expiry(
    tmp_path: Path,
) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "unclassified.sqlite3")
    transport = ExplodingTransport()

    with pytest.raises(RuntimeError):
        await service_at(database, transport, NOW).send(delivery_id, rendered())
    assert (await snapshot(database, delivery_id))["state"] == DeliveryState.SENDING.value

    later = NOW + LEASE + timedelta(seconds=1)
    assert not await service_at(database, transport, later).send(delivery_id, rendered())

    assert transport.calls == 1
    after = await snapshot(database, delivery_id)
    assert after["state"] == DeliveryState.DELIVERY_AMBIGUOUS.value
    assert after["error_code"] == STALE_CODE
    assert after["attempt_count"] == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_lost_final_write_after_telegram_accepted_is_ambiguous_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "lost-write.sqlite3")
    transport = SuccessfulTransport()

    async def broken_mark_sent(self, internal_id, *, message_id, sent_at):
        del self, internal_id, message_id, sent_at
        raise RuntimeError("database became unavailable")

    monkeypatch.setattr(DeliveryRepository, "mark_sent", broken_mark_sent)
    with pytest.raises(RuntimeError):
        await service_at(database, transport, NOW).send(delivery_id, rendered())
    monkeypatch.undo()

    later = NOW + LEASE + timedelta(minutes=1)
    assert not await service_at(database, transport, later).send(delivery_id, rendered())

    assert transport.calls == 1
    after = await snapshot(database, delivery_id)
    assert after["state"] == DeliveryState.DELIVERY_AMBIGUOUS.value
    assert after["telegram_message_id"] is None and after["sent_at"] is None
    await database.dispose()


@pytest.mark.asyncio
async def test_lease_boundary_and_live_sender_are_respected(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "boundary.sqlite3")
    await claim(database, delivery_id)
    transport = SuccessfulTransport()

    for moment in (NOW, NOW + LEASE - timedelta(microseconds=1)):
        assert await service_at(database, transport, moment).recover_stale_sending() == 0
        assert (await snapshot(database, delivery_id))["state"] == DeliveryState.SENDING.value

    # `_finish` needs lease_until > now, so lease_until <= now is exactly the stale set.
    assert await service_at(database, transport, NOW + LEASE).recover_stale_sending() == 1
    assert (await snapshot(database, delivery_id))["state"] == (
        DeliveryState.DELIVERY_AMBIGUOUS.value
    )
    assert transport.calls == 0
    await database.dispose()


@pytest.mark.parametrize(
    "state",
    [
        DeliveryState.PENDING,
        DeliveryState.FAILED_RETRYABLE,
        DeliveryState.SENT,
        DeliveryState.FAILED_PERMANENT,
        DeliveryState.DELIVERY_AMBIGUOUS,
        DeliveryState.MANUAL_REVIEW,
    ],
)
@pytest.mark.asyncio
async def test_recovery_never_touches_rows_that_are_not_sending(
    tmp_path: Path, state: DeliveryState
) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, f"other-{state.value}.sqlite3")
    async with database.session() as session:
        await session.execute(
            update(DeliveryModel)
            .where(DeliveryModel.id == delivery_id)
            .values(
                state=state.value,
                attempt_count=1,
                lease_until=NOW,  # an old lease value must not make the row "stale"
                telegram_message_id="321" if state is DeliveryState.SENT else None,
                sent_at=NOW if state is DeliveryState.SENT else None,
                error_code="ORIGINAL_CODE" if state is not DeliveryState.SENT else None,
            )
        )
    before = await snapshot(database, delivery_id)

    count = await service_at(
        database, SuccessfulTransport(), NOW + timedelta(days=1)
    ).recover_stale_sending()

    assert count == 0
    assert await snapshot(database, delivery_id) == before
    await database.dispose()


@pytest.mark.asyncio
async def test_recovery_is_idempotent_and_safe_under_concurrency(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "concurrent.sqlite3")
    await claim(database, delivery_id)
    later = NOW + LEASE + timedelta(minutes=1)
    transport = SuccessfulTransport()

    counts = await asyncio.gather(
        *(service_at(database, transport, later).recover_stale_sending() for _ in range(6))
    )
    again = await service_at(database, transport, later).recover_stale_sending()

    assert sum(counts) == 1 and again == 0
    after = await snapshot(database, delivery_id)
    assert after["state"] == DeliveryState.DELIVERY_AMBIGUOUS.value
    assert after["attempt_count"] == 1
    assert transport.calls == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_sender_that_lost_its_lease_cannot_overwrite_the_ambiguous_state(
    tmp_path: Path,
) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "lost-lease.sqlite3")
    await claim(database, delivery_id)
    later = NOW + LEASE + timedelta(seconds=1)
    assert await service_at(database, SuccessfulTransport(), later).recover_stale_sending() == 1

    async with database.session() as session:
        with pytest.raises(DeliveryTransitionConflict):
            await DeliveryRepository(session).mark_sent(
                delivery_id, message_id="999", sent_at=later
            )

    after = await snapshot(database, delivery_id)
    assert after["state"] == DeliveryState.DELIVERY_AMBIGUOUS.value
    assert after["telegram_message_id"] is None and after["sent_at"] is None
    await database.dispose()


@pytest.mark.asyncio
async def test_confirmed_sent_delivery_is_not_altered_by_recovery(tmp_path: Path) -> None:
    database, delivery_id = await database_with_delivery(tmp_path, "confirmed.sqlite3")
    transport = SuccessfulTransport()
    assert await service_at(database, transport, NOW).send(delivery_id, rendered())
    before = await snapshot(database, delivery_id)

    later = NOW + timedelta(days=1)
    assert not await service_at(database, transport, later).send(delivery_id, rendered())

    assert transport.calls == 1
    assert await snapshot(database, delivery_id) == before
    assert before["state"] == DeliveryState.SENT.value and before["telegram_message_id"] == "321"
    await database.dispose()
