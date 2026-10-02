"""Audited signed requests with temporary SQLite and a physically fake wire."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from promo_bot.affiliate.history_context import AuditedGenerationCall, audited_generation_call
from promo_bot.database.history_repository import AffiliateLinkHistoryRepository
from promo_bot.database.models import Base
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder
from tests.offline_aliexpress import OfflineAliExpressHttpTransport, OfflineSignedAliExpressClient


async def signed_fixture_response(path, transport, payload, *, app_key, app_secret, prepared=None):
    database = create_affiliate_shadow_database(path)
    now, token = datetime.now(UTC), str(uuid4())
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            row = await AffiliateLinkHistoryRepository(session).prepare(
                scope="shadow",
                identity_key="contract-fixture",
                now=now,
                lease_until=now + timedelta(minutes=5),
                lease_token=token,
                call_id=str(uuid4()),
                call_ordinal=0,
                input_fingerprint="a" * 64,
                tracking_fingerprint="b" * 64,
                key_fingerprint="c" * 64,
            )
        call = AuditedGenerationCall(database, (row.id,), (token,), payload, lambda: now)
        with audited_generation_call(call):
            if prepared is not None:
                return await OfflineAliExpressHttpTransport(transport).execute(prepared)
            return await OfflineSignedAliExpressClient(
                transport,
                request_builder=AliExpressTopRequestBuilder(app_key, app_secret),
            ).execute(LINK_GENERATE, payload)
    finally:
        await database.dispose()
