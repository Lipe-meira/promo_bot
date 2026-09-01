"""Predictable Phase 2 templates that never reuse source-channel advertising."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from promo_bot.domain.enums import RelayLinkState
from promo_bot.providers.shopee.policy import PriceDisplayMode

SYNTHETIC_TEST_URL = "https://example.com/promo-bot-test"


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    button_label: str
    button_url: str


def render_synthetic_test() -> RenderedMessage:
    return RenderedMessage(
        text=(
            "✅ TESTE DO PROMO BOT\n\n"
            "Esta é uma mensagem sintética. Nenhuma promoção real foi processada ou publicada."
        ),
        button_label="Abrir oferta",
        button_url=SYNTHETIC_TEST_URL,
    )


def render_candidate_dry_run(
    *, store: str, external_product_id: str, canonical_url: str, state: RelayLinkState
) -> RenderedMessage:
    if state not in {RelayLinkState.PENDING_AFFILIATE, RelayLinkState.MANUAL_REVIEW}:
        raise ValueError("Phase 2 dry-run renders only non-publishable candidates")
    return RenderedMessage(
        text=(
            "🔎 OFERTA IDENTIFICADA — DRY-RUN\n\n"
            f"Loja: {store}\n"
            f"Produto: {external_product_id}\n"
            f"Estado: {state.value}\n\n"
            "Envio bloqueado: ainda não existe link de afiliado oficial."
        ),
        button_label="Abrir oferta",
        button_url=canonical_url,
    )


def render_ready_shopee_deal(
    *,
    title: str,
    price: Decimal,
    price_mode: PriceDisplayMode,
    affiliate_link: str,
    verified_at: datetime,
    seller: str | None = None,
) -> RenderedMessage:
    if not title.strip() or not affiliate_link.strip():
        raise ValueError("ready deal requires title and affiliate link")
    if price <= 0:
        raise ValueError("ready deal price must be positive")
    prefix = "A partir de: " if price_mode is PriceDisplayMode.STARTING_AT else "Preço: "
    formatted_price = f"R$ {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    seller_line = f"\nVendido por: {seller.strip()}" if seller and seller.strip() else ""
    return RenderedMessage(
        text=(
            "OFERTA SHOPEE\n\n"
            f"{title.strip()}\n"
            f"{prefix}{formatted_price}{seller_line}\n\n"
            f"Verificado em: {verified_at.isoformat()}\n"
            "Preço e estoque podem mudar. Link de afiliado: posso receber comissão pela compra."
        ),
        button_label="Abrir oferta",
        button_url=affiliate_link,
    )
