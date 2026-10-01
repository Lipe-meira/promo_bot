from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
from promo_bot.database.history_models import AffiliateLinkGenerationModel
from promo_bot.database.models import AliExpressCoinShadowEvidenceModel, Base
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport

NOW = datetime(2026, 10, 1, 13, tzinfo=UTC)
SHORT = "https://a.aliexpress.com/_Ab12Cd34"
GENERATED = "https://s.click.aliexpress.com/e/new-synthetic"


class SyntheticGateway:
    def __init__(self, database, *, fail=False):
        self.database, self.fail, self.calls = database, fail, 0

    async def execute(self, operation, payload):
        assert operation == LINK_GENERATE
        assert payload["source_values"] == SHORT
        self.calls += 1
        async with self.database.session() as session:
            durable = await session.scalar(select(AffiliateLinkGenerationModel))
            assert durable is not None, "durable call intent missing before simulated network"
            assert durable.state == "CALL_STARTED"
        if self.fail:
            raise TimeoutError()
        return {
            "aliexpress_affiliate_link_generate_response": {
                "resp_result": {
                    "resp_code": "200",
                    "result": {
                        "tracking_id": "fixture-tracking",
                        "promotion_links": [
                            {"promotion_link": GENERATED},
                        ],
                    },
                }
            }
        }


async def make_database(tmp_path):
    database = create_affiliate_shadow_database(tmp_path / "generation.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def test_coin_generation_commits_started_before_network_and_cache_references_original(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path)
    gateway = SyntheticGateway(database)
    service = CoinShadowGenerationService(
        database,
        gateway,
        app_secret="fixture-secret",
        tracking_id="fixture-tracking",
        clock=lambda: NOW,
    )
    try:
        first = await service.generate(SHORT)
        assert first.state == "READY", first.error_code
        second = await service.generate(SHORT)
        assert second.cache_hit is True
        assert gateway.calls == 1
        async with database.session() as session:
            generations = list(await session.scalars(select(AffiliateLinkGenerationModel)))
            assert len(generations) == 1
            generation = generations[0]
            assert generation.state == "CONFIRMED"
            assert generation.generated_at == NOW
            assert generation.generated_url == GENERATED
            assert generation.tracking_confirmed and generation.attribution_unverified
            evidence = await session.get(AliExpressCoinShadowEvidenceModel, first.evidence_id)
            assert evidence.generation_id == generation.id
    finally:
        await database.dispose()


async def test_coin_timeout_blocks_restart_and_tracking_rotation(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    gateway = SyntheticGateway(database, fail=True)
    try:
        first = await CoinShadowGenerationService(
            database,
            gateway,
            app_secret="fixture-secret",
            tracking_id="fixture-tracking",
            clock=lambda: NOW,
        ).generate(SHORT)
        assert first.state == "UNCERTAIN"
        restarted = CoinShadowGenerationService(
            database,
            gateway,
            app_secret="fixture-secret",
            tracking_id="changed-tracking",
            clock=lambda: NOW + timedelta(days=2),
        )
        with pytest.raises(ValueError, match="AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED"):
            await restarted.generate(SHORT)
        assert gateway.calls == 1
    finally:
        await database.dispose()


async def test_real_client_rejects_unaudited_generation_before_transport() -> None:
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: calls.append(request.method) or httpx.Response(200, json={})
        )
    ) as http:
        client = AliExpressAffiliateApiClient(
            AliExpressHttpTransport(http, max_attempts=1),
            request_builder=AliExpressTopRequestBuilder("fixture", "fixture"),
            live_enabled=True,
        )
        with pytest.raises(ValueError, match="AFFILIATE_HISTORY_CONTEXT_REQUIRED"):
            await client.execute(LINK_GENERATE, {"source_values": SHORT, "tracking_id": "fixture"})
        assert calls == []


async def test_crash_after_start_recovers_durably_and_never_repeats_call(tmp_path: Path) -> None:
    class ProcessCrash(BaseException):
        pass

    class CrashingGateway(SyntheticGateway):
        async def execute(self, operation, payload):
            await super().execute(operation, payload)
            raise ProcessCrash()

    database = await make_database(tmp_path)
    gateway = CrashingGateway(database)
    try:
        first = CoinShadowGenerationService(
            database,
            gateway,
            app_secret="fixture-secret",
            tracking_id="fixture-tracking",
            clock=lambda: NOW,
        )
        with pytest.raises(ProcessCrash):
            await first.generate(SHORT)
        restarted = CoinShadowGenerationService(
            database,
            gateway,
            app_secret="fixture-secret",
            tracking_id="fixture-tracking",
            clock=lambda: NOW + timedelta(minutes=6),
        )
        with pytest.raises(ValueError, match="AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED"):
            await restarted.generate(SHORT)
        async with database.session() as session:
            generation = await session.scalar(select(AffiliateLinkGenerationModel))
            assert generation.state == "UNCERTAIN"
            evidence = await session.scalar(select(AliExpressCoinShadowEvidenceModel))
            assert evidence.state == "UNCERTAIN"
        assert gateway.calls == 1
    finally:
        await database.dispose()


async def test_candidate_repository_cannot_reclaim_an_expired_started_call(tmp_path: Path) -> None:
    from promo_bot.database.models import AffiliateCandidateModel
    from promo_bot.database.repositories import AffiliateCandidateRepository
    from tests.unit.test_aliexpress_conversion import (
        CANONICAL,
        conversion_service,
        persist_and_process,
    )
    from tests.unit.test_aliexpress_conversion import (
        make_database as canonical_database,
    )

    class ProcessCrash(BaseException):
        pass

    def crash(request):
        raise ProcessCrash()

    database = await canonical_database(tmp_path, "canonical.sqlite3")
    message_id = await persist_and_process(database, 1, CANONICAL)
    service, http = conversion_service(database, httpx.MockTransport(crash), clock=lambda: NOW)
    try:
        with pytest.raises(ProcessCrash):
            await service.convert(message_id)
        async with database.session() as session:
            candidate = await session.scalar(select(AffiliateCandidateModel))
            claim = await AffiliateCandidateRepository(session).claim_for_generation(
                candidate.id,
                now=NOW + timedelta(minutes=6),
                lease_until=NOW + timedelta(minutes=11),
                max_attempts=3,
            )
            assert claim is None
    finally:
        await http.aclose()
        await database.dispose()


async def test_legacy_generating_candidate_cannot_be_reclaimed(tmp_path: Path) -> None:
    from promo_bot.database.models import AffiliateCandidateModel
    from promo_bot.database.repositories import AffiliateCandidateRepository
    from tests.unit.test_aliexpress_conversion import CANONICAL, make_database, persist_and_process

    database = await make_database(tmp_path, "unknown-legacy.sqlite3")
    try:
        await persist_and_process(database, 1, CANONICAL)
        async with database.session() as session:
            candidate = await session.scalar(select(AffiliateCandidateModel))
            candidate.state = "GENERATING_AFFILIATE"
            candidate.generation_attempts = 1
            candidate.processing_lease_until = NOW - timedelta(minutes=1)
        async with database.session() as session:
            claim = await AffiliateCandidateRepository(session).claim_for_generation(
                candidate.id, now=NOW, lease_until=NOW + timedelta(minutes=5), max_attempts=3
            )
            assert claim is None
    finally:
        await database.dispose()


async def test_explicit_coin_legacy_transition_preserves_snapshot_and_consumes_once(tmp_path):
    from promo_bot.database.coin_shadow_repository import (
        CoinShadowFingerprintDomain,
        coin_shadow_fingerprint,
    )
    from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
    from tests.unit.test_affiliate_history_legacy import LEGACY_URL, legacy_database

    database = await legacy_database(tmp_path)
    gateway = SyntheticGateway(database)
    service = CoinShadowGenerationService(
        database,
        gateway,
        app_secret="fixture-secret",
        tracking_id="fixture-tracking",
        clock=lambda: NOW,
    )
    try:
        async with database.session() as session:
            old = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            old.input_fingerprint = coin_shadow_fingerprint(
                "fixture-secret", SHORT, CoinShadowFingerprintDomain.INPUT
            )
        with pytest.raises(ValueError, match="GENERATION_LINK_MISSING"):
            await service.generate(SHORT)
        async with database.session() as session:
            request = await AffiliateLinkHistoryRepository(session).request_legacy_generation(
                scope="shadow", legacy_kind="coin-evidence", legacy_id=1, now=NOW
            )
            request_id = request.id
        result = await service.generate(SHORT, generation_request=request_id)
        assert result.state == "READY"
        async with database.session() as session:
            generation = await session.get(AffiliateLinkGenerationModel, request_id)
            assert generation.state == "CONFIRMED"
            assert generation.legacy_record_snapshot["record"]["promotion_link"] == LEGACY_URL
            assert generation.generated_url == GENERATED
        with pytest.raises(ValueError, match="LEGACY_TARGET_INELIGIBLE"):
            await service.generate(SHORT, generation_request=request_id)
        assert gateway.calls == 1
    finally:
        await database.dispose()
