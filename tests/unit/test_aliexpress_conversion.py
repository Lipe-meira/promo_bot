from __future__ import annotations

import asyncio
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
from promo_bot.relay.models import IncomingMessage, MessageSurfaceMetadata
from promo_bot.relay.parser import extract_links
from promo_bot.relay.queue import DurableRelayQueue

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
APP_KEY = "fixture-app-key"
APP_SECRET = "fixture-app-secret"
TRACKING_ID = "fixture-tracking-id"
PRODUCT_ID = "1005000000000001"
CANONICAL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
CANONICAL_WITH_SKU = f"{CANONICAL}?sku_id=120000000000001"
GENERATION_WITH_SKU = f"https://pt.aliexpress.com/item/{PRODUCT_ID}.html?sku_id=120000000000001"
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


def batch_link_response(product_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "code": "0",
        "aliexpress_affiliate_link_generate_response": {
            "resp_result": {
                "result": {
                    "total_result_count": str(len(product_ids)),
                    "promotion_links": [
                        {
                            "promotion_link": f"https://s.click.aliexpress.com/e/result-{product_id}",
                            "source_value": f"https://pt.aliexpress.com/item/{product_id}.html",
                        }
                        for product_id in reversed(product_ids)
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
    contention_wait_seconds: float = 0,
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
        contention_wait_seconds=contention_wait_seconds,
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
        "source_values": GENERATION_WITH_SKU,
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
async def test_multiple_aliexpress_links_use_one_batch_and_preserve_other_stores(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "multi.sqlite3")
    first = CANONICAL
    second_id = "1005000000000002"
    second = f"https://pt.aliexpress.com/item/{second_id}.html?utm_source=old-affiliate"
    amazon = "https://www.amazon.com.br/dp/B0ABCDEFGH"
    original = f"Duas ofertas:\n{first}\n{amazon}\n{second}"
    source_message_id = await persist_and_process(database, 3, original)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=batch_link_response((PRODUCT_ID, second_id)),
            request=request,
        )

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    preview = await service.convert(source_message_id)

    assert len(requests) == 1
    form = dict(parse_qsl(requests[0].content.decode(), keep_blank_values=True))
    assert form["source_values"].split(",") == [
        f"https://pt.aliexpress.com/item/{PRODUCT_ID}.html",
        f"https://pt.aliexpress.com/item/{second_id}.html",
    ]
    assert "utm_source" not in form["source_values"]
    assert preview.replacement_count == 2
    assert preview.converted_text == (
        "Duas ofertas:\n"
        f"https://s.click.aliexpress.com/e/result-{PRODUCT_ID}\n"
        f"{amazon}\n"
        f"https://s.click.aliexpress.com/e/result-{second_id}"
    )
    assert len(preview.correlations) == 2
    assert [item.product_id for item in preview.correlations] == [PRODUCT_ID, second_id]
    assert not preview.all_cache_hit
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_four_unique_aliexpress_products_are_rejected_before_api(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "too-many.sqlite3")
    urls = [f"https://pt.aliexpress.com/item/{1005000000000000 + index}.html" for index in range(4)]
    source_message_id = await persist_and_process(database, 4, "\n".join(urls))

    async def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"API must not be called: {request.method}")

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(forbidden),
        clock=lambda: NOW,
    )
    with pytest.raises(AliExpressConversionRejected, match="ALIEXPRESS_LINK_LIMIT_EXCEEDED"):
        await service.convert(source_message_id)
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(AffiliateLinkProofModel)) == 0
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_automatic_conversion_rejects_hidden_link_surface_before_api(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "unsafe-surface.sqlite3")
    source_message_id = await persist_and_process(database, 6, f"Oferta {CANONICAL}")
    async with database.session() as session:
        source = await session.get(SourceMessageModel, source_message_id)
        assert source is not None
        source.surface_metadata = MessageSurfaceMetadata(has_hidden_links=True).as_dict()

    async def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"API must not be called: {request.method}")

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(forbidden),
        clock=lambda: NOW,
    )
    service.require_safe_surface = True
    with pytest.raises(AliExpressConversionRejected, match="ALIEXPRESS_MESSAGE_SURFACE_UNSAFE"):
        await service.convert(source_message_id)
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_repeated_visible_url_is_generated_once_and_replaced_everywhere(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path, "repeated.sqlite3")
    source_message_id = await persist_and_process(
        database, 5, f"Primeiro {CANONICAL}\nDe novo {CANONICAL}"
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=link_response(), request=request)

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    preview = await service.convert(source_message_id)

    assert calls == 1
    assert preview.replacement_count == 2
    assert preview.converted_text.count(AFFILIATE_LINK) == 2
    assert len(preview.correlations) == 1
    assert preview.correlations[0].occurrence_count == 2
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_distinct_urls_for_same_identity_share_one_proof(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "same-identity.sqlite3")
    alternate = f"https://pt.aliexpress.com/item/{PRODUCT_ID}.html?utm_source=discarded"
    source_message_id = await persist_and_process(
        database,
        7,
        f"Principal {CANONICAL}\nAlternativa {alternate}",
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=link_response(), request=request)

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    preview = await service.convert(source_message_id)

    assert calls == 1
    assert preview.replacement_count == 2
    assert len(preview.correlations) == 2
    assert {item.affiliate_proof_id for item in preview.correlations} == {
        preview.affiliate_proof_id
    }
    assert "utm_source" not in preview.converted_text
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_partial_cache_batches_only_missing_product(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "partial-cache.sqlite3")
    second_id = "1005000000000002"
    first_message = await persist_and_process(database, 8, f"Primeira {CANONICAL}")
    second_message = await persist_and_process(
        database,
        9,
        f"Duas {CANONICAL} https://pt.aliexpress.com/item/{second_id}.html",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        requested = dict(parse_qsl(request.content.decode()))["source_values"]
        product_id = PRODUCT_ID if len(requests) == 1 else second_id
        assert product_id in requested
        return httpx.Response(200, json=link_response(product_id), request=request)

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    await service.convert(first_message)
    preview = await service.convert(second_message)

    assert len(requests) == 2
    second_form = dict(parse_qsl(requests[1].content.decode()))
    assert second_form["source_values"] == (f"https://pt.aliexpress.com/item/{second_id}.html")
    assert [item.cache_hit for item in preview.correlations] == [True, False]
    assert not preview.all_cache_hit
    await http_client.aclose()
    await database.dispose()


@pytest.mark.asyncio
async def test_one_invalid_batch_result_persists_no_proof(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "atomic-batch.sqlite3")
    second_id = "1005000000000002"
    message_id = await persist_and_process(
        database,
        11,
        f"{CANONICAL} https://pt.aliexpress.com/item/{second_id}.html",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = batch_link_response((PRODUCT_ID, second_id))
        links = payload["aliexpress_affiliate_link_generate_response"]
        assert isinstance(links, dict)
        result = links["resp_result"]
        assert isinstance(result, dict)
        body = result["result"]
        assert isinstance(body, dict)
        promotion_links = body["promotion_links"]
        assert isinstance(promotion_links, list)
        promotion_links[0]["promotion_link"] = "https://untrusted.invalid/fixture"
        return httpx.Response(200, json=payload, request=request)

    service, http_client = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: NOW,
    )
    with pytest.raises(AliExpressConversionRejected):
        await service.convert(message_id)
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
async def test_concurrent_messages_for_same_product_share_one_generation(tmp_path: Path) -> None:
    database = await make_database(tmp_path, "concurrent.sqlite3")
    first_id = await persist_and_process(database, 73, f"Primeira {CANONICAL}")
    second_id = await persist_and_process(database, 74, f"Segunda {CANONICAL}")
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        request_started.set()
        await release_request.wait()
        return httpx.Response(200, json=link_response(), request=request)

    service, http = conversion_service(
        database,
        httpx.MockTransport(handler),
        clock=lambda: NOW,
        contention_wait_seconds=1,
    )
    try:
        first_task = asyncio.create_task(service.convert(first_id))
        await request_started.wait()
        second_task = asyncio.create_task(service.convert(second_id))
        await asyncio.sleep(0.1)
        release_request.set()
        first, second = await asyncio.gather(first_task, second_task)

        assert calls == 1
        assert not first.cache_hit
        assert second.cache_hit
        assert first.affiliate_link == second.affiliate_link
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
