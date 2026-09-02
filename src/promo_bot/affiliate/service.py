"""Persist only already validated provider DTOs as publishable deals."""

from __future__ import annotations

from urllib.parse import urlsplit

from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    AffiliateOfferRepository,
    AliExpressOfferRepository,
    ProductRepository,
    ShopeeOfferRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import Store
from promo_bot.providers.aliexpress.contracts import LINK_GENERATE
from promo_bot.providers.aliexpress.models import (
    EnrichedAffiliateOffer as AliExpressEnrichedAffiliateOffer,
)
from promo_bot.providers.aliexpress.policy import (
    select_publishable_price as select_aliexpress_publishable_price,
)
from promo_bot.providers.aliexpress.policy import (
    validated_official_image_url as validated_aliexpress_image_url,
)
from promo_bot.providers.shopee.models import EnrichedAffiliateOffer
from promo_bot.providers.shopee.policy import (
    select_publishable_price,
    validated_official_image_url,
)


class ShopeeEnrichmentService:
    def __init__(self, database: Database, *, official_image_hosts: frozenset[str]) -> None:
        self.database = database
        self.official_image_hosts = official_image_hosts

    async def persist(self, candidate_id: int, offer: EnrichedAffiliateOffer) -> int:
        product_snapshot = offer.product
        proof = offer.affiliate_proof
        presentation = select_publishable_price(product_snapshot)
        proposed_image = (
            product_snapshot.selected_variation_image_url
            if presentation.variation_id is not None
            else product_snapshot.image_url
        )
        official_image_url = validated_official_image_url(
            proposed_image,
            allowed_hosts=self.official_image_hosts,
        )

        async with self.database.session() as session:
            candidates = AffiliateCandidateRepository(session)
            candidate = await candidates.get(candidate_id)
            if candidate is None or candidate.state != "VALIDATING":
                raise ValueError("affiliate candidate must be claimed before enrichment")
            reference = product_snapshot.reference
            if (
                candidate.external_product_id != reference.external_product_id
                or candidate.canonical_url != reference.canonical_url
            ):
                raise ValueError("provider offer does not belong to the claimed candidate")

            product = await ProductRepository(session).upsert_shopee(
                external_id=reference.external_product_id,
                title=product_snapshot.title,
                canonical_url=reference.canonical_url,
                currency=product_snapshot.currency,
                image_url=official_image_url,
                seller=product_snapshot.seller,
            )
            offers = ShopeeOfferRepository(session)
            stored_proof = await offers.add_proof(
                candidate_id=candidate_id,
                provider=proof.provider,
                operation=proof.operation,
                requested_at=proof.requested_at,
                responded_at=proof.responded_at,
                source_external_product_id=proof.source_external_product_id,
                canonical_url=proof.canonical_url,
                short_link=proof.short_link,
                official_endpoint_host=proof.official_endpoint_host,
                credential_profile_id=proof.credential_profile_id,
                contract_version=proof.contract_version,
                sub_ids=list(proof.sub_ids),
            )
            await offers.add_snapshot(
                candidate_id=candidate_id,
                product_id=product.id,
                shop_id=reference.shop_id,
                item_id=reference.item_id,
                selected_variation_id=product_snapshot.selected_variation_id,
                price_min=product_snapshot.price_min.amount,
                price_max=product_snapshot.price_max.amount,
                selected_price=(
                    product_snapshot.selected_variation_price.amount
                    if product_snapshot.selected_variation_price
                    else None
                ),
                currency=product_snapshot.currency,
                available=product_snapshot.available,
                selected_variation_available=product_snapshot.selected_variation_available,
                range_semantics_confirmed=product_snapshot.range_semantics_confirmed,
                official_image_url=official_image_url,
                queried_at=product_snapshot.queried_at,
            )
            deal = await offers.add_ready_deal(
                product_id=product.id,
                proof_id=stored_proof.id,
                display_price=presentation.price.amount,
                price_min=product_snapshot.price_min.amount,
                price_max=product_snapshot.price_max.amount,
                selected_price=(
                    product_snapshot.selected_variation_price.amount
                    if product_snapshot.selected_variation_price
                    else None
                ),
                price_display_mode=presentation.mode.value,
                variation_id=presentation.variation_id,
                currency=product_snapshot.currency,
                affiliate_link=proof.short_link,
                discovered_at=product_snapshot.queried_at,
            )
            await offers.add_price_history(
                product_id=product.id,
                price=presentation.price.amount,
                currency=product_snapshot.currency,
                collected_at=product_snapshot.queried_at,
            )
            await candidates.mark_enriched(
                candidate_id,
                now=proof.responded_at,
                product_id=product.id,
                deal_id=deal.id,
            )
            return deal.id


class AliExpressEnrichmentService:
    """Persist only evidence tied to one official link-generation response."""

    def __init__(
        self,
        database: Database,
        *,
        official_endpoint_hosts: frozenset[str],
    ) -> None:
        self.database = database
        self.official_endpoint_hosts = frozenset(
            host.strip().casefold() for host in official_endpoint_hosts if host.strip()
        )

    async def persist(self, candidate_id: int, offer: AliExpressEnrichedAffiliateOffer) -> int:
        product_snapshot = offer.product
        proof = offer.affiliate_proof
        presentation = select_aliexpress_publishable_price(product_snapshot)
        official_image_url = validated_aliexpress_image_url(product_snapshot.image_url)
        self._validate_proof(proof.operation, proof.short_link, proof.official_endpoint_host)

        async with self.database.session() as session:
            candidates = AffiliateCandidateRepository(session)
            candidate = await candidates.get(candidate_id)
            if candidate is None or candidate.state != "VALIDATING":
                raise ValueError("affiliate candidate must be claimed before enrichment")
            reference = product_snapshot.reference
            if (
                candidate.store != Store.ALIEXPRESS.value
                or candidate.external_product_id != reference.external_product_id
                or candidate.canonical_url != reference.canonical_url
                or candidate.variation_key != reference.variation_key
            ):
                raise ValueError("provider offer does not belong to the claimed candidate")

            product = await ProductRepository(session).upsert_provider(
                store=Store.ALIEXPRESS,
                external_id=reference.external_product_id,
                title=product_snapshot.title,
                canonical_url=reference.canonical_url,
                currency=product_snapshot.currency,
                image_url=official_image_url,
                seller=product_snapshot.seller,
            )
            offers = AffiliateOfferRepository(session)
            stored_proof = await offers.add_proof(
                candidate_id=candidate_id,
                provider="aliexpress_official",
                operation=proof.operation,
                requested_at=proof.requested_at,
                responded_at=proof.responded_at,
                source_external_product_id=proof.source_external_product_id,
                canonical_url=proof.canonical_url,
                short_link=proof.short_link,
                official_endpoint_host=proof.official_endpoint_host,
                credential_profile_id=proof.credential_profile_id,
                contract_version=proof.contract_version,
                sub_ids=[],
            )
            await AliExpressOfferRepository(session).add_snapshot(
                candidate_id=candidate_id,
                product_id=product.id,
                external_product_id=reference.external_product_id,
                selected_sku_id=product_snapshot.selected_sku_id,
                price_min=product_snapshot.price_min.amount,
                price_max=product_snapshot.price_max.amount,
                selected_price=(
                    product_snapshot.selected_price.amount
                    if product_snapshot.selected_price is not None
                    else None
                ),
                price_scope=product_snapshot.price_scope.value,
                currency=product_snapshot.currency,
                available=product_snapshot.available,
                official_image_url=official_image_url,
                commission_rate=product_snapshot.commission_rate,
                commission_amount=(
                    product_snapshot.commission_amount.amount
                    if product_snapshot.commission_amount is not None
                    else None
                ),
                shipping_fee=(
                    product_snapshot.shipping_fee.amount
                    if product_snapshot.shipping_fee is not None
                    else None
                ),
                source_operation=product_snapshot.source_operation,
                queried_at=product_snapshot.queried_at,
            )
            deal = await offers.add_ready_deal(
                product_id=product.id,
                proof_id=stored_proof.id,
                display_price=presentation.minimum.amount,
                price_min=presentation.minimum.amount,
                price_max=presentation.maximum.amount,
                selected_price=(
                    product_snapshot.selected_price.amount
                    if product_snapshot.selected_price is not None
                    else None
                ),
                price_display_mode=presentation.mode.value,
                variation_id=presentation.selected_sku_id,
                currency=product_snapshot.currency,
                affiliate_link=proof.short_link,
                discovered_at=product_snapshot.queried_at,
                available=product_snapshot.available,
                source="aliexpress_affiliate_api",
            )
            await offers.add_price_history(
                product_id=product.id,
                price=presentation.minimum.amount,
                currency=product_snapshot.currency,
                collected_at=product_snapshot.queried_at,
                source="aliexpress_affiliate_api",
                freight=(
                    product_snapshot.shipping_fee.amount
                    if product_snapshot.shipping_fee is not None
                    else None
                ),
            )
            await candidates.mark_enriched(
                candidate_id,
                now=proof.responded_at,
                product_id=product.id,
                deal_id=deal.id,
            )
            return deal.id

    def _validate_proof(self, operation: str, short_link: str, endpoint_host: str) -> None:
        if operation != LINK_GENERATE:
            raise ValueError("AliExpress affiliate proof must come from link.generate")
        try:
            link_host = urlsplit(short_link).hostname
        except ValueError as exc:
            raise ValueError("AliExpress affiliate link is malformed") from exc
        if link_host != "s.click.aliexpress.com":
            raise ValueError("AliExpress affiliate link host is not officially documented")
        if endpoint_host.strip().casefold() not in self.official_endpoint_hosts:
            raise ValueError("AliExpress endpoint host is not explicitly allowed")
