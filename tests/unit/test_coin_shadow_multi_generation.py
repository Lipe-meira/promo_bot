from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from promo_bot.affiliate.aliexpress_shadow_listener import ShadowRunController, ShadowRunLimits
from promo_bot.affiliate.coin_shadow_generation import CoinShadowGenerationService
from promo_bot.affiliate.coin_shadow_preview import CoinShadowPreviewRejected
from promo_bot.database.history_models import AffiliateLinkGenerationModel, AffiliateLinkUseModel
from promo_bot.database.models import Base
from promo_bot.database.session import create_affiliate_shadow_database
from tests.unit.test_coin_shadow_multi_contract import NOW, A, B, incoming
from tests.unit.test_coin_shadow_multi_storage import LINKS, SECRET, TRACKING, Client


class BudgetClient(Client):
    def __init__(self, budget, *, fail_source=None, uncertain=False):
        super().__init__()
        self.budget, self.fail_source, self.uncertain = budget, fail_source, uncertain

    async def execute(self, operation, payload):
        from promo_bot.affiliate.history_context import CURRENT_GENERATION_CALL

        await self.budget.before_api_call()
        await CURRENT_GENERATION_CALL.get().before_network()
        if payload["source_values"] == self.fail_source:
            self.calls.append(self.fail_source)
            if self.uncertain:
                raise TimeoutError()
            return {
                "code": "0",
                "aliexpress_affiliate_link_generate_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "result": {"promotion_links": [], "tracking_id": TRACKING},
                    }
                },
            }
        return await super().execute(operation, payload)


async def setup(tmp_path, *, calls=3, fail_source=None, uncertain=False):
    from promo_bot.affiliate.coin_shadow_multi import CoinShadowMultiPreview

    database = create_affiliate_shadow_database(tmp_path / "generation.sqlite3")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    budget = ShadowRunController(ShadowRunLimits(10, 60, calls, 5))
    budget.enable_coin_multi()
    client = BudgetClient(budget, fail_source=fail_source, uncertain=uncertain)
    gen = CoinShadowGenerationService(
        database,
        client,
        app_secret=SECRET,
        tracking_id=TRACKING,
        clock=lambda: NOW,
        observer_wait_seconds=0,
    )
    service = CoinShadowMultiPreview(
        database, gen, app_secret=SECRET, budget=budget, max_occurrences=3, clock=lambda: NOW
    )
    return database, client, gen, service, budget


@pytest.mark.asyncio
async def test_partial_and_total_cache_replace_all_spans_and_record_per_generation_cache(tmp_path):
    database, client, gen, service, budget = await setup(tmp_path)
    try:
        await gen.generate(A)
        content = f"🔥 APP {A}\r\nPC {B}\nRepetido {A}. Cupom XYZ  R$ 10"
        result = await service.prepare(incoming(content))
        assert client.calls == [A, B]
        assert (
            result.rendered_text
            == f"🔥 APP {LINKS[A]}\r\nPC {LINKS[B]}\nRepetido {LINKS[A]}. Cupom XYZ  R$ 10"
        )
        assert result.replacement_count == 3 and not result.cache_hit
        assert budget.coin_multi == {
            "occurrences_admitted": 3,
            "distinct_inputs_admitted": 2,
            "cache_distinct_inputs": 1,
            "generated_distinct_inputs_confirmed": 1,
            "in_message_reuses": 1,
            "all_cache_messages": 0,
            "partial_cache_messages": 1,
        }
        from dataclasses import replace

        next_message = replace(incoming(f"{A}\n{B}"), message_id=2)
        result2 = await service.prepare(next_message)
        assert result2.cache_hit and len(client.calls) == 2
        assert budget.coin_multi["all_cache_messages"] == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_observer_exits_without_polling_and_later_manual_message_uses_cache(
    tmp_path,
):
    import asyncio
    from dataclasses import replace

    database, client, _, service, _ = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original_execute = client.execute

    async def blocked_execute(operation, payload):
        entered.set()
        await release.wait()
        return await original_execute(operation, payload)

    client.execute = blocked_execute
    task = asyncio.create_task(service.prepare(incoming(f"{A}\n{A}")))
    try:
        await entered.wait()
        with pytest.raises(
            CoinShadowPreviewRejected, match="AFFILIATE_HISTORY_GENERATION_IN_PROGRESS"
        ):
            await asyncio.wait_for(
                service.prepare(replace(incoming(f"{A}\n{A}"), message_id=2)), timeout=0.5
            )
        release.set()
        await task
        later = await service.prepare(replace(incoming(f"{A}\n{A}"), message_id=3))
        assert later.cache_hit and client.calls == [A]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await database.dispose()


@pytest.mark.asyncio
async def test_cache_expires_after_probe_partial_confirmation_is_kept_without_exceeding_budget(
    tmp_path,
):
    database, client, gen, service, budget = await setup(tmp_path, calls=2)
    now = [NOW - timedelta(hours=24) + timedelta(seconds=1)]
    gen.clock = lambda: now[0]
    service.clock = lambda: now[0]
    try:
        await gen.generate(B)
        now[0] = NOW
        original_execute = client.execute

        async def move_clock(operation, payload):
            result = await original_execute(operation, payload)
            now[0] += timedelta(seconds=2)
            return result

        client.execute = move_clock
        with pytest.raises(
            CoinShadowPreviewRejected, match="ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT"
        ):
            await service.prepare(incoming(f"{A}\n{B}"))
        assert client.calls == [B, A] and budget.api_calls == 2
        assert (await gen.inspect(A)).state == "READY"
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AffiliateLinkUseModel)) == 0
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_known_insufficient_budget_rejects_before_any_claim_or_call(tmp_path):
    database, client, _, service, budget = await setup(tmp_path, calls=1)
    try:
        with pytest.raises(
            CoinShadowPreviewRejected, match="ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT"
        ):
            await service.prepare(incoming(f"{A}\n{B}"))
        assert client.calls == [] and budget.api_calls == 0
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AffiliateLinkGenerationModel))
                == 0
            )
            assert (
                await session.scalar(text("SELECT COUNT(*) FROM aliexpress_coin_shadow_evidence"))
                == 0
            )
    finally:
        await database.dispose()


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.asyncio
async def test_prefix_confirmation_survives_failed_later_input_and_restart_never_retries(
    tmp_path, uncertain
):
    database, client, gen, service, budget = await setup(
        tmp_path, fail_source=B, uncertain=uncertain
    )
    try:
        with pytest.raises(CoinShadowPreviewRejected):
            await service.prepare(incoming(f"{A}\n{B}"))
        async with database.session() as session:
            rows = list(
                await session.scalars(
                    select(AffiliateLinkGenerationModel).order_by(
                        AffiliateLinkGenerationModel.call_started_at
                    )
                )
            )
            assert sorted(row.state for row in rows) == sorted(
                ["CONFIRMED", "UNCERTAIN" if uncertain else "REJECTED"]
            )
            assert (
                await session.scalar(select(func.count()).select_from(AffiliateLinkUseModel)) == 0
            )
        assert budget.coin_multi["generated_distinct_inputs_confirmed"] == 1
        with pytest.raises(CoinShadowPreviewRejected):
            await service.prepare(incoming(f"{A}\n{B}"))
        assert len(client.calls) == 2
        assert (await gen.inspect(A)).cache_hit
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_probe_is_read_only_and_expired_cache_is_not_current_evidence(tmp_path):
    database, client, gen, _, _ = await setup(tmp_path)
    try:
        assert await gen.inspect(A) is None
        ready = await gen.generate(A)
        assert (await gen.inspect(A)).generation_id == ready.generation_id
        gen.clock = lambda: NOW + timedelta(hours=25)
        assert await gen.inspect(A) is None
        async with database.session() as session:
            assert (
                await session.scalar(text("SELECT COUNT(*) FROM aliexpress_coin_shadow_evidence"))
                == 1
            )
        assert client.calls == [A]
    finally:
        await database.dispose()
