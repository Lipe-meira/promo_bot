"""Synthetic audit facts for pre-existing isolated delivery fixtures; never real backfill."""

from datetime import timedelta
from uuid import uuid4

from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
from promo_bot.database.models import AffiliateCandidateModel


async def fixture_graph(
    session,
    *,
    now,
    source_id=11,
    proof_id=22,
    link_id=31,
    product_id="12345",
    short_link="https://s.click.aliexpress.com/e/secret",
):
    from promo_bot.database.models import (
        AffiliateLinkProofModel,
        SourceMessageLinkModel,
        SourceMessageModel,
    )

    source = await session.get(SourceMessageModel, source_id)
    if source is None:
        source = SourceMessageModel(
            id=source_id,
            platform="telegram",
            message_id=str(source_id),
            channel_id="-1001234567890",
            occurred_at=now,
            original_text="synthetic",
            links=[],
            content_hash="a" * 64,
            processing_status="COMPLETED",
        )
        session.add(source)
        await session.flush()
    candidate = AffiliateCandidateModel(
        store="aliexpress",
        external_product_id=product_id,
        variation_key="",
        canonical_url=f"https://www.aliexpress.com/item/{product_id}.html",
    )
    session.add(candidate)
    await session.flush()
    proof = AffiliateLinkProofModel(
        id=proof_id,
        candidate_id=candidate.id,
        provider="aliexpress_official",
        operation="aliexpress.affiliate.link.generate",
        requested_at=now,
        responded_at=now,
        source_external_product_id=product_id,
        canonical_url=candidate.canonical_url,
        short_link=short_link,
        official_endpoint_host="api-sg.aliexpress.com",
        credential_profile_id="fixture",
        contract_version="top-link-generate-tracking-v2",
        generation_state="CONFIRMED",
        official_response_validated=True,
        expires_at=now + timedelta(hours=24),
    )
    session.add(proof)
    session.add(
        SourceMessageLinkModel(
            id=link_id,
            source_message_id=source_id,
            ordinal=0 if proof_id in {22, 1} else 1,
            source_kind="TEXT",
            input_hash=f"{link_id:064x}",
            input_url=candidate.canonical_url,
            store="aliexpress",
            external_product_id=product_id,
            canonical_url=candidate.canonical_url,
            affiliate_candidate_id=candidate.id,
            state="PENDING_AFFILIATE",
        )
    )
    await session.flush()
    await fixture_proof_history(session, proof, now=now)
    return proof


async def fixture_proof_history(session, proof, *, now, scope="shadow"):
    history = AffiliateLinkHistoryRepository(session)
    candidate = await session.get(AffiliateCandidateModel, proof.candidate_id)
    token = str(uuid4())
    generation = await history.prepare(
        scope=scope,
        identity_key=f"canonical:{proof.candidate_id}",
        now=proof.requested_at,
        lease_until=now + timedelta(minutes=5),
        lease_token=token,
        call_id=str(uuid4()),
        call_ordinal=0,
        input_fingerprint="a" * 64,
        tracking_fingerprint=proof.tracking_fingerprint or "b" * 64,
        key_fingerprint="c" * 64,
        origin={
            "product_id": proof.source_external_product_id,
            "variation_key": candidate.variation_key,
        },
    )
    proof.tracking_fingerprint = generation.tracking_fingerprint
    proof.promotion_link_type = 0
    await history.start_call((generation.id,), now=proof.requested_at, lease_tokens=(token,))
    await session.refresh(generation)
    await history.confirm(
        generation.id,
        now=proof.responded_at,
        generated_url=proof.short_link,
        expires_at=proof.expires_at,
        contract_version=proof.contract_version,
        correlation_mode="PRODUCT_ID",
        validation_facts={"tracking_exact": True, "promotion_link_validated": True},
    )
    proof.generation_id = generation.id
    return generation.id


async def fixture_preview_history(session, preview, generation_ids, *, now):
    use = await AffiliateLinkHistoryRepository(session).record_use(
        scope="shadow",
        kind="PREVIEW",
        generation_ids=tuple(generation_ids),
        now=now,
        operational_kind="canonical-preview",
        operational_id=preview.id,
        origin={"source_message_id": preview.source_message_id},
    )
    preview.history_use_id = use.id
