from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from telethon.tl.types import MessageEntityUrl

from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected
from promo_bot.database.history_models import AffiliateLinkGenerationModel, AffiliateLinkUseModel
from promo_bot.database.history_repository import (
    AffiliateHistoryError,
)
from promo_bot.security.seuhardware_short_links import SeuHardwareShortResolver
from promo_bot.telegram.monitor import _adapt_message
from tests.unit.test_coin_shadow_multi_contract import NOW, A, B, incoming
from tests.unit.test_coin_shadow_multi_generation import setup
from tests.unit.test_coin_shadow_multi_storage import LINKS, TRACKING
from tests.unit.test_seuhardware_listener import V, W
from tests.unit.test_seuhardware_security import Dns, Headers


class ResolutionHeaders(Headers):
    async def fetch(self, url, **kwargs):
        self.response = replace(self.response, headers={"location": {W: A, V: B}[url]})
        return await super().fetch(url, **kwargs)


def attach_resolver(service):
    requester = ResolutionHeaders()
    service.wrapper_resolver = SeuHardwareShortResolver(resolver=Dns(), requester=requester)
    return requester


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.asyncio
async def test_partial_generation_is_preserved_and_failure_blocks_whole_message_and_restart(
    tmp_path, uncertain
):
    database, client, _gen, service, budget = await setup(
        tmp_path, fail_source=B, uncertain=uncertain
    )
    http = attach_resolver(service)
    try:
        with pytest.raises(CoinShadowPreviewRejected):
            await service.prepare(incoming(f"{W}\n{V}"))
        assert client.calls == [A, B] and len(http.calls) == 2
        async with database.session() as session:
            rows = list(await session.scalars(select(AffiliateLinkGenerationModel)))
            assert [row.state for row in rows] == [
                "CONFIRMED",
                "UNCERTAIN" if uncertain else "REJECTED",
            ]
            # No complete preview exists: each attempted generation must still
            # retain which original wrapper/span produced its actual TOP input.
            for row, wrapper, ordinal in zip(rows, (W, V), (0, 1), strict=True):
                observation = row.origin["wrapper_resolutions"][0]
                assert row.origin["wrapper_resolution_version"] == 1
                assert observation["ordinal"] == ordinal
                assert observation["resolved_input_fingerprint"] == row.input_fingerprint
                assert observation["span_end"] - observation["span_start"] == len(wrapper)
                assert observation["http_status"] == 302
                assert observation["validity"] == "MESSAGE_ONLY_NOT_CACHE"
            assert await session.scalar(text("SELECT COUNT(*) FROM affiliate_link_uses")) == 0
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM aliexpress_coin_shadow_multi_previews")
                )
                == 0
            )
        with pytest.raises(CoinShadowPreviewRejected):
            await service.prepare(replace(incoming(f"{W}\n{V}"), message_id=2))
        assert client.calls == [A, B] and budget.send_messages == 0
    finally:
        await database.dispose()


@pytest.mark.parametrize("tracking", [None, "foreign", 1, TRACKING + " "])
@pytest.mark.asyncio
async def test_any_invalid_tracking_prevents_complete_preview_without_repeating_top(
    tmp_path, tracking
):
    database, client, _gen, service, _budget = await setup(tmp_path)
    attach_resolver(service)
    execute = client.execute

    async def response(operation, payload):
        result = await execute(operation, payload)
        if payload["source_values"] == B:
            result["aliexpress_affiliate_link_generate_response"]["resp_result"]["result"][
                "tracking_id"
            ] = tracking
        return result

    client.execute = response
    try:
        with pytest.raises(CoinShadowPreviewRejected):
            await service.prepare(incoming(f"{W}\n{V}"))
        assert client.calls == [A, B]
        async with database.session() as session:
            assert list(await session.scalars(select(AffiliateLinkGenerationModel.state))) == [
                "CONFIRMED",
                "REJECTED",
            ]
            assert await session.scalar(text("SELECT COUNT(*) FROM affiliate_link_uses")) == 0
    finally:
        await database.dispose()


@pytest.mark.parametrize("delta", [0, -1, 1])
@pytest.mark.asyncio
async def test_real_telegram_entity_adapter_preserves_utf16_and_rejects_bad_spans(tmp_path, delta):
    database, client, _gen, service, _budget = await setup(tmp_path)
    http = attach_resolver(service)
    content = f"🔥 APP {W}\r\nPC {B} cupom TESTE — R$ 10"
    entity = MessageEntityUrl(offset=7 + delta, length=len(W))
    raw = SimpleNamespace(
        raw_text=content,
        media=None,
        buttons=None,
        id=1,
        date=NOW,
        get_entities_text=lambda: [(entity, W)],
    )
    message = _adapt_message(raw, "-1001234567890")
    try:
        if delta:
            with pytest.raises(CoinShadowPreviewRejected, match="ALIEXPRESS_COIN_SURFACE_UNSAFE"):
                await service.prepare(message)
            assert http.calls == [] and client.calls == []
        else:
            result = await service.prepare(message)
            assert result.rendered_text == f"🔥 APP {LINKS[A]}\r\nPC {LINKS[B]} cupom TESTE — R$ 10"
            assert len(http.calls) == 1 and client.calls == [A, B]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_wrapper_resolution_is_message_local_even_when_destination_changes(tmp_path):
    database, client, _gen, service, _budget = await setup(tmp_path)
    http = attach_resolver(service)
    try:
        first = await service.prepare(incoming(W))
        http.response = replace(http.response, headers={"location": B})
        # This requester intentionally changes the redirect on the next message.
        service.wrapper_resolver = SeuHardwareShortResolver(
            resolver=Dns(), requester=Headers(location=B)
        )
        second = await service.prepare(replace(incoming(W), message_id=2))
        assert first.rendered_text == LINKS[A] and second.rendered_text == LINKS[B]
        assert client.calls == [A, B]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_cache_partial_and_audit_tampering_cannot_authorize_delivery(tmp_path):
    from promo_bot.database.coin_shadow_multi_repository import CoinShadowMultiPreviewRepository

    database, client, gen, service, budget = await setup(tmp_path, calls=2)
    attach_resolver(service)
    try:
        await gen.generate(A)
        preview = await service.prepare(incoming(f"{W}\n{V}\n{A}"))
        assert client.calls == [A, B] and budget.coin_multi["partial_cache_messages"] == 1
        async with database.session() as session:
            generations = list(await session.scalars(select(AffiliateLinkGenerationModel)))
            assert generations[0].origin is None  # cache is not retroactive evidence
            assert generations[1].origin["wrapper_resolution_version"] == 1
            uses = list(await session.scalars(select(AffiliateLinkUseModel)))
            assert len(uses) == 1
            origin = dict(uses[0].origin)
            rows = [dict(row) for row in origin["wrapper_resolutions"]]
            rows[0]["resolved_input_fingerprint"] = "0" * 64
            uses[0].origin = {**origin, "wrapper_resolutions": rows}
        async with database.session() as session:
            with pytest.raises(
                AffiliateHistoryError, match="AFFILIATE_HISTORY_WRAPPER_MAP_INVALID"
            ):
                await CoinShadowMultiPreviewRepository(session).get_ready(
                    preview.preview_id, now=NOW
                )
        assert budget.send_messages == 0
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_short_claim_never_creates_a_second_top(tmp_path):
    import asyncio

    database, client, _gen, service, _budget = await setup(tmp_path)
    attach_resolver(service)
    entered, release = asyncio.Event(), asyncio.Event()
    execute = client.execute

    async def blocking(operation, payload):
        entered.set()
        await release.wait()
        return await execute(operation, payload)

    client.execute = blocking
    winner = asyncio.create_task(service.prepare(incoming(W)))
    try:
        await entered.wait()
        with pytest.raises(
            CoinShadowPreviewRejected, match="AFFILIATE_HISTORY_GENERATION_IN_PROGRESS"
        ):
            await service.prepare(replace(incoming(W), message_id=2))
        release.set()
        await winner
        assert (await service.prepare(replace(incoming(W), message_id=3))).cache_hit
        assert client.calls == [A]
    finally:
        release.set()
        await asyncio.gather(winner, return_exceptions=True)
        await database.dispose()


@pytest.mark.asyncio
async def test_exhausted_send_budget_rejects_before_wrapper_http(tmp_path):
    database, client, _gen, service, budget = await setup(tmp_path)
    http = attach_resolver(service)
    budget.send_messages = budget.limits.max_send_messages
    try:
        with pytest.raises(CoinShadowPreviewRejected, match="SHADOW_SEND_MESSAGE_LIMIT_REACHED"):
            await service.prepare(incoming(W))
        assert http.calls == [] and client.calls == []
    finally:
        await database.dispose()
