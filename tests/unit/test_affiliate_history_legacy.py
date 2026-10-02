from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from promo_bot.database import history_repository
from promo_bot.database.models import AliExpressCoinShadowEvidenceModel, Base
from promo_bot.database.session import create_affiliate_shadow_database

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
LEGACY_URL = "https://s.click.aliexpress.com/e/synthetic-old"


async def legacy_database(tmp_path: Path):
    database = create_affiliate_shadow_database(tmp_path / "legacy.sqlite3")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        row = AliExpressCoinShadowEvidenceModel(
            input_fingerprint="a" * 64,
            tracking_fingerprint="b" * 64,
            promotion_link_type=0,
            state="READY",
            generation_count=1,
            tracking_confirmed=True,
            attribution_unverified=True,
            route_preservation_manually_observed=False,
            generation_started_at=NOW - timedelta(days=2),
            generated_at=NOW - timedelta(days=2),
            expires_at=NOW - timedelta(days=1),
            correlation_mode="POSITIONAL_SINGLETON",
            promotion_link=LEGACY_URL,
            affiliate_host="s.click.aliexpress.com",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(row)
    return database


async def test_explicit_request_preserves_expired_legacy_snapshot_without_confirmation(
    tmp_path: Path,
) -> None:
    database = await legacy_database(tmp_path)
    try:
        repository_type = getattr(history_repository, "AffiliateLinkHistoryRepository", None)
        assert repository_type is not None, "explicit legacy transition is not implemented"
        async with database.session() as session:
            request = await repository_type(session).request_legacy_generation(
                scope="shadow",
                legacy_kind="coin-evidence",
                legacy_id=1,
                now=NOW,
            )
            assert request.state == "REQUESTED"
            assert request.generated_url is None and request.generated_at is None
            assert request.tracking_confirmed is False
            assert request.legacy_record_snapshot["label"] == "LEGACY_NOT_REVALIDATED"
            assert request.legacy_record_snapshot["record"]["promotion_link"] == LEGACY_URL
            request_id = request.id
        async with database.session() as session:
            second = await repository_type(session).request_legacy_generation(
                scope="shadow",
                legacy_kind="coin-evidence",
                legacy_id=1,
                now=NOW,
            )
            assert second.id == request_id
            old = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            assert old.promotion_link == LEGACY_URL
            assert old.state == "READY" and old.generation_id is None
    finally:
        await database.dispose()


@pytest.mark.parametrize("state", ["GENERATING", "UNCERTAIN", "REVIEW_REQUIRED"])
async def test_legacy_request_never_unlocks_active_unknown_or_rejected_evidence(
    tmp_path: Path,
    state: str,
) -> None:
    database = await legacy_database(tmp_path)
    try:
        async with database.session() as session:
            old = await session.get(AliExpressCoinShadowEvidenceModel, 1)
            old.state = state
            old.promotion_link = None
            old.affiliate_host = None
            old.tracking_confirmed = False
            old.correlation_mode = None
            old.generated_at = None
            old.expires_at = None
            if state == "GENERATING":
                old.lease_until = NOW - timedelta(minutes=1)
                old.lease_token = "expired-token"
        async with database.session() as session:
            with pytest.raises(ValueError, match="AFFILIATE_HISTORY_"):
                await history_repository.AffiliateLinkHistoryRepository(
                    session
                ).request_legacy_generation(
                    scope="shadow",
                    legacy_kind="coin-evidence",
                    legacy_id=1,
                    now=NOW,
                )
    finally:
        await database.dispose()


async def test_two_concurrent_operators_receive_one_pending_authorization(tmp_path: Path) -> None:
    database = await legacy_database(tmp_path)
    other = create_affiliate_shadow_database(tmp_path / "legacy.sqlite3")
    try:

        async def request(handle):
            async with handle.session() as session:
                row = await history_repository.AffiliateLinkHistoryRepository(
                    session
                ).request_legacy_generation(
                    scope="shadow",
                    legacy_kind="coin-evidence",
                    legacy_id=1,
                    now=NOW,
                )
                return row.id

        first, second = await asyncio.gather(request(database), request(other))
        assert first == second
    finally:
        await other.dispose()
        await database.dispose()


async def test_canonical_request_detects_changed_variation_identity(tmp_path: Path) -> None:
    import httpx
    from sqlalchemy import select

    from promo_bot.database.models import AffiliateCandidateModel, AffiliateLinkProofModel
    from tests.unit.test_aliexpress_conversion import (
        CANONICAL,
        conversion_service,
        link_response,
        make_database,
        persist_and_process,
    )

    database = await make_database(tmp_path, "canonical-legacy.sqlite3")
    message_id = await persist_and_process(database, 1, CANONICAL)
    service, http = conversion_service(
        database,
        httpx.MockTransport(lambda request: httpx.Response(200, json=link_response())),
        clock=lambda: NOW,
    )
    try:
        await service.convert(message_id)
        async with database.session() as session:
            proof = await session.scalar(select(AffiliateLinkProofModel))
            proof.generation_id = None
        async with database.session() as session:
            repository = history_repository.AffiliateLinkHistoryRepository(session)
            request = await repository.request_legacy_generation(
                scope="runtime",
                legacy_kind="canonical-proof",
                legacy_id=1,
                now=NOW,
            )
            request_id = request.id
        async with database.session() as session:
            candidate = await session.get(AffiliateCandidateModel, 1)
            candidate.variation_key = "new-variation"
        async with database.session() as session:
            with pytest.raises(ValueError, match="AFFILIATE_HISTORY_LEGACY_TARGET_CHANGED"):
                await history_repository.AffiliateLinkHistoryRepository(session).validate_request(
                    request_id,
                    scope="runtime",
                    identity_key="canonical:1",
                    legacy_kind="canonical-proof",
                )
    finally:
        await http.aclose()
        await database.dispose()
