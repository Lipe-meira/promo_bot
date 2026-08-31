"""Shopee provider contracts with the real client blocked by documentation gate."""

from promo_bot.providers.shopee.client import UnavailableShopeeAffiliateClient
from promo_bot.providers.shopee.provider import ShopeeProvider

__all__ = ["ShopeeProvider", "UnavailableShopeeAffiliateClient"]
