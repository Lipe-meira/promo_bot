"""Temporary schema setup ONLY for fake-transport entrypoint fixtures."""

from promo_bot.affiliate.history_context import validate_history_storage
from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.session import create_affiliate_shadow_database
from promo_bot.database.shadow import shadow_database_url
from tests.offline_aliexpress import OfflineSignedAliExpressClient


async def open_offline_shadow(path):
    await upgrade_database_async(shadow_database_url(path))
    database = create_affiliate_shadow_database(path)
    await validate_history_storage(database, real=False)
    return database


def install_offline_shadow_runtime(monkeypatch):
    import promo_bot.cli as cli

    monkeypatch.setattr(cli, "_open_durable_shadow_database", open_offline_shadow)
    monkeypatch.setattr(cli, "AliExpressAffiliateApiClient", OfflineSignedAliExpressClient)
