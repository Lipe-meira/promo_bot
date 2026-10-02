"""Review corrections at the generation, legacy-command and fake-wire seams."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
from promo_bot.database.coin_shadow_repository import (
    CoinShadowFingerprintDomain,
    coin_shadow_fingerprint,
)
from promo_bot.database.history_models import AffiliateLinkGenerationModel
from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
from promo_bot.database.models import AliExpressCoinShadowEvidenceModel
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from tests.offline_aliexpress import OfflineSignedAliExpressClient
from tests.unit.test_affiliate_history_generation import NOW, SHORT, SyntheticGateway
from tests.unit.test_affiliate_history_legacy import legacy_database

SECRET = "fixture-secret"
TRACKING = "fixture-tracking"
OTHER_SHORT = "https://a.aliexpress.com/_Ef56Gh78"


async def test_same_key_context_proves_other_legacy_ready_is_unrelated(tmp_path):
    database = await legacy_database(tmp_path)
    gateway = SyntheticGateway(database)
    try:
        async with database.session() as session:
            legacy = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            legacy.input_fingerprint = coin_shadow_fingerprint(
                SECRET, OTHER_SHORT, CoinShadowFingerprintDomain.INPUT
            )
            legacy.tracking_fingerprint = coin_shadow_fingerprint(
                SECRET, TRACKING, CoinShadowFingerprintDomain.TRACKING
            )
        outcome = await CoinShadowGenerationService(
            database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
        ).generate(SHORT)
        assert outcome.state == "READY"
        assert gateway.calls == 1
        async with database.session() as session:
            legacy = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            assert legacy.state == "READY" and legacy.generation_id is None
            generations = list(await session.scalars(select(AffiliateLinkGenerationModel)))
            assert len(generations) == 1 and generations[0].state == "CONFIRMED"
    finally:
        await database.dispose()


@pytest.mark.parametrize("body", [[], "synthetic-scalar", 123, True, None])
async def test_canonical_complete_nonobject_rejects_without_retry_or_confirming_tracking(
    tmp_path, body
):
    from promo_bot.affiliate.aliexpress_conversion import AliExpressConversionRejected
    from tests.unit.test_aliexpress_conversion import (
        CANONICAL,
        conversion_service,
        make_database,
        persist_and_process,
    )

    database = await make_database(tmp_path, "canonical-complete.sqlite3")
    message_id = await persist_and_process(database, 1, CANONICAL)
    calls = []
    service, http = conversion_service(
        database,
        httpx.MockTransport(
            lambda request: (
                calls.append(request.method)
                or httpx.Response(
                    200, content=json.dumps(body), headers={"Content-Type": "application/json"}
                )
            )
        ),
        clock=lambda: NOW,
    )
    try:
        with pytest.raises(AliExpressConversionRejected, match="ALIEXPRESS_RESPONSE_INCOMPATIBLE"):
            await service.convert(message_id)
        async with database.session() as session:
            generation = await session.scalar(select(AffiliateLinkGenerationModel))
            assert generation.state == "REJECTED"
            assert generation.generated_url is None and not generation.tracking_confirmed
        service.clock = lambda: NOW + timedelta(days=2)
        with pytest.raises(AliExpressConversionRejected):
            await service.convert(message_id)
        assert calls == ["POST"]
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.parametrize("body", [[], "synthetic-scalar", 123, True, None])
async def test_complete_nonobject_response_is_rejected_and_restart_never_repeats_top(
    tmp_path, body, caplog, capsys
):
    from tests.unit.test_affiliate_history_generation import make_database

    database = await make_database(tmp_path)
    calls = []
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    calls.append(request.method)
                    or httpx.Response(
                        200, content=json.dumps(body), headers={"Content-Type": "application/json"}
                    )
                )
            ),
            trust_env=False,
            follow_redirects=False,
        ) as http:
            client = OfflineSignedAliExpressClient(
                AliExpressHttpTransport(http, max_attempts=1, durable_retry=False),
                request_builder=AliExpressTopRequestBuilder("fixture", SECRET),
            )
            service = CoinShadowGenerationService(
                database, client, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
            )
            outcome = await service.generate(SHORT)
            assert outcome.state == "REVIEW_REQUIRED"
            assert outcome.promotion_link is None
            async with database.session() as session:
                generation = await session.scalar(select(AffiliateLinkGenerationModel))
                assert generation.state == "REJECTED"
                assert generation.generated_url is None
                assert generation.call_started_at == NOW
            restarted = CoinShadowGenerationService(
                database,
                client,
                app_secret=SECRET,
                tracking_id=TRACKING,
                clock=lambda: NOW + timedelta(days=2),
            )
            with pytest.raises(ValueError, match="LEGACY_TARGET_INELIGIBLE"):
                await restarted.generate(SHORT)
            rotated = CoinShadowGenerationService(
                database,
                client,
                app_secret="rotated-secret",
                tracking_id=TRACKING,
                clock=lambda: NOW + timedelta(days=2),
            )
            with pytest.raises(ValueError, match="GENERATION_REJECTED_BLOCKED"):
                await rotated.generate(SHORT)
            assert calls == ["POST"]
            captured = capsys.readouterr()
            for sensitive in (SHORT, SECRET, TRACKING, "https://"):
                assert sensitive not in captured.out + captured.err + caplog.text
    finally:
        await database.dispose()


@pytest.mark.parametrize("commit_failure", [False, True])
async def test_consumed_request_rejection_or_commit_failure_never_rearms(tmp_path, commit_failure):
    database = await legacy_database(tmp_path)
    calls = []
    try:
        async with database.session() as session:
            old = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            old.input_fingerprint = coin_shadow_fingerprint(
                SECRET, SHORT, CoinShadowFingerprintDomain.INPUT
            )
        async with database.session() as session:
            request = await AffiliateLinkHistoryRepository(session).request_legacy_generation(
                scope="shadow", legacy_kind="coin-evidence", legacy_id=1, now=NOW
            )
            request_id = request.id
        if commit_failure:
            async with database.session() as session:
                await session.execute(
                    text(
                        "CREATE TRIGGER synthetic_rejected_write_failure BEFORE UPDATE "
                        "ON affiliate_link_generations WHEN NEW.state='REJECTED' "
                        "BEGIN SELECT RAISE(ABORT, 'SYNTHETIC_STORAGE_FAILURE'); END"
                    )
                )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: calls.append(request.method) or httpx.Response(200, json=[])
            ),
            trust_env=False,
            follow_redirects=False,
        ) as http:
            client = OfflineSignedAliExpressClient(
                AliExpressHttpTransport(http, max_attempts=1, durable_retry=False),
                request_builder=AliExpressTopRequestBuilder("fixture", SECRET),
            )
            service = CoinShadowGenerationService(
                database, client, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
            )
            outcome = await service.generate(SHORT, generation_request=request_id)
            assert outcome.state == ("UNCERTAIN" if commit_failure else "REVIEW_REQUIRED")
            async with database.session() as session:
                request = await session.get(AffiliateLinkGenerationModel, request_id)
                assert request.state == ("UNCERTAIN" if commit_failure else "REJECTED")
                assert request.legacy_record_snapshot["label"] == "LEGACY_NOT_REVALIDATED"
                assert request.generated_url is None and not request.tracking_confirmed
                count = list(await session.scalars(select(AffiliateLinkGenerationModel)))
                assert len(count) == 1
            code = "GENERATION_UNCERTAIN_BLOCKED" if commit_failure else "LEGACY_TARGET_INELIGIBLE"
            with pytest.raises(ValueError, match=code):
                await service.generate(SHORT, generation_request=request_id)
            with pytest.raises(ValueError, match=code):
                await service.generate(SHORT)
            assert calls == ["POST"]
    finally:
        await database.dispose()


@pytest.mark.parametrize("failure", ["timeout", "truncated-json"])
async def test_unknown_response_remains_uncertain_without_retry(tmp_path, failure):
    from tests.unit.test_affiliate_history_generation import make_database

    database = await make_database(tmp_path)
    calls = []

    def respond(request):
        calls.append(request.method)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(200, content='{"incomplete":')

    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), trust_env=False
        ) as http:
            client = OfflineSignedAliExpressClient(
                AliExpressHttpTransport(http, max_attempts=1, durable_retry=False),
                request_builder=AliExpressTopRequestBuilder("fixture", SECRET),
            )
            service = CoinShadowGenerationService(
                database, client, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
            )
            assert (await service.generate(SHORT)).state == "UNCERTAIN"
            with pytest.raises(ValueError, match="GENERATION_UNCERTAIN_BLOCKED"):
                await service.generate(SHORT)
            async with database.session() as session:
                assert (
                    await session.scalar(select(AffiliateLinkGenerationModel))
                ).state == "UNCERTAIN"
            assert calls == ["POST"]
    finally:
        await database.dispose()


@pytest.mark.parametrize("comparable", [True, False])
async def test_ready_transition_with_other_uncertain_requires_provable_separation(
    tmp_path, comparable
):
    database = await legacy_database(tmp_path)
    gateway = SyntheticGateway(database)
    try:
        async with database.session() as session:
            target = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            target.input_fingerprint = coin_shadow_fingerprint(
                SECRET, SHORT, CoinShadowFingerprintDomain.INPUT
            )
            session.add(
                AliExpressCoinShadowEvidenceModel(
                    input_fingerprint=coin_shadow_fingerprint(
                        SECRET, OTHER_SHORT, CoinShadowFingerprintDomain.INPUT
                    ),
                    tracking_fingerprint=(
                        coin_shadow_fingerprint(
                            SECRET, TRACKING, CoinShadowFingerprintDomain.TRACKING
                        )
                        if comparable
                        else "c" * 64
                    ),
                    promotion_link_type=0,
                    state="UNCERTAIN",
                    generation_count=1,
                    tracking_confirmed=False,
                    attribution_unverified=True,
                    route_preservation_manually_observed=False,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        async with database.session() as session:
            repo = AffiliateLinkHistoryRepository(session)
            reports = await repo.legacy_blocks(scope="shadow")
            target_report = next(row for row in reports if row["legacy_id"] == 1)
            assert target_report["record_eligible"] is True
            assert target_report["execution_eligible"] is None
            assert target_report["correspondence_status"] == "UNPROVEN"
            request = await repo.request_legacy_generation(
                scope="shadow", legacy_kind="coin-evidence", legacy_id=1, now=NOW
            )
            request_id = request.id
        service = CoinShadowGenerationService(
            database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
        )
        if comparable:
            assert (await service.generate(SHORT, generation_request=request_id)).state == "READY"
        else:
            with pytest.raises(ValueError, match="GENERATION_UNCERTAIN_BLOCKED"):
                await service.generate(SHORT, generation_request=request_id)
        assert gateway.calls == (1 if comparable else 0)
        async with database.session() as session:
            uncertain = await session.get(AliExpressCoinShadowEvidenceModel, 2)
            assert uncertain.state == "UNCERTAIN" and uncertain.generation_id is None
            request = await session.get(AffiliateLinkGenerationModel, request_id)
            assert request.state == ("CONFIRMED" if comparable else "REQUESTED")
            assert request.legacy_record_snapshot["label"] == "LEGACY_NOT_REVALIDATED"
    finally:
        await database.dispose()


async def test_two_consumers_and_restart_cannot_rearm_one_legacy_request(tmp_path):
    database = await legacy_database(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedGateway(SyntheticGateway):
        async def execute(self, operation, payload):
            response = await super().execute(operation, payload)
            entered.set()
            await release.wait()
            return response

    gateway = PausedGateway(database)
    service = CoinShadowGenerationService(
        database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
    )
    first = None
    try:
        async with database.session() as session:
            old = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            old.input_fingerprint = coin_shadow_fingerprint(
                SECRET, SHORT, CoinShadowFingerprintDomain.INPUT
            )
        async with database.session() as session:
            request = await AffiliateLinkHistoryRepository(session).request_legacy_generation(
                scope="shadow", legacy_kind="coin-evidence", legacy_id=1, now=NOW
            )
            request_id = request.id
        first = asyncio.create_task(service.generate(SHORT, generation_request=request_id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(ValueError, match="LEGACY_TARGET_INELIGIBLE"):
            await service.generate(SHORT, generation_request=request_id)
        release.set()
        assert (await first).state == "READY"
        restarted = CoinShadowGenerationService(
            database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
        )
        with pytest.raises(ValueError, match="LEGACY_TARGET_INELIGIBLE"):
            await restarted.generate(SHORT, generation_request=request_id)
        assert (await restarted.generate(SHORT)).cache_hit is True
        assert gateway.calls == 1
    finally:
        release.set()
        if first is not None:
            await first
        await database.dispose()


@pytest.mark.parametrize("state", ["GENERATING", "UNCERTAIN", "REVIEW_REQUIRED"])
async def test_unrelated_legacy_failure_with_comparable_context_stays_preserved(tmp_path, state):
    database = await legacy_database(tmp_path)
    gateway = SyntheticGateway(database)
    try:
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            row.input_fingerprint = coin_shadow_fingerprint(
                SECRET, OTHER_SHORT, CoinShadowFingerprintDomain.INPUT
            )
            row.tracking_fingerprint = coin_shadow_fingerprint(
                SECRET, TRACKING, CoinShadowFingerprintDomain.TRACKING
            )
            row.state = state
            row.promotion_link = row.affiliate_host = row.correlation_mode = None
            row.generated_at = row.expires_at = None
            row.tracking_confirmed = False
            if state == "GENERATING":
                row.lease_token, row.lease_until = "old-owner", NOW + timedelta(minutes=5)
        outcome = await CoinShadowGenerationService(
            database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
        ).generate(SHORT)
        assert outcome.state == "READY" and gateway.calls == 1
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            assert row.state == state and row.generation_id is None
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "state,code",
    [
        ("GENERATING", "AFFILIATE_HISTORY_GENERATION_IN_PROGRESS"),
        ("UNCERTAIN", "AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED"),
        ("REVIEW_REQUIRED", "AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE"),
    ],
)
async def test_known_target_block_agrees_across_legacy_listing_request_and_consumption(
    tmp_path, state, code
):
    database = await legacy_database(tmp_path)
    gateway = SyntheticGateway(database)
    try:
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            row.input_fingerprint = coin_shadow_fingerprint(
                SECRET, SHORT, CoinShadowFingerprintDomain.INPUT
            )
            # Input equality alone proves correspondence even after a tracking change.
            row.state = state
            row.promotion_link = row.affiliate_host = row.correlation_mode = None
            row.generated_at = row.expires_at = None
            row.tracking_confirmed = False
            if state == "GENERATING":
                row.lease_token, row.lease_until = "old-owner", NOW + timedelta(minutes=5)
        async with database.session() as session:
            repo = AffiliateLinkHistoryRepository(session)
            report = (await repo.legacy_blocks(scope="shadow"))[0]
            assert report["block_code"] == code
            assert report["record_eligible"] is False
            assert report["execution_eligible"] is False
            with pytest.raises(ValueError, match=code):
                await repo.request_legacy_generation(
                    scope="shadow", legacy_kind="coin-evidence", legacy_id=1, now=NOW
                )
        with pytest.raises(ValueError, match=code):
            await CoinShadowGenerationService(
                database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
            ).generate(SHORT)
        assert gateway.calls == 0
    finally:
        await database.dispose()


async def test_unknown_legacy_key_context_has_specific_diagnostic_and_no_call(tmp_path):
    database = await legacy_database(tmp_path)
    gateway = SyntheticGateway(database)
    service = CoinShadowGenerationService(
        database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
    )
    try:
        async with database.session() as session:
            request = await AffiliateLinkHistoryRepository(session).request_legacy_generation(
                scope="shadow", legacy_kind="coin-evidence", legacy_id=1, now=NOW
            )
            request_id = request.id
        for request_id_or_none in (None, request_id):
            with pytest.raises(ValueError, match="AFFILIATE_HISTORY_LEGACY_KEY_CONTEXT_UNPROVEN"):
                await service.generate(SHORT, generation_request=request_id_or_none)
        assert gateway.calls == 0
        async with database.session() as session:
            legacy = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            request = await session.get(AffiliateLinkGenerationModel, request_id)
            assert legacy.state == "READY" and legacy.generation_id is None
            assert request.state == "REQUESTED" and request.call_started_at is None
    finally:
        await database.dispose()


@pytest.mark.parametrize("key_context", [None, "d" * 64])
async def test_durable_uncertain_without_comparable_key_still_blocks_other_identity(
    tmp_path, key_context
):
    from tests.unit.test_affiliate_history_generation import make_database

    database = await make_database(tmp_path)
    gateway = SyntheticGateway(database)
    try:
        async with database.session() as session:
            repo = AffiliateLinkHistoryRepository(session)
            row = await repo.prepare(
                scope="shadow",
                identity_key="coin:" + "a" * 64,
                now=NOW,
                lease_until=NOW + timedelta(minutes=5),
                lease_token="owner",
                call_id="call",
                call_ordinal=0,
                input_fingerprint="a" * 64,
                tracking_fingerprint="b" * 64,
                key_fingerprint=key_context,
            )
            await repo.start_call((row.id,), now=NOW, lease_tokens=("owner",))
            await repo.fail((row.id,), now=NOW, state="UNCERTAIN", error_code="SYNTHETIC_TIMEOUT")
            generation_id = row.id
        for _ in range(2):
            with pytest.raises(ValueError, match="GENERATION_UNCERTAIN_BLOCKED"):
                await CoinShadowGenerationService(
                    database, gateway, app_secret=SECRET, tracking_id=TRACKING, clock=lambda: NOW
                ).generate(SHORT)
        assert gateway.calls == 0
        async with database.session() as session:
            assert (
                await session.get(AffiliateLinkGenerationModel, generation_id)
            ).state == "UNCERTAIN"
    finally:
        await database.dispose()
