"""Isolated hot-product discovery gateway and response envelope."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from promo_bot.providers.aliexpress.contracts import HOTPRODUCT_QUERY, hotproduct_query_payload
from promo_bot.providers.aliexpress.discovery import (
    DiscoveryPage,
    ProductQueryClient,
    _parse_discovery_page,
)


class AliExpressHotProductGateway:
    def __init__(self, client: ProductQueryClient, *, tracking_id: str) -> None:
        if not tracking_id:
            raise ValueError("ALIEXPRESS_TRACKING_ID_MISSING")
        self._client = client
        self._tracking_id = tracking_id

    async def query_page(
        self,
        *,
        keyword: str,
        category_ids: tuple[str, ...],
        ship_to_country: str,
        target_currency: str,
        target_language: str,
        page_no: int,
        page_size: int,
    ) -> DiscoveryPage:
        payload = hotproduct_query_payload(
            tracking_id=self._tracking_id,
            keywords=keyword,
            category_ids=category_ids,
            ship_to_country=ship_to_country,
            target_currency=target_currency,
            target_language=target_language,
            page_no=page_no,
            page_size=page_size,
        )
        response = await self._client.execute(HOTPRODUCT_QUERY, payload)
        return parse_discovery_hotproduct_query(response)


def parse_discovery_hotproduct_query(payload: Mapping[str, Any]) -> DiscoveryPage:
    return _parse_discovery_page(payload, "aliexpress_affiliate_hotproduct_query_response")
