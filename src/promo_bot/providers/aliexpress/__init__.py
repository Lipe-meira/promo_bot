"""Offline-safe AliExpress Affiliate contracts."""

from promo_bot.providers.aliexpress.client import UnavailableAliExpressAffiliateClient
from promo_bot.providers.aliexpress.provider import AliExpressProvider

__all__ = ["AliExpressProvider", "UnavailableAliExpressAffiliateClient"]
