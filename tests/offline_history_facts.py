"""Synthetic audit facts for pre-existing isolated delivery fixtures; never real backfill."""

from datetime import timedelta
from uuid import uuid4

from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
from promo_bot.database.models import AffiliateCandidateModel


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
    )
    preview.history_use_id = use.id
