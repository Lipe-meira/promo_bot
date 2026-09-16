from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE

NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
SOURCE = "https://s.click.aliexpress.com/e/_Ab12Cd3"
PROMOTION = "https://s.click.aliexpress.com/e/_Zy98Xw7"
TRACKING = "configured-tracking"
SECRET = "fixture-app-secret"


def success_payload() -> dict[str, object]:
    return {
        "code": "0",
        "aliexpress_affiliate_link_generate_response": {
            "resp_result": {
                "resp_code": "200",
                "result": {
                    "promotion_links": [{"source_value": SOURCE, "promotion_link": PROMOTION}],
                    "tracking_id": TRACKING,
                },
            }
        },
    }


class BlockingClient:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, operation: str, payload: dict[str, str]) -> dict[str, object]:
        assert operation == LINK_GENERATE
        assert payload == {
            "ship_to_country": "BR",
            "promotion_link_type": "0",
            "source_values": SOURCE,
            "tracking_id": TRACKING,
        }
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return success_payload()


async def make_database(tmp_path: Path, name: str):
    from promo_bot.database.models import Base

    database = create_affiliate_shadow_database(tmp_path / name)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def test_coin_shadow_fingerprints_are_fixed_and_domain_separated() -> None:
    from promo_bot.database.coin_shadow_repository import (
        CoinShadowFingerprintDomain,
        coin_shadow_fingerprint,
    )

    input_fingerprint = coin_shadow_fingerprint(SECRET, SOURCE, CoinShadowFingerprintDomain.INPUT)
    tracking_fingerprint = coin_shadow_fingerprint(
        SECRET, SOURCE, CoinShadowFingerprintDomain.TRACKING
    )

    assert len(input_fingerprint) == 64
    assert len(tracking_fingerprint) == 64
    assert input_fingerprint != tracking_fingerprint
    assert SOURCE not in input_fingerprint
    assert SECRET not in input_fingerprint


@pytest.mark.asyncio
async def test_expired_generating_becomes_uncertain_and_blocks_new_claim(tmp_path: Path) -> None:
    from promo_bot.database.coin_shadow_repository import (
        CoinShadowClaimDisposition,
        CoinShadowEvidenceRepository,
        CoinShadowEvidenceState,
        CoinShadowFingerprintDomain,
        coin_shadow_fingerprint,
    )

    database = await make_database(tmp_path, "stale.sqlite3")
    input_fp = coin_shadow_fingerprint(SECRET, SOURCE, CoinShadowFingerprintDomain.INPUT)
    tracking_fp = coin_shadow_fingerprint(SECRET, TRACKING, CoinShadowFingerprintDomain.TRACKING)
    async with database.session() as session:
        first = await CoinShadowEvidenceRepository(session).claim(
            input_fingerprint=input_fp,
            tracking_fingerprint=tracking_fp,
            promotion_link_type=0,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
        )
    assert first.disposition is CoinShadowClaimDisposition.GENERATE

    async with database.session() as session:
        second = await CoinShadowEvidenceRepository(session).claim(
            input_fingerprint=input_fp,
            tracking_fingerprint=tracking_fp,
            promotion_link_type=0,
            now=NOW + timedelta(minutes=6),
            lease_until=NOW + timedelta(minutes=11),
        )
    assert second.disposition is CoinShadowClaimDisposition.BLOCKED
    assert second.state is CoinShadowEvidenceState.UNCERTAIN
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_generation_observes_winner_after_it_becomes_ready(
    tmp_path: Path,
) -> None:
    from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
    from promo_bot.database.models import AliExpressCoinShadowEvidenceModel

    database = await make_database(tmp_path, "concurrent.sqlite3")
    client = BlockingClient()
    service = CoinShadowGenerationService(
        database,
        client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: NOW,
        observer_wait_seconds=2,
        observer_poll_seconds=0.01,
    )

    winner = asyncio.create_task(service.generate(SOURCE))
    await client.started.wait()
    follower = asyncio.create_task(service.generate(SOURCE))
    await asyncio.sleep(0.05)
    assert client.calls == 1
    assert not follower.done()

    client.release.set()
    first, second = await asyncio.gather(winner, follower)

    assert first.state == "READY"
    assert second.state == "READY"
    assert {first.cache_hit, second.cache_hit} == {False, True}
    assert client.calls == 1
    async with database.session() as session:
        count = await session.scalar(select(func.count(AliExpressCoinShadowEvidenceModel.id)))
    assert count == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_generation_after_expired_ready_creates_one_new_evidence(
    tmp_path: Path,
) -> None:
    from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
    from promo_bot.database.models import AliExpressCoinShadowEvidenceModel

    database = await make_database(tmp_path, "purge-race.sqlite3")
    initial_client = BlockingClient()
    initial_client.release.set()
    initial = CoinShadowGenerationService(
        database,
        initial_client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: NOW,
    )
    assert (await initial.generate(SOURCE)).state == "READY"

    later = NOW + timedelta(hours=25)
    client = BlockingClient()
    service = CoinShadowGenerationService(
        database,
        client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: later,
        observer_wait_seconds=2,
        observer_poll_seconds=0.01,
    )
    first_task = asyncio.create_task(service.generate(SOURCE))
    await client.started.wait()
    second_task = asyncio.create_task(service.generate(SOURCE))
    await asyncio.sleep(0.05)
    assert client.calls == 1

    client.release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first.state == second.state == "READY"
    assert client.calls == 1
    async with database.session() as session:
        rows = (await session.execute(select(AliExpressCoinShadowEvidenceModel))).scalars().all()
    assert len(rows) == 1
    assert rows[0].created_at == later
    await database.dispose()


@pytest.mark.asyncio
async def test_review_and_uncertain_results_are_never_regenerated(tmp_path: Path) -> None:
    from promo_bot.database.coin_shadow_repository import (
        CoinShadowClaimDisposition,
        CoinShadowEvidenceRepository,
        CoinShadowFingerprintDomain,
        coin_shadow_fingerprint,
    )

    database = await make_database(tmp_path, "terminal.sqlite3")
    input_fp = coin_shadow_fingerprint(SECRET, SOURCE, CoinShadowFingerprintDomain.INPUT)
    tracking_fp = coin_shadow_fingerprint(SECRET, TRACKING, CoinShadowFingerprintDomain.TRACKING)
    async with database.session() as session:
        repository = CoinShadowEvidenceRepository(session)
        claimed = await repository.claim(
            input_fingerprint=input_fp,
            tracking_fingerprint=tracking_fp,
            promotion_link_type=0,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
        )
        await repository.finish_review_required(
            claimed.evidence_id,
            claimed.lease_token,
            now=NOW,
            error_code="ALIEXPRESS_COIN_FIXTURE_REJECTED",
        )

    async with database.session() as session:
        blocked = await CoinShadowEvidenceRepository(session).claim(
            input_fingerprint=input_fp,
            tracking_fingerprint=tracking_fp,
            promotion_link_type=0,
            now=NOW + timedelta(days=30),
            lease_until=NOW + timedelta(days=30, minutes=5),
        )
    assert blocked.disposition is CoinShadowClaimDisposition.BLOCKED
    await database.dispose()


@pytest.mark.asyncio
async def test_ready_purge_cascades_preview_but_preserves_delivery_reservation(
    tmp_path: Path,
) -> None:
    from promo_bot.database.coin_shadow_repository import (
        CoinShadowEvidenceRepository,
        CoinShadowFingerprintDomain,
        coin_shadow_fingerprint,
    )
    from promo_bot.database.models import (
        AliExpressCoinShadowDeliveryModel,
        AliExpressCoinShadowPreviewModel,
    )
    from promo_bot.providers.aliexpress.coin_shadow import CoinShadowCorrelationMode

    database = await make_database(tmp_path, "purge-fks.sqlite3")
    input_fp = coin_shadow_fingerprint(SECRET, SOURCE, CoinShadowFingerprintDomain.INPUT)
    tracking_fp = coin_shadow_fingerprint(SECRET, TRACKING, CoinShadowFingerprintDomain.TRACKING)
    async with database.session() as session:
        repository = CoinShadowEvidenceRepository(session)
        claimed = await repository.claim(
            input_fingerprint=input_fp,
            tracking_fingerprint=tracking_fp,
            promotion_link_type=0,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
        )
        await repository.finish_ready(
            claimed.evidence_id,
            claimed.lease_token,
            now=NOW,
            expires_at=NOW + timedelta(hours=24),
            promotion_link=PROMOTION,
            affiliate_host="s.click.aliexpress.com",
            correlation_mode=CoinShadowCorrelationMode.SOURCE_VALUE_EXACT,
        )
        preview = AliExpressCoinShadowPreviewModel(
            evidence_id=claimed.evidence_id,
            evidence_state="READY",
            source_message_fingerprint="c" * 64,
            rendered_text=f"Oferta {PROMOTION}",
            content_expires_at=NOW + timedelta(hours=24),
        )
        session.add(preview)
        await session.flush()
        delivery = AliExpressCoinShadowDeliveryModel(
            preview_id=preview.id,
            source_message_fingerprint="c" * 64,
            destination_fingerprint="d" * 64,
            state="sent",
            attempt_count=1,
            started_at=NOW,
            finished_at=NOW,
        )
        session.add(delivery)
        await session.flush()
        preview_id, delivery_id = preview.id, delivery.id

    async with database.session() as session:
        await CoinShadowEvidenceRepository(session).claim(
            input_fingerprint=input_fp,
            tracking_fingerprint=tracking_fp,
            promotion_link_type=0,
            now=NOW + timedelta(hours=25),
            lease_until=NOW + timedelta(hours=25, minutes=5),
        )

    async with database.session() as session:
        preview = await session.get(AliExpressCoinShadowPreviewModel, preview_id)
        delivery = await session.get(AliExpressCoinShadowDeliveryModel, delivery_id)
    assert preview is None
    assert delivery is not None
    assert delivery.preview_id is None
    await database.dispose()
