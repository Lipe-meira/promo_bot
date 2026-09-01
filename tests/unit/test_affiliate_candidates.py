from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from promo_bot.database.models import (
    Base,
    ProcessedItemModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    AffiliateCandidateTransitionConflict,
    SourceMessageRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import AffiliateCandidateState, SourceMessageState

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


async def make_database(tmp_path: Path, name: str) -> Database:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def add_shopee_link(
    database: Database, *, message_id: str, link_state: str, reason_code: str
) -> int:
    async with database.session() as session:
        message = SourceMessageModel(
            platform="telegram",
            message_id=message_id,
            channel_id="source",
            occurred_at=NOW,
            original_text="fixture",
            links=[],
            content_hash=message_id.zfill(64),
            processing_status=SourceMessageState.COMPLETED.value,
            completed_at=NOW,
        )
        session.add(message)
        await session.flush()
        link = SourceMessageLinkModel(
            source_message_id=message.id,
            ordinal=0,
            source_kind="TEXT",
            input_hash=message_id.zfill(64),
            input_url="https://shopee.com.br/product/10/20",
            store="shopee",
            external_product_id="10:20",
            canonical_url="https://shopee.com.br/product/10/20",
            state=link_state,
            reason_code=reason_code,
        )
        session.add(link)
        await session.flush()
        return link.id


async def add_mercado_livre_link(database: Database, *, message_id: str) -> int:
    async with database.session() as session:
        message = SourceMessageModel(
            platform="telegram",
            message_id=message_id,
            channel_id="source",
            occurred_at=NOW,
            original_text="fixture",
            links=[],
            content_hash=message_id.zfill(64),
            processing_status=SourceMessageState.COMPLETED.value,
            completed_at=NOW,
        )
        session.add(message)
        await session.flush()
        link = SourceMessageLinkModel(
            source_message_id=message.id,
            ordinal=0,
            source_kind="TEXT",
            input_hash=message_id.zfill(64),
            input_url="https://produto.mercadolivre.com.br/MLB-123456789",
            store="mercadolivre",
            external_product_id="MLB123456789",
            canonical_url="https://produto.mercadolivre.com.br/MLB-123456789",
            state="PENDING_AFFILIATE",
            reason_code="AFFILIATE_PROVIDER_REQUIRED",
        )
        session.add(link)
        await session.flush()
        return link.id


@pytest.mark.asyncio
async def test_backfill_retains_pending_and_duplicate_links_as_one_candidate(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "backfill.sqlite3")
    first_link = await add_shopee_link(
        database,
        message_id="1",
        link_state="PENDING_AFFILIATE",
        reason_code="AFFILIATE_PROVIDER_REQUIRED",
    )
    second_link = await add_shopee_link(
        database,
        message_id="2",
        link_state="IGNORED",
        reason_code="DUPLICATE_CANONICAL",
    )
    async with database.session() as session:
        session.add(
            ProcessedItemModel(
                store="shopee",
                external_product_id="10:20",
                variation_key="",
                deal_hash="observed-only",
                details={"state": "PENDING_AFFILIATE"},
            )
        )

    async with database.session() as session:
        count = await AffiliateCandidateRepository(session).backfill_shopee(limit=10)
    assert count == 2

    async with database.session() as session:
        repository = AffiliateCandidateRepository(session)
        candidate = await repository.find("shopee", "10:20")
        first = await session.get(SourceMessageLinkModel, first_link)
        second = await session.get(SourceMessageLinkModel, second_link)
        assert candidate is not None
        assert candidate.state == AffiliateCandidateState.PENDING_AFFILIATE.value
        assert first is not None and first.affiliate_candidate_id == candidate.id
        assert second is not None and second.affiliate_candidate_id == candidate.id
    await database.dispose()


@pytest.mark.asyncio
async def test_backfill_does_not_guess_identity_for_legacy_item_only_rows(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "legacy-identity.sqlite3")
    link_id = await add_shopee_link(
        database,
        message_id="1",
        link_state="PENDING_AFFILIATE",
        reason_code="AFFILIATE_PROVIDER_REQUIRED",
    )
    async with database.session() as session:
        link = await session.get(SourceMessageLinkModel, link_id)
        assert link is not None
        link.external_product_id = "20"

    async with database.session() as session:
        count = await AffiliateCandidateRepository(session).backfill_shopee(limit=10)
        link = await session.get(SourceMessageLinkModel, link_id)
        assert count == 0
        assert link is not None and link.affiliate_candidate_id is None
        with pytest.raises(ValueError, match="shop_id:item_id"):
            await AffiliateCandidateRepository(session).ensure_for_link(link_id)
    await database.dispose()


@pytest.mark.asyncio
async def test_candidate_claim_is_atomic_and_stale_lease_is_recoverable(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "claim.sqlite3")
    link_id = await add_shopee_link(
        database,
        message_id="1",
        link_state="PENDING_AFFILIATE",
        reason_code="AFFILIATE_PROVIDER_REQUIRED",
    )
    async with database.session() as session:
        repository = AffiliateCandidateRepository(session)
        candidate = await repository.ensure_for_link(link_id)
        candidate_id = candidate.id

    async with database.session() as session:
        repository = AffiliateCandidateRepository(session)
        first = await repository.claim(
            candidate_id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            max_attempts=3,
        )
        second = await repository.claim(
            candidate_id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            max_attempts=3,
        )
        assert first is not None
        assert second is None

    async with database.session() as session:
        recoverable = await AffiliateCandidateRepository(session).recoverable(
            now=NOW + timedelta(minutes=6), max_attempts=3, limit=10
        )
        assert [item.id for item in recoverable] == [candidate_id]
    await database.dispose()


@pytest.mark.asyncio
async def test_mercado_livre_candidates_reuse_existing_queue_and_lease(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "mercadolivre.sqlite3")
    link_id = await add_mercado_livre_link(database, message_id="1")
    async with database.session() as session:
        repository = AffiliateCandidateRepository(session)
        candidate = await repository.ensure_for_link(link_id)
        assert candidate.store == "mercadolivre"
        assert candidate.external_product_id == "MLB123456789"
        claimed = await repository.claim(
            candidate.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            max_attempts=3,
        )
        assert claimed is not None
        await repository.mark_awaiting_generation(candidate.id, now=NOW)

    async with database.session() as session:
        candidate = await AffiliateCandidateRepository(session).find("mercadolivre", "MLB123456789")
        assert candidate is not None
        assert candidate.state == AffiliateCandidateState.AWAITING_AFFILIATE_GENERATION.value
        assert candidate.processing_lease_until is None
        assert candidate.error_code == "WAITING_AFFILIATE_GENERATION"
    await database.dispose()


@pytest.mark.asyncio
async def test_provider_failure_does_not_reopen_completed_source_message(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "independent.sqlite3")
    link_id = await add_shopee_link(
        database,
        message_id="1",
        link_state="PENDING_AFFILIATE",
        reason_code="AFFILIATE_PROVIDER_REQUIRED",
    )
    async with database.session() as session:
        candidates = AffiliateCandidateRepository(session)
        candidate = await candidates.ensure_for_link(link_id)
        await candidates.claim(
            candidate.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            max_attempts=3,
        )
        await candidates.fail(
            candidate.id,
            now=NOW,
            target_state=AffiliateCandidateState.FAILED_RETRYABLE,
            next_attempt_at=NOW + timedelta(minutes=1),
            error_code="PROVIDER_UNAVAILABLE",
        )

    async with database.session() as session:
        source = await SourceMessageRepository(session).get(1)
        assert source is not None
        assert source.processing_status == SourceMessageState.COMPLETED.value
        assert source.completed_at == NOW
    await database.dispose()


@pytest.mark.asyncio
async def test_candidate_transition_rejects_lost_state_and_expired_lease(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "transition-conflict.sqlite3")
    link_id = await add_shopee_link(
        database,
        message_id="1",
        link_state="PENDING_AFFILIATE",
        reason_code="AFFILIATE_PROVIDER_REQUIRED",
    )
    async with database.session() as session:
        candidates = AffiliateCandidateRepository(session)
        candidate = await candidates.ensure_for_link(link_id)
        await candidates.claim(
            candidate.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=1),
            max_attempts=3,
        )
        with pytest.raises(AffiliateCandidateTransitionConflict, match="lease expired"):
            await candidates.fail(
                candidate.id,
                now=NOW + timedelta(minutes=2),
                target_state=AffiliateCandidateState.FAILED_RETRYABLE,
                next_attempt_at=NOW + timedelta(minutes=3),
                error_code="PROVIDER_UNAVAILABLE",
            )

    async with database.session() as session:
        candidates = AffiliateCandidateRepository(session)
        reclaimed = await candidates.claim(
            candidate.id,
            now=NOW + timedelta(minutes=2),
            lease_until=NOW + timedelta(minutes=7),
            max_attempts=3,
        )
        assert reclaimed is not None
        await candidates.fail(
            candidate.id,
            now=NOW + timedelta(minutes=2),
            target_state=AffiliateCandidateState.FAILED_PERMANENT,
            next_attempt_at=None,
            error_code="PERMANENT",
        )
        with pytest.raises(AffiliateCandidateTransitionConflict, match="transition lost"):
            await candidates.fail(
                candidate.id,
                now=NOW + timedelta(minutes=2),
                target_state=AffiliateCandidateState.FAILED_PERMANENT,
                next_attempt_at=None,
                error_code="PERMANENT",
            )
    await database.dispose()
