"""Internal Shopee DTOs independent of GraphQL response shapes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from promo_bot.domain.models import Money, ensure_utc


@dataclass(frozen=True, slots=True)
class ProviderProductReference:
    store: str
    external_product_id: str
    canonical_url: str
    shop_id: str
    item_id: str
    requested_variation_id: str | None = None

    def __post_init__(self) -> None:
        if self.store != "shopee":
            raise ValueError("Shopee reference must use the shopee store")
        if not all(
            value.strip()
            for value in (
                self.external_product_id,
                self.canonical_url,
                self.shop_id,
                self.item_id,
            )
        ):
            raise ValueError("Shopee product identifiers cannot be empty")


@dataclass(frozen=True, slots=True)
class VariationSnapshot:
    variation_id: str
    price: Money
    available: bool
    image_url: str | None = None

    def __post_init__(self) -> None:
        if not self.variation_id.strip():
            raise ValueError("variation ID cannot be empty")


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    reference: ProviderProductReference
    title: str
    price_min: Money
    price_max: Money
    available: bool
    queried_at: datetime
    seller: str | None = None
    image_url: str | None = None
    selected_variation_id: str | None = None
    selected_variation_price: Money | None = None
    selected_variation_available: bool | None = None
    selected_variation_image_url: str | None = None
    range_semantics_confirmed: bool = False
    variations: tuple[VariationSnapshot, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "queried_at", ensure_utc(self.queried_at))
        if not self.title.strip():
            raise ValueError("product title cannot be empty")
        currencies = {
            self.price_min.currency,
            self.price_max.currency,
            *(item.price.currency for item in self.variations),
        }
        if self.selected_variation_price is not None:
            currencies.add(self.selected_variation_price.currency)
        if len(currencies) != 1:
            raise ValueError("snapshot monetary values must use one currency")
        if self.price_min.amount > self.price_max.amount:
            raise ValueError("price_min cannot exceed price_max")
        selected_values = (
            self.selected_variation_id,
            self.selected_variation_price,
            self.selected_variation_available,
        )
        if any(value is not None for value in selected_values) and not all(
            value is not None for value in selected_values
        ):
            raise ValueError("selected variation fields must be supplied together")
        if self.reference.requested_variation_id is not None and (
            self.selected_variation_id != self.reference.requested_variation_id
        ):
            raise ValueError("provider did not confirm the requested variation")
        if self.selected_variation_id is not None and self.variations:
            matches = [
                item for item in self.variations if item.variation_id == self.selected_variation_id
            ]
            if len(matches) != 1:
                raise ValueError("selected variation is absent or duplicated in the snapshot")
            selected = matches[0]
            if (
                selected.price != self.selected_variation_price
                or selected.available != self.selected_variation_available
                or selected.image_url != self.selected_variation_image_url
            ):
                raise ValueError("selected variation data comes from inconsistent snapshots")

    @property
    def currency(self) -> str:
        return self.price_min.currency


@dataclass(frozen=True, slots=True)
class AffiliateLinkProof:
    provider: str
    operation: str
    requested_at: datetime
    responded_at: datetime
    source_external_product_id: str
    canonical_url: str
    short_link: str
    official_endpoint_host: str
    credential_profile_id: str
    contract_version: str
    sub_ids: tuple[str, ...] = ()
    official_response_validated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_at", ensure_utc(self.requested_at))
        object.__setattr__(self, "responded_at", ensure_utc(self.responded_at))
        if self.responded_at < self.requested_at:
            raise ValueError("affiliate response cannot precede the request")
        if self.provider != "shopee_official":
            raise ValueError("affiliate proof must identify the official Shopee provider")
        required = (
            self.operation,
            self.source_external_product_id,
            self.canonical_url,
            self.short_link,
            self.official_endpoint_host,
            self.credential_profile_id,
            self.contract_version,
        )
        if not all(value.strip() for value in required):
            raise ValueError("affiliate proof fields cannot be empty")
        for url in (self.canonical_url, self.short_link):
            try:
                parts = urlsplit(url)
                port = parts.port
            except (TypeError, ValueError) as exc:
                raise ValueError("affiliate proof URL is malformed") from exc
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or port not in {None, 443}
            ):
                raise ValueError("affiliate proof URL must be a safe HTTPS URL")


@dataclass(frozen=True, slots=True)
class EnrichedAffiliateOffer:
    product: ProductSnapshot
    affiliate_proof: AffiliateLinkProof

    def __post_init__(self) -> None:
        if not self.affiliate_proof.official_response_validated:
            raise ValueError("an enriched offer requires a validated official response")
        if (
            self.product.reference.external_product_id
            != self.affiliate_proof.source_external_product_id
        ):
            raise ValueError("affiliate proof belongs to another product")
        if self.product.reference.canonical_url != self.affiliate_proof.canonical_url:
            raise ValueError("affiliate proof belongs to another canonical URL")
