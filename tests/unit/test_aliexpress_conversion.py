from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from sqlalchemy import func, select

from promo_bot.affiliate.aliexpress_conversion import (
    ALIEXPRESS_LINK_PROOF_TTL,
    AliExpressConversionRejected,
    AliExpressConversionSafety,
    AliExpressMessageConversionService,
    tracking_config_fingerprint,
)
from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    Base,
    DealModel,
    DeliveryModel,
    SourceMessageModel,
)
from promo_bot.database.session import Database
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.parser import extract_links
from promo_bot.relay.queue import DurableRelayQueue

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
APP_KEY = "fixture-app-key"
APP_SECRET = "fixture-app-secret"
TRACKING_ID = "fixture-tracking-id"
PRODUCT_ID = "1005000000000001"
CANONICAL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
CANONICAL_WITH_SKU = f"{CANONICAL}?sku_id=120000000000001"
AFFILIATE_LINK = "https://s.click.aliexpress.com/e/fixture-result"


async def make_database(tmp_path: Path, name: str) -> Database:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def persist_and_process(database: Database, message_id: int, text: str) -> int:
    relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: NOW)
    persisted = await relay.persist(
        IncomingMessage(
            platform="telegram",
            message_id=message_id,
            channel_id="synthetic-channel",
            occurred_at=NOW,
            original_text=text,
            links=extract_links(text),
        )
    )
    await relay.processor.process(persisted.internal_id)
    return persisted.internal_id


def link_response(product_id: str = PRODUCT_ID) -> dict[str, object]:
    return {
        "code": "0",
        "aliexpress_affiliate_link_generate_response": {
            "resp_result": {
                "result": {
                    "total_result_count": "1",
                    "promotion_links": [
                        {
                            "promotion_link": AFFILIATE_LINK,
                            "source_value": (
                                f"https://pt.aliexpress.com/item/{product_id}.html?spm=normalized"
                            ),
                        }
                    ],
                },
                "resp_code": "200",
                "resp_msg": "success",
            }
        },
        "request_id": "fixture-request",
    }


def conversion_service(
    database: Database,
    handler: httpx.AsyncBaseTransport,
    *,
    clock: Callable[[], datetime],
    tracking_id: str = TRACKING_ID,
) -> tuple[AliExpressMessageConversionService, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(
        transport=handler,
        trust_env=False,
        follow_redirects=False,
    )
    api = AliExpressAffiliateApiClient(
        AliExpressHttpTransport(http_client, max_attempts=1, durable_retry=True),
        request_builder=AliExpressTopRequestBuilder(APP_KEY, APP_SECRET),
        live_enabled=True,
    )
    service = AliExpressMessageConversionService(
        database,
        api,
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        tracking_id=tracking_id,
        safety=AliExpressConversionSafety(
            dry_run=True,
            publish_real_deals=False,
            publish_without_affiliate=False,
            search_enabled=False,
        ),
        clock=clock,
    )
    return service, http_client


@pytest.mark.asyncio
async def test_offline_end_to_end_converts_only_one_aliexpress_link_and_caches_proof(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = await make_database(tmp_path, "conversion.sqlite3")
    source_url = (
        f"https://pt.aliexpress.com/item/{PRODUCT_ID}.html?skuId=120000000000001&utm_source=fixture"
    )
    other_url = "https://www.amazon.com.br/dp/B0ABCDEFGH"
    original = f"Oferta especial: {source_url}\nCompare também: {other_url}"
    source_message_id = await persist_and_process(database, 1, original)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=link_response(), request=request)

    clock_value = [NOW]
    service, http_client = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: clock_value[0],
    )
    caplog.set_level("INFO")

    first = await service.convert(source_message_id)
    second = await service.convert(source_message_id)

    assert len(requests) == 1
    assert requests[0].url.params.get_list("method") == [LINK_GENERATE, LINK_GENERATE]
    form = dict(parse_qsl(requests[0].content.decode("utf-8"), keep_blank_values=True))
    assert form == {
        "promotion_link_type": "0",
        "ship_to_country": "BR",
        "source_values": CANONICAL_WITH_SKU,
        "tracking_id": TRACKING_ID,
    }
    assert first.converted_text == original.replace(source_url, AFFILIATE_LINK)
    assert other_url in first.converted_text
    assert first.affiliate_link == AFFILIATE_LINK
    assert first.variation_key == "sku_id:120000000000001"
    assert first.replacement_count == 1
    assert not first.cache_hit
    assert second.cache_hit
    assert repr(first) == (
        "AliExpressDryRunPreview(source_message_id=1, product_id='1005000000000001', "
        "converted_text=<redacted>, affiliate_link=<redacted>, cache_hit=False)"
    )

    async with database.session() as session:
        source = await session.get(SourceMessageModel, source_message_id)
        proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
        assert source is not None and source.original_text == original
        assert proof.requested_at == NOW
        assert proof.promotion_link_type == 0
        assert proof.expires_at == NOW + ALIEXPRESS_LINK_PROOF_TTL
        assert proof.tracking_fingerprint == tracking_config_fingerprint(
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            tracking_id=TRACKING_ID,
        )
        assert TRACKING_ID not in proof.tracking_fingerprint
        assert len(proof.tracking_fingerprint) == 64
        assert await session.scalar(select(func.count()).select_from(DealModel)) == 0
        assert await session.scalar(select(func.count()).select_from(DeliveryModel)) == 0

    general_logs = caplog.text
    assert original not in general_logs
    assert AFFILIATE_LINK not in general_logs
    assert TRACKING_ID not in general_logs
    assert APP_KEY not in general_logs
    assert APP_SECRET not in general_logs
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_expired_ttl_and_changed_tracking_fingerprint_force_regeneration(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "refresh.sqlite3")
    source_message_id = await persist_and_process(database, 2, f"Oferta {CANONICAL}")
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=link_response(), request=request)

    transport = httpx.MockTransport(handler)
    clock_value = [NOW]
    first_service, first_http = conversion_service(
        database,
        transport,
        clock=lambda: clock_value[0],
    )
    await first_service.convert(source_message_id)
    clock_value[0] = NOW + ALIEXPRESS_LINK_PROOF_TTL - timedelta(seconds=1)
    assert (await first_service.convert(source_message_id)).cache_hit
    clock_value[0] = NOW + ALIEXPRESS_LINK_PROOF_TTL + timedelta(seconds=1)
    assert not (await first_service.convert(source_message_id)).cache_hit

    changed_service, changed_http = conversion_service(
        database,
        transport,
        clock=lambda: clock_value[0],
        tracking_id="different-fixture-tracking",
    )
    assert not (await changed_service.convert(source_message_id)).cache_hit
    assert call_count == 3
    await first_http.aclose()
    await changed_http.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_multiple_aliexpress_links_are_rejected_atomically_without_api_call(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "ambiguous.sqlite3")
    first = CANONICAL
    second = "https://www.aliexpress.com/item/1005000000000002.html"
    source_message_id = await persist_and_process(database, 3, f"Duas ofertas: {first} {second}")

    async def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"API must not be called: {request.method}")

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(forbidden),
        clock=lambda: NOW,
    )
    with pytest.raises(
        AliExpressConversionRejected,
        match="ALIEXPRESS_MULTIPLE_LINKS_AMBIGUOUS",
    ):
        await service.convert(source_message_id)

    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
    await http_client.aclose()
    await database.dispose()


def test_conversion_safety_requires_dry_run_and_closed_publication_gates() -> None:
    with pytest.raises(ValueError, match="ALIEXPRESS_CONVERSION_SAFETY_GATE_CLOSED"):
        AliExpressConversionSafety(
            dry_run=False,
            publish_real_deals=False,
            publish_without_affiliate=False,
            search_enabled=False,
        )
    with pytest.raises(ValueError, match="ALIEXPRESS_CONVERSION_SAFETY_GATE_CLOSED"):
        AliExpressConversionSafety(
            dry_run=True,
            publish_real_deals=True,
            publish_without_affiliate=False,
            search_enabled=False,
        )


@pytest.mark.asyncio
async def test_replacement_preserves_aliexpress_url_embedded_in_another_stores_link(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "boundaries.sqlite3")
    other = f"https://www.amazon.com.br/dp/B0ABCDEFGH?ref={CANONICAL}"
    original = f"Oferta {CANONICAL}\nOutra loja {other}"
    message_id = await persist_and_process(database, 10, original)
    service, http = conversion_service(
        database,
        httpx.MockTransport(lambda request: httpx.Response(200, json=link_response())),
        clock=lambda: NOW,
    )
    try:
        preview = await service.convert(message_id)
        assert preview.converted_text == f"Oferta {AFFILIATE_LINK}\nOutra loja {other}"
        assert preview.replacement_count == 1
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        f"https://www.aliexpress.com/redirect/item/{PRODUCT_ID}.html",
        f"{CANONICAL}/redirect",
        f"{CANONICAL}#another-product",
        f"http://www.aliexpress.com/item/{PRODUCT_ID}.html",
        f"https://m.aliexpress.com/item/{PRODUCT_ID}.html",
        f"https://www.aliexpress.com:invalid/item/{PRODUCT_ID}.html",
        f"https://www.aliexpress.com./item/{PRODUCT_ID}.html",
    ],
)
async def test_noncanonical_paths_and_authorities_are_rejected_before_api(
    tmp_path: Path,
    url: str,
) -> None:
    database = await make_database(tmp_path, "strict-input.sqlite3")
    message_id = await persist_and_process(database, 20, f"Oferta {url}")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=link_response())

    service, http = conversion_service(database, httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        with pytest.raises(AliExpressConversionRejected, match="ALIEXPRESS_CANONICAL_URL_REQUIRED"):
            await service.convert(message_id)
        assert calls == []
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_expired_worker_cannot_finish_a_newer_claim(tmp_path: Path) -> None:
    from promo_bot.database.repositories import AffiliateCandidateRepository

    database = await make_database(tmp_path, "lease-ownership.sqlite3")
    message_id = await persist_and_process(database, 30, f"Oferta {CANONICAL}")
    clock_value = [NOW]

    async def handler(request: httpx.Request) -> httpx.Response:
        clock_value[0] = NOW + timedelta(minutes=6)
        async with database.session() as session:
            candidate = (await session.execute(select(AffiliateCandidateModel))).scalar_one()
            claimed = await AffiliateCandidateRepository(session).claim_for_generation(
                candidate.id,
                now=clock_value[0],
                lease_until=clock_value[0] + timedelta(minutes=5),
                max_attempts=3,
            )
            assert claimed is not None
        return httpx.Response(200, json=link_response())

    service, http = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: clock_value[0],
    )
    try:
        with pytest.raises(AliExpressConversionRejected, match="ALIEXPRESS_GENERATION_LEASE_LOST"):
            await service.convert(message_id)
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
            )
            candidate = (await session.execute(select(AffiliateCandidateModel))).scalar_one()
            assert candidate.state == "GENERATING_AFFILIATE"
            assert candidate.attempt_count == 2
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_transient_failure_uses_durable_backoff_and_exhaustion(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "retry.sqlite3")
    message_id = await persist_and_process(database, 40, f"Oferta {CANONICAL}")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(503, text="untrusted response fixture")

    clock_value = [NOW]
    service, http = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: clock_value[0],
    )
    try:
        for index, minutes in enumerate([0, 2, 5], start=1):
            clock_value[0] = NOW + timedelta(minutes=minutes)
            with pytest.raises(AliExpressConversionRejected, match="ALIEXPRESS_RETRY_EXHAUSTED"):
                await service.convert(message_id)
            with pytest.raises(AliExpressConversionRejected, match="BUSY_OR_EXHAUSTED"):
                await service.convert(message_id)
            assert len(calls) == index
        clock_value[0] = NOW + timedelta(hours=1)
        with pytest.raises(AliExpressConversionRejected, match="BUSY_OR_EXHAUSTED"):
            await service.convert(message_id)
        assert len(calls) == 3
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
            )
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_debug_logging_and_protocol_errors_never_expose_wire_values(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import traceback

    database = await make_database(tmp_path, "safe-errors.sqlite3")
    message_id = await persist_and_process(database, 50, f"Oferta {CANONICAL}")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError(f"{request.url} {request.content!r}")

    service, http = conversion_service(database, httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        caplog.set_level("DEBUG", logger="httpx")
        with pytest.raises(AliExpressConversionRejected) as error:
            await service.convert(message_id)
        rendered = "".join(traceback.format_exception(error.value)) + caplog.text
        for secret in (APP_KEY, APP_SECRET, TRACKING_ID, "sign=", CANONICAL):
            assert secret not in rendered
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalidated_dimension", ["promotion_link_type", "tracking_fingerprint", "expires_at"]
)
async def test_legacy_or_different_promotion_proofs_are_cache_misses(
    tmp_path: Path,
    invalidated_dimension: str,
) -> None:
    database = await make_database(tmp_path, "cache-dimensions.sqlite3")
    message_id = await persist_and_process(database, 60, f"Oferta {CANONICAL}")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=link_response())

    service, http = conversion_service(database, httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        await service.convert(message_id)
        async with database.session() as session:
            proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
            setattr(
                proof,
                invalidated_dimension,
                2 if invalidated_dimension == "promotion_link_type" else None,
            )
        assert not (await service.convert(message_id)).cache_hit
        assert len(calls) == 2
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_separate_messages_deduplicate_but_variations_get_separate_proofs(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "dedup.sqlite3")
    first = await persist_and_process(database, 70, f"Oferta {CANONICAL}")
    second = await persist_and_process(database, 71, f"Nova promoção {CANONICAL}")
    variation = await persist_and_process(database, 72, f"Variação {CANONICAL_WITH_SKU}")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=link_response())

    service, http = conversion_service(database, httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        await service.convert(first)
        assert (await service.convert(second)).cache_hit
        assert not (await service.convert(variation)).cache_hit
        assert len(calls) == 2
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_result", ["product", "host", "scheme", "count"])
async def test_invalid_official_result_fails_atomically_without_proof(
    tmp_path: Path,
    wrong_result: str,
) -> None:
    database = await make_database(tmp_path, "invalid-response.sqlite3")
    message_id = await persist_and_process(database, 80, f"Oferta {CANONICAL}")

    def handler(request: httpx.Request) -> httpx.Response:
        source = (
            CANONICAL if wrong_result != "product" else "https://www.aliexpress.com/item/999.html"
        )
        target = {
            "host": "https://unexpected.invalid/fixture",
            "scheme": "http://s.click.aliexpress.com/e/fixture",
        }.get(wrong_result, AFFILIATE_LINK)
        links = [{"source_value": source, "promotion_link": target}]
        if wrong_result == "count":
            links = []
        return httpx.Response(
            200,
            json={
                "aliexpress_affiliate_link_generate_response": {
                    "resp_result": {"resp_code": "200", "result": {"promotion_links": links}},
                }
            },
        )

    service, http = conversion_service(database, httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        with pytest.raises(AliExpressConversionRejected):
            await service.convert(message_id)
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
            )
            source = await session.get(SourceMessageModel, message_id)
            assert source is not None and source.original_text == f"Oferta {CANONICAL}"
            assert await session.scalar(select(func.count()).select_from(DeliveryModel)) == 0
    finally:
        await http.aclose()
        await database.dispose()


@pytest.mark.asyncio
async def test_unexpected_failure_is_sanitized_for_plain_traceback(tmp_path: Path) -> None:
    import traceback

    database = await make_database(tmp_path, "unexpected.sqlite3")
    message_id = await persist_and_process(database, 90, f"Oferta {CANONICAL}")

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"fixture error {TRACKING_ID} {request.url}")

    service, http = conversion_service(database, httpx.MockTransport(handler), clock=lambda: NOW)
    try:
        with pytest.raises(
            AliExpressConversionRejected, match="ALIEXPRESS_CONVERSION_FAILED"
        ) as error:
            await service.convert(message_id)
        rendered = "".join(traceback.format_exception(error.value))
        assert TRACKING_ID not in rendered
        assert APP_KEY not in rendered
        assert "sign=" not in rendered
    finally:
        await http.aclose()
        await database.dispose()
