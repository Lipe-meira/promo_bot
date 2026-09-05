"""Explicit synthetic end-to-end demonstration; no credentials, network or durable DB."""

from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx

from promo_bot.affiliate.aliexpress_conversion import (
    AliExpressConversionSafety,
    AliExpressMessageConversionService,
)
from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.models import Base
from promo_bot.database.session import Database
from promo_bot.providers.aliexpress.client import AliExpressAffiliateApiClient
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport
from promo_bot.relay.models import IncomingMessage
from promo_bot.relay.parser import extract_links
from promo_bot.relay.queue import DurableRelayQueue


async def run_offline_conversion_demo() -> dict[str, object]:
    """Exercise the production relay/converter using an in-memory DB and MockTransport."""
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    original = (
        "DEMONSTRAÇÃO SINTÉTICA — preço de exemplo R$ 99,90\n"
        "https://www.aliexpress.com/item/1005000000000001.html\n"
        "Outra loja: https://www.amazon.com.br/dp/B0ABCDEFGH"
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.params.get_list("method") != [LINK_GENERATE, LINK_GENERATE]:
            raise RuntimeError("OFFLINE_DEMO_UNEXPECTED_OPERATION")
        form = parse_qs(request.content.decode())
        return httpx.Response(
            200,
            json={
                "aliexpress_affiliate_link_generate_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "resp_msg": "synthetic fixture",
                        "result": {
                            "promotion_links": [
                                {
                                    "source_value": form["source_values"][0],
                                    "promotion_link": "https://s.click.aliexpress.com/e/offline-demo",
                                }
                            ]
                        },
                    },
                },
            },
        )

    database = Database("sqlite+aiosqlite:///:memory:")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        relay = DurableRelayQueue(database, TelegramRelayConfig(), clock=lambda: now)
        message = IncomingMessage(
            "telegram", 1, "synthetic", now, original, extract_links(original)
        )
        persisted = await relay.persist(message)
        await relay.processor.process(persisted.internal_id)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ) as http:
            api = AliExpressAffiliateApiClient(
                AliExpressHttpTransport(http, max_attempts=1, durable_retry=True),
                request_builder=AliExpressTopRequestBuilder("demo-key", "demo-secret"),
                live_enabled=True,  # Authorizes the injected MockTransport only.
            )
            converter = AliExpressMessageConversionService(
                database,
                api,
                app_key="demo-key",
                app_secret="demo-secret",
                tracking_id="demo-tracking",
                clock=lambda: now,
                safety=AliExpressConversionSafety(True, False, False, False),
            )
            preview = await converter.convert(persisted.internal_id)
            duplicate = await converter.convert(persisted.internal_id)
            return {
                **preview.explicit_output(),
                "synthetic": True,
                "evidence_source": "MockTransport",
                "network_call": False,
                "database": "ephemeral_in_memory",
                "mock_request_count": calls,
                "duplicate_cache_hit": duplicate.cache_hit,
            }
    finally:
        await database.dispose()
