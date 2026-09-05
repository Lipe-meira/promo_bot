from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from sqlalchemy import func, select

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionRejected,
    AliExpressConversionSafety,
    AliExpressMessageConversionService,
)
from promo_bot.affiliate.aliexpress_shadow import (
    AliExpressTelegramShadowService,
    resolve_shadow_database_path,
)
from promo_bot.config import EnvironmentSettings
from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import Base, DealModel, DeliveryModel
from promo_bot.database.session import Database
from promo_bot.domain.enums import LinkSource
from promo_bot.observability import configure_logging
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.relay.models import ExtractedLink, IncomingMessage
from promo_bot.relay.parser import extract_links
from promo_bot.telegram.monitor import TelegramMessageReference

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
APP_KEY = "fixture-shadow-key"
APP_SECRET = "fixture-shadow-secret"
TRACKING_ID = "fixture-shadow-tracking"
PRODUCT_ID = "1005000000000001"
CANONICAL = f"https://www.aliexpress.com/item/{PRODUCT_ID}.html"
AFFILIATE_LINK = "https://s.click.aliexpress.com/e/shadow-fixture"
REFERENCE = TelegramMessageReference(message_id=77, chat_id=-1001234567890)


class FakeMessageReader:
    def __init__(self, message: IncomingMessage) -> None:
        self.message = message
        self.references: list[TelegramMessageReference] = []

    async def fetch(self, reference: TelegramMessageReference) -> IncomingMessage:
        self.references.append(reference)
        return self.message


async def make_database(tmp_path: Path, name: str = "shadow.sqlite3") -> Database:
    database = Database(f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def make_message(
    text: str,
    *,
    message_id: int = 77,
    links: tuple[ExtractedLink, ...] | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        platform="telegram",
        message_id=message_id,
        channel_id="-1001234567890",
        occurred_at=NOW,
        original_text=text,
        links=extract_links(text) if links is None else links,
    )


def link_response() -> dict[str, object]:
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
                                f"https://pt.aliexpress.com/item/{PRODUCT_ID}.html?spm=normalized"
                            ),
                        }
                    ],
                },
                "resp_code": "200",
                "resp_msg": "success",
            }
        },
        "request_id": "fixture-shadow-request",
    }


def build_conversion(
    database: Database,
    transport: httpx.AsyncBaseTransport,
) -> tuple[AliExpressMessageConversionService, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    )
    api_client = AliExpressAffiliateApiClient(
        AliExpressHttpTransport(http_client, max_attempts=1, durable_retry=True),
        request_builder=AliExpressTopRequestBuilder(APP_KEY, APP_SECRET),
        live_enabled=True,
    )
    return (
        AliExpressMessageConversionService(
            database,
            api_client,
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            tracking_id=TRACKING_ID,
            safety=AliExpressConversionSafety(
                dry_run=True,
                publish_real_deals=False,
                publish_without_affiliate=False,
                search_enabled=False,
            ),
            clock=lambda: NOW,
        ),
        http_client,
    )


@pytest.mark.asyncio
async def test_shadow_preview_reads_one_message_generates_once_and_reuses_cache(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = await make_database(tmp_path)
    amazon = "https://www.amazon.com.br/dp/B0ABCDEFGH"
    amazon_short = "https://amzn.to/fixture"
    original = f"Oferta {CANONICAL}\nCompare {amazon}\nAtalho {amazon_short}"
    reader = FakeMessageReader(make_message(original))
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=link_response(), request=request)

    conversion, http_client = build_conversion(database, httpx.MockTransport(handler))
    service = AliExpressTelegramShadowService(
        database,
        reader,
        conversion,
        relay_config=TelegramRelayConfig(),
        clock=lambda: NOW,
    )
    configure_logging("INFO")
    try:
        first = await service.preview(REFERENCE)
        second = await service.preview(REFERENCE)

        assert reader.references == [REFERENCE, REFERENCE]
        assert len(requests) == 1
        assert requests[0].url.params.get_list("method") == [LINK_GENERATE, LINK_GENERATE]
        form = dict(parse_qsl(requests[0].content.decode(), keep_blank_values=True))
        assert form["source_values"] == CANONICAL
        assert first.converted_text == original.replace(CANONICAL, AFFILIATE_LINK)
        assert amazon in first.converted_text
        assert amazon_short in first.converted_text
        assert first.replacement_count == 1
        assert not first.cache_hit
        assert second.cache_hit
        assert second.converted_text == first.converted_text
        logs = capsys.readouterr().err
        assert "shadow-fixture" not in logs
        assert original not in logs
        assert CANONICAL not in logs
        assert amazon not in logs
        assert TRACKING_ID not in logs

        async with database.session() as session:
            deal_count = await session.scalar(select(func.count()).select_from(DealModel))
            delivery_count = await session.scalar(select(func.count()).select_from(DeliveryModel))
        assert deal_count == 0
        assert delivery_count == 0
    finally:
        await http_client.aclose()
        await database.dispose()


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (
            make_message("Oferta https://a.aliexpress.com/_fixture"),
            "ALIEXPRESS_SHORT_URL_UNSUPPORTED",
        ),
        (
            make_message(
                "Oferta sem URL visível",
                links=(ExtractedLink(CANONICAL, LinkSource.ENTITY_TEXT_URL, 0),),
            ),
            "ALIEXPRESS_TEXT_LINK_REQUIRED",
        ),
        (
            make_message(
                "Oferta sem URL visível",
                links=(ExtractedLink(CANONICAL, LinkSource.BUTTON, 0),),
            ),
            "ALIEXPRESS_TEXT_LINK_REQUIRED",
        ),
        (
            make_message(f"Duas {CANONICAL} https://www.aliexpress.com/item/999999.html"),
            "ALIEXPRESS_MULTIPLE_LINKS_AMBIGUOUS",
        ),
    ],
)
@pytest.mark.asyncio
async def test_shadow_preview_rejects_unsupported_aliexpress_surfaces_before_generation(
    tmp_path: Path,
    message: IncomingMessage,
    reason: str,
) -> None:
    database = await make_database(tmp_path, f"{message.message_id}-{reason}.sqlite3")

    async def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"AliExpress must not be called: {request.method}")

    conversion, http_client = build_conversion(database, httpx.MockTransport(forbidden))
    service = AliExpressTelegramShadowService(
        database,
        FakeMessageReader(message),
        conversion,
        relay_config=TelegramRelayConfig(),
        clock=lambda: NOW,
    )
    try:
        with pytest.raises(AliExpressConversionRejected, match=reason):
            await service.preview(
                TelegramMessageReference(
                    message_id=message.message_id,
                    chat_id=-1001234567890,
                )
            )
    finally:
        await http_client.aclose()
        await database.dispose()


def test_shadow_database_defaults_outside_main_database_and_rejects_workspace_paths(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    main_database = tmp_path / "main.sqlite3"
    settings = EnvironmentSettings(
        _env_file=None,
        PROMO_BOT_RUNTIME_DIR=runtime,
        PROMO_BOT_DATABASE_URL=f"sqlite+aiosqlite:///{main_database.as_posix()}",
    )

    shadow = resolve_shadow_database_path(settings)

    assert shadow == (runtime / "shadow" / "aliexpress-shadow.sqlite3").resolve()
    assert shadow != main_database.resolve()
    repository_path = Path(__file__).resolve().parents[2] / "shadow.sqlite3"
    with pytest.raises(ValueError, match="ALIEXPRESS_SHADOW_DATABASE_MUST_BE_EXTERNAL"):
        resolve_shadow_database_path(settings, repository_path)
