"""Persist only already validated provider DTOs as publishable deals."""

from __future__ import annotations

from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    ProductRepository,
    ShopeeOfferRepository,
)
from promo_bot.database.session import Database
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
