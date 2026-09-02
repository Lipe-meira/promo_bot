from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.affiliate.service import AliExpressEnrichmentService
from promo_bot.database.models import (
    AffiliateLinkProofModel,
    AliExpressProductSnapshotModel,
    Base,
    DealModel,
    DeliveryModel,
    ProductModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    AffiliateCandidateTransitionConflict,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import AffiliateCandidateState, SourceMessageState
from promo_bot.domain.models import Money
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.models import (
    AffiliateLinkProof,
    AliExpressProductReference,
    AliExpressProductSnapshot,
    EnrichedAffiliateOffer,
    PriceScope,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
PRODUCT_ID = "1005000000000001"
CANONICAL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"


async def make_database(tmp_path: Path, name: str) -> Database:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def claimed_candidate(database: Database) -> int:
    async with database.session() as session:
        source = SourceMessageModel(
            platform="telegram",
            message_id="ali-1",
            channel_id="fixture-channel",
            occurred_at=NOW,
            original_text="sanitized fixture",
            links=[],
            content_hash="a" * 64,
            processing_status=SourceMessageState.COMPLETED.value,
            completed_at=NOW,
        )
        session.add(source)
        await session.flush()
        link = SourceMessageLinkModel(
            source_message_id=source.id,
            ordinal=0,
            source_kind="TEXT",
            input_hash="b" * 64,
            input_url=CANONICAL,
            store="aliexpress",
            external_product_id=PRODUCT_ID,
            canonical_url=CANONICAL,
            state="PENDING_AFFILIATE",
            reason_code="AFFILIATE_PROVIDER_UNAVAILABLE",
        )
        session.add(link)
        await session.flush()
        candidates = AffiliateCandidateRepository(session)
        first = await candidates.ensure_for_link(link.id)
        second = await candidates.ensure_for_link(link.id)
        assert first.id == second.id
        claimed = await candidates.claim(
            first.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            max_attempts=3,
        )
        assert claimed is not None
        return first.id


def offer(*, operation: str = LINK_GENERATE) -> EnrichedAffiliateOffer:
    reference = AliExpressProductReference(PRODUCT_ID, CANONICAL)
    return EnrichedAffiliateOffer(
        product=AliExpressProductSnapshot(
            reference=reference,
            title="Produto oficial de fixture",
            price_min=Money(Decimal("119.90")),
            price_max=Money(Decimal("119.90")),
            price_scope=PriceScope.PRODUCT,
            queried_at=NOW,
            available=True,
            image_url="https://ae01.alicdn.com/kf/fixture.jpg",
            seller="Loja fixture",
            commission_rate=Decimal("3.5"),
            commission_amount=Money(Decimal("4.20")),
            shipping_fee=Money(Decimal("12.50")),
        ),
        affiliate_proof=AffiliateLinkProof(
            operation=operation,
            requested_at=NOW,
            responded_at=NOW + timedelta(seconds=1),
            source_external_product_id=PRODUCT_ID,
            canonical_url=CANONICAL,
            short_link="https://s.click.aliexpress.com/e/fixture",
            official_endpoint_host="api-sg.aliexpress.com",
            credential_profile_id="default",
            contract_version="attachment-2026-09",
            official_response_validated=True,
        ),
    )


@pytest.mark.asyncio
async def test_official_proof_persists_ready_state_without_creating_outbox(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "aliexpress.sqlite3")
    candidate_id = await claimed_candidate(database)

    deal_id = await AliExpressEnrichmentService(
        database,
        official_endpoint_hosts=frozenset({"api-sg.aliexpress.com"}),
    ).persist(candidate_id, offer())

    async with database.session() as session:
        deal = await session.get(DealModel, deal_id)
        product = (await session.execute(select(ProductModel))).scalar_one()
        proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
        snapshot = (await session.execute(select(AliExpressProductSnapshotModel))).scalar_one()
        candidate = await AffiliateCandidateRepository(session).get(candidate_id)
        deliveries = await session.scalar(select(func.count()).select_from(DeliveryModel))
        assert deal is not None and deal.status == "READY"
        assert deal.affiliate_link == proof.short_link
        assert deal.confidence == "MEDIUM"
        assert deal.source == "aliexpress_affiliate_api"
        assert proof.provider == "aliexpress_official"
        assert proof.operation == LINK_GENERATE
        assert product.store == "aliexpress"
        assert snapshot.external_product_id == PRODUCT_ID
        assert snapshot.shipping_fee == Decimal("12.50")
        assert candidate is not None
        assert candidate.state == AffiliateCandidateState.ENRICHED.value
        assert deliveries == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_non_link_generate_evidence_cannot_create_ready_records(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "invalid-proof.sqlite3")
    candidate_id = await claimed_candidate(database)

    with pytest.raises(ValueError, match=r"link\.generate"):
        await AliExpressEnrichmentService(
            database,
            official_endpoint_hosts=frozenset({"api-sg.aliexpress.com"}),
        ).persist(candidate_id, offer(operation="aliexpress.affiliate.productdetail.get"))

    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(DealModel)) == 0
        assert await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_expired_candidate_lease_rolls_back_all_enrichment_records(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "expired.sqlite3")
    candidate_id = await claimed_candidate(database)
    expired_offer = offer()
    expired_proof = expired_offer.affiliate_proof
    object.__setattr__(expired_proof, "responded_at", NOW + timedelta(minutes=6))

    with pytest.raises(AffiliateCandidateTransitionConflict):
        await AliExpressEnrichmentService(
            database,
            official_endpoint_hosts=frozenset({"api-sg.aliexpress.com"}),
        ).persist(candidate_id, expired_offer)

    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(DealModel)) == 0
        candidate = await AffiliateCandidateRepository(session).get(candidate_id)
        assert candidate is not None and candidate.state == "VALIDATING"
    await database.dispose()
