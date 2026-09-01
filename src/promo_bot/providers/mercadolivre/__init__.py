"""Offline-safe Mercado Livre provider contracts."""

from promo_bot.providers.mercadolivre.browser import (
    GeneratedAffiliateLink,
    GeneratorUiContract,
    PlaywrightLinkGeneratorAdapter,
)
from promo_bot.providers.mercadolivre.models import MercadoLivreProductReference
from promo_bot.providers.mercadolivre.policy import validate_affiliate_link
from promo_bot.providers.mercadolivre.profile_lock import (
    BrowserProfileInUse,
    BrowserProfileLock,
    ensure_profile_outside_workspace,
)

__all__ = [
    "BrowserProfileInUse",
    "BrowserProfileLock",
    "GeneratedAffiliateLink",
    "GeneratorUiContract",
    "MercadoLivreProductReference",
    "PlaywrightLinkGeneratorAdapter",
    "ensure_profile_outside_workspace",
    "validate_affiliate_link",
]
