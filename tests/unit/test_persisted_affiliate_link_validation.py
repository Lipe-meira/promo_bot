"""T2.1: persisted affiliate links are revalidated before they can reach a transport.

Links stored before the T2 hardening may be malformed. Every case below tampers the *whole*
persisted chain (proof, preview, rendered text and history generation) so that the pre-existing
equality checks still agree, and only the link-shape contract can stop the delivery.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from promo_bot.affiliate.service import AliExpressEnrichmentService
from promo_bot.database.history_models import AffiliateLinkGenerationModel
from promo_bot.database.history_repository import AffiliateHistoryError
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    AffiliateShadowPreviewLinkModel,
    AffiliateShadowPreviewModel,
    AliExpressCoinShadowDeliveryModel,
    AliExpressCoinShadowEvidenceModel,
    AliExpressCoinShadowPreviewModel,
    DealModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.providers.aliexpress.models import AffiliateLinkProof
from tests.unit.test_aliexpress_enrichment import claimed_candidate, make_database, offer
from tests.unit.test_coin_shadow_delivery import make_stack
from tests.unit.test_shadow_delivery import LINK, NOW, FakeTransport, deliver, seed

BAD_LINKS = {
    "lf": "https://s.click.aliexpress.com/e/bad\nhttps://evil.example/x",
    "crlf": "https://s.click.aliexpress.com/e/bad\r\nX-Header: 1",
    "trailing-lf": "https://s.click.aliexpress.com/e/bad\n",
    "tab": "https://s.click.aliexpress.com/e/ba\td",
    "space": "https://s.click.aliexpress.com/e/bad link",
    "trailing-space": "https://s.click.aliexpress.com/e/bad ",
    "nul": "https://s.click.aliexpress.com/e/ba\x00d",
    "bidi": "https://s.click.aliexpress.com/e/ba‮d",
    "zero-width": "https://s.click.aliexpress.com/e/ba​d",
    "credentials": "https://user:pw@s.click.aliexpress.com/e/bad",
    "http": "http://s.click.aliexpress.com/e/bad",
    "port": "https://s.click.aliexpress.com:8443/e/bad",
    "bad-port": "https://s.click.aliexpress.com:99999/e/bad",
    "fragment": "https://s.click.aliexpress.com/e/bad#frag",
    "root-only": "https://s.click.aliexpress.com/",
    "bad-escape": "https://s.click.aliexpress.com/e/b%zzad",
    "other-host": "https://s.click.aliexpress.com.evil.example/e/bad",
    "too-long": "https://s.click.aliexpress.com/e/" + "a" * 2100,
}


async def _tamper_single_chain(database, preview_id: int, bad: str) -> None:
    async with database.session() as session:
        proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
        preview = await session.get(AffiliateShadowPreviewModel, preview_id)
        assert preview is not None and preview.rendered_text is not None
        generation = await session.get(AffiliateLinkGenerationModel, proof.generation_id)
        assert generation is not None
        preview.rendered_text = preview.rendered_text.replace(LINK, bad)
        preview.affiliate_link = bad
        proof.short_link = bad
        generation.generated_url = bad


@pytest.mark.parametrize("bad", list(BAD_LINKS.values()), ids=list(BAD_LINKS))
@pytest.mark.asyncio
async def test_malformed_persisted_single_link_never_reaches_transport(
    tmp_path: Path, bad: str
) -> None:
    path = tmp_path / "persisted-single.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    try:
        await _tamper_single_chain(database, preview_id, bad)
        report = await deliver(database, transport, preview_id)

        assert report["status"] == "failed_safe"
        assert report["error_code"] == "SHADOW_LINK_INVALID"
        assert report["get_chat_attempts"] == report["send_message_attempts"] == 0
        assert transport.gets == transport.sends == []
        assert bad not in repr(report)
        again = await deliver(database, transport, preview_id)
        assert again["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
        assert transport.sends == []
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_legitimate_persisted_link_with_tracking_parameters_is_preserved(
    tmp_path: Path,
) -> None:
    good = "https://s.click.aliexpress.com/e/_DdAbc12?af=1&sk=a%20b&utm_source=x"
    path = tmp_path / "persisted-good.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport(database)
    try:
        await _tamper_single_chain(database, preview_id, good)
        report = await deliver(database, transport, preview_id)

        assert report["status"] == "sent"
        assert [text for _chat, text in transport.sends] == [
            f"Oferta **literal** <b>😀</b>\n{good}\nhttps://amazon.com.br/item/test"
        ]
    finally:
        await database.dispose()


@pytest.mark.parametrize("which", ["first", "second"])
@pytest.mark.parametrize(
    "bad",
    [BAD_LINKS["lf"], BAD_LINKS["space"], BAD_LINKS["bidi"], BAD_LINKS["credentials"]],
    ids=["lf", "space", "bidi", "credentials"],
)
@pytest.mark.asyncio
async def test_malformed_link_in_multi_link_preview_blocks_the_whole_delivery(
    tmp_path: Path, which: str, bad: str
) -> None:
    path = tmp_path / "persisted-multi.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    second_good = "https://s.click.aliexpress.com/e/second-fixture"
    try:
        async with database.session() as session:
            source = (await session.execute(select(SourceMessageModel))).scalar_one()
            first_link = (await session.execute(select(SourceMessageLinkModel))).scalar_one()
            first_proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
            first_generation = await session.get(
                AffiliateLinkGenerationModel, first_proof.generation_id
            )
            preview = await session.get(AffiliateShadowPreviewModel, preview_id)
            assert preview is not None and first_generation is not None
            second_candidate = AffiliateCandidateModel(
                store="aliexpress",
                external_product_id="67890",
                variation_key="",
                canonical_url="https://www.aliexpress.com/item/67890.html",
            )
            session.add(second_candidate)
            await session.flush()
            second_link = SourceMessageLinkModel(
                source_message_id=source.id,
                ordinal=1,
                source_kind="TEXT",
                input_hash="second-fixture",
                input_url=second_candidate.canonical_url,
                store="aliexpress",
                external_product_id="67890",
                canonical_url=second_candidate.canonical_url,
                affiliate_candidate_id=second_candidate.id,
                state="PENDING_AFFILIATE",
            )
            session.add(second_link)
            first_url, second_url = (bad, second_good) if which == "first" else (LINK, bad)
            second_proof = AffiliateLinkProofModel(
                candidate_id=second_candidate.id,
                provider="aliexpress_official",
                operation="aliexpress.affiliate.link.generate",
                requested_at=NOW,
                responded_at=NOW,
                source_external_product_id="67890",
                canonical_url=second_candidate.canonical_url,
                short_link=second_url,
                official_endpoint_host="api-sg.aliexpress.com",
                credential_profile_id="configured",
                contract_version="top-link-generate-tracking-v2",
                generation_state="CONFIRMED",
                official_response_validated=True,
                expires_at=NOW + timedelta(hours=24),
            )
            session.add(second_proof)
            await session.flush()
            first_proof.short_link = first_url
            first_generation.generated_url = first_url
            preview.affiliate_link = first_url
            source.original_text = f"Oferta\n{first_link.input_url}\n{second_link.input_url}"
            preview.rendered_text = f"Oferta\n{first_url}\n{second_url}"
            preview.replacement_count = 2
            session.add_all(
                [
                    AffiliateShadowPreviewLinkModel(
                        preview_id=preview.id,
                        source_message_link_id=first_link.id,
                        affiliate_proof_id=first_proof.id,
                        ordinal=0,
                        occurrence_count=1,
                        cache_hit=False,
                    ),
                    AffiliateShadowPreviewLinkModel(
                        preview_id=preview.id,
                        source_message_link_id=second_link.id,
                        affiliate_proof_id=second_proof.id,
                        ordinal=1,
                        occurrence_count=1,
                        cache_hit=False,
                    ),
                ]
            )

        report = await deliver(database, transport, preview_id)

        assert report["status"] == "failed_safe"
        assert report["error_code"] == "SHADOW_LINK_INVALID"
        assert transport.gets == transport.sends == []
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "bad",
    [BAD_LINKS["lf"], BAD_LINKS["space"], BAD_LINKS["bidi"], BAD_LINKS["credentials"]],
    ids=["lf", "space", "bidi", "credentials"],
)
@pytest.mark.asyncio
async def test_malformed_persisted_coin_shadow_link_never_reaches_transport(
    tmp_path: Path, bad: str
) -> None:
    database, _client, _service, preview, transport, delivery = await make_stack(tmp_path)
    try:
        async with database.session() as session:
            row = await session.get(AliExpressCoinShadowPreviewModel, preview.preview_id)
            assert row is not None
            evidence = await session.get(AliExpressCoinShadowEvidenceModel, row.evidence_id)
            assert evidence is not None and evidence.promotion_link is not None
            generation = await session.get(AffiliateLinkGenerationModel, evidence.generation_id)
            assert generation is not None
            row.rendered_text = row.rendered_text.replace(evidence.promotion_link, bad)
            generation.generated_url = bad
            evidence.promotion_link = bad

        with pytest.raises(AffiliateHistoryError, match="AFFILIATE_HISTORY_LINK_INVALID"):
            await delivery.deliver(preview.preview_id, "private-test")

        assert transport.inspections == transport.sends == []
        async with database.session() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(AliExpressCoinShadowDeliveryModel)
                )
                == 0
            )
    finally:
        await database.dispose()


@pytest.mark.parametrize("bad", list(BAD_LINKS.values()), ids=list(BAD_LINKS))
@pytest.mark.asyncio
async def test_enrichment_rejects_malformed_link_without_persisting(
    tmp_path: Path, bad: str
) -> None:
    database = await make_database(tmp_path, "persist-bad-link.sqlite3")
    candidate_id = await claimed_candidate(database)
    good = offer()
    proof = good.affiliate_proof
    with pytest.raises(ValueError) as excinfo:
        # The DTO itself already refuses some shapes (credentials, http, ports); the service must
        # refuse the rest, so both layers sit inside the same expectation.
        tampered = AffiliateLinkProof(
            operation=proof.operation,
            requested_at=proof.requested_at,
            responded_at=proof.responded_at,
            source_external_product_id=proof.source_external_product_id,
            canonical_url=proof.canonical_url,
            short_link=bad,
            official_endpoint_host=proof.official_endpoint_host,
            credential_profile_id=proof.credential_profile_id,
            contract_version=proof.contract_version,
            official_response_validated=True,
        )
        await AliExpressEnrichmentService(
            database, official_endpoint_hosts=frozenset({"api-sg.aliexpress.com"})
        ).persist(candidate_id, replace(good, affiliate_proof=tampered))

    assert bad not in str(excinfo.value)
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(DealModel)) == 0
        assert await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
    await database.dispose()
