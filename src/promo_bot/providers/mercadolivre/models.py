"""Dependency-free Mercado Livre identifiers for the offline contract gate."""

from __future__ import annotations

import re
from dataclasses import dataclass

from promo_bot.domain.enums import Store
from promo_bot.stores.urls import canonicalize_store_url

_ITEM_ID = re.compile(r"^MLB[0-9]{5,}$")


@dataclass(frozen=True, slots=True)
class MercadoLivreProductReference:
    external_product_id: str
    canonical_url: str

    def __post_init__(self) -> None:
        if not _ITEM_ID.fullmatch(self.external_product_id):
            raise ValueError("Mercado Livre product ID must use the canonical MLB identity")
        canonical = canonicalize_store_url(self.canonical_url)
        if (
            canonical.store is not Store.MERCADOLIVRE
            or canonical.external_product_id != self.external_product_id
            or canonical.canonical_url != self.canonical_url
        ):
            raise ValueError("Mercado Livre canonical URL does not match the product ID")
