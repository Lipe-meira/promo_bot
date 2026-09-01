from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from promo_bot.affiliate.service import ShopeeEnrichmentService
from promo_bot.database.models import (
    AffiliateLinkProofModel,
    Base,
    DealModel,
    ProductModel,
    ShopeeProductSnapshotModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.repositories import AffiliateCandidateRepository
from promo_bot.database.session import Database
from promo_bot.domain.enums import AffiliateCandidateState, SourceMessageState
from promo_bot.domain.models import Money
from promo_bot.providers.base import ProviderError
from promo_bot.providers.shopee.models import (
    AffiliateLinkProof,
    EnrichedAffiliateOffer,
    ProductSnapshot,
    ProviderProductReference,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


async def claimed_candidate(database: Database) -> int:
    async with database.session() as session:
        source = SourceMessageModel(
            platform="telegram",
            message_id="1",
            channel_id="channel",
            occurred_at=NOW,
            original_text="fixture",
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
            input_url="https://shopee.com.br/product/10/20",
            store="shopee",
            external_product_id="10:20",
            canonical_url="https://shopee.com.br/product/10/20",
            state="PENDING_AFFILIATE",
            reason_code="AFFILIATE_PROVIDER_REQUIRED",
        )
        session.add(link)
        await session.flush()
        repository = AffiliateCandidateRepository(session)
        candidate = await repository.ensure_for_link(link.id)
        claimed = await repository.claim(
            candidate.id,
            now=NOW,
            lease_until=NOW + timedelta(minutes=5),
            max_attempts=3,
        )
        assert claimed is not None
        return candidate.id


def offer(
    *, image_url: str | None, minimum: str = "90", maximum: str = "90"
) -> EnrichedAffiliateOffer:
    reference = ProviderProductReference(
        store="shopee",
        external_product_id="10:20",
        canonical_url="https://shopee.com.br/product/10/20",
        shop_id="10",
        item_id="20",
    )
    return EnrichedAffiliateOffer(
        product=ProductSnapshot(
            reference=reference,
            title="Produto oficial de teste",
            price_min=Money(Decimal(minimum)),
            price_max=Money(Decimal(maximum)),
            available=True,
            queried_at=NOW,
            seller="Loja de teste",
            image_url=image_url,
            range_semantics_confirmed=minimum != maximum,
        ),
        affiliate_proof=AffiliateLinkProof(
            provider="shopee_official",
            operation="syntheticOperation",
            requested_at=NOW,
            responded_at=NOW + timedelta(seconds=1),
            source_external_product_id="10:20",
            canonical_url="https://shopee.com.br/product/10/20",
            short_link="https://short.example.test/fixture",
            official_endpoint_host="official.example.test",
            credential_profile_id="default",
            contract_version="fixture-v1",
            sub_ids=("test",),
            official_response_validated=True,
        ),
    )


@pytest.mark.asyncio
async def test_validated_offer_persists_ready_deal_and_sanitized_proof(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'offer.sqlite3').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    candidate_id = await claimed_candidate(database)

    deal_id = await ShopeeEnrichmentService(
        database,
        official_image_hosts=frozenset({"cdn.example.test"}),
    ).persist(candidate_id, offer(image_url="https://cdn.example.test/image.jpg"))

    async with database.session() as session:
        deal = await session.get(DealModel, deal_id)
        product = (await session.execute(select(ProductModel))).scalar_one()
        proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
        snapshot = (await session.execute(select(ShopeeProductSnapshotModel))).scalar_one()
        candidate = await AffiliateCandidateRepository(session).get(candidate_id)
        source = await session.get(SourceMessageModel, 1)
        assert deal is not None and deal.status == "READY"
        assert deal.confidence == "MEDIUM"
        assert deal.price_display_mode == "EXACT"
        assert deal.affiliate_link == "https://short.example.test/fixture"
        assert product.image_url == "https://cdn.example.test/image.jpg"
        assert proof.operation == "syntheticOperation"
        assert proof.sub_ids == ["test"]
        assert not hasattr(proof, "signature")
        assert snapshot.price_min == Decimal("90.00")
        assert candidate is not None and candidate.state == AffiliateCandidateState.ENRICHED.value
        assert source is not None and source.processing_status == SourceMessageState.COMPLETED.value
    await database.dispose()


@pytest.mark.asyncio
async def test_zero_price_is_rejected_before_ready_records_are_created(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'zero.sqlite3').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    candidate_id = await claimed_candidate(database)

    with pytest.raises(ProviderError) as captured:
        await ShopeeEnrichmentService(database, official_image_hosts=frozenset()).persist(
            candidate_id,
            offer(image_url=None, minimum="0", maximum="0"),
        )

    assert captured.value.code == "SHOPEE_NON_POSITIVE_PRICE"
    async with database.session() as session:
        assert (await session.execute(select(DealModel))).scalar_one_or_none() is None
    await database.dispose()


@pytest.mark.asyncio
async def test_unconfirmed_image_is_dropped_but_text_deal_remains_ready(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'image.sqlite3').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    candidate_id = await claimed_candidate(database)

    deal_id = await ShopeeEnrichmentService(database, official_image_hosts=frozenset()).persist(
        candidate_id,
        offer(image_url="https://unconfirmed.example.test/image.jpg"),
    )

    async with database.session() as session:
        deal = await session.get(DealModel, deal_id)
        product = (await session.execute(select(ProductModel))).scalar_one()
        assert deal is not None and deal.status == "READY"
        assert product.image_url is None
    await database.dispose()


@pytest.mark.asyncio
async def test_confirmed_range_is_persisted_as_starting_at_not_exact(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'range.sqlite3').as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    candidate_id = await claimed_candidate(database)

    deal_id = await ShopeeEnrichmentService(database, official_image_hosts=frozenset()).persist(
        candidate_id,
        offer(image_url=None, minimum="90", maximum="120"),
    )

    async with database.session() as session:
        deal = await session.get(DealModel, deal_id)
        assert deal is not None
        assert deal.current_price == Decimal("90.00")
        assert deal.price_min == Decimal("90.00")
        assert deal.price_max == Decimal("120.00")
        assert deal.price_display_mode == "STARTING_AT"
    await database.dispose()
