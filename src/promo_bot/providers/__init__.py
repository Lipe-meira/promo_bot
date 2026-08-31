"""Provider contracts; concrete network adapters remain explicitly gated."""

from promo_bot.providers.base import ProviderError, StoreProvider

__all__ = ["ProviderError", "StoreProvider"]
