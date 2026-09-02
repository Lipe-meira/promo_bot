"""Predictable Phase 2 templates that never reuse source-channel advertising."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from promo_bot.domain.enums import RelayLinkState
from promo_bot.providers.aliexpress.models import PriceDisplayMode as AliExpressPriceDisplayMode
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


def render_ready_aliexpress_deal(
    *,
    title: str,
    price_min: Decimal,
    price_max: Decimal,
    price_mode: AliExpressPriceDisplayMode,
    affiliate_link: str,
    verified_at: datetime,
    seller: str | None = None,
    shipping_fee: Decimal | None = None,
) -> RenderedMessage:
    """Render only confirmed fields; campaign and coupon claims are intentionally absent."""

    if not title.strip() or not affiliate_link.strip():
        raise ValueError("ready deal requires title and affiliate link")
    if price_min <= 0 or price_max <= 0 or price_min > price_max:
        raise ValueError("ready deal prices must be a positive ordered range")
    if price_mode is AliExpressPriceDisplayMode.EXACT and price_min != price_max:
        raise ValueError("exact price mode requires equal prices")
    if price_mode is AliExpressPriceDisplayMode.RANGE and price_min == price_max:
        raise ValueError("range price mode requires distinct prices")

    formatted_min = _format_brl(price_min)
    if price_mode is AliExpressPriceDisplayMode.RANGE:
        price_line = f"Faixa confirmada: {formatted_min} a {_format_brl(price_max)}"
    elif price_mode is AliExpressPriceDisplayMode.STARTING_AT:
        price_line = f"A partir de: {formatted_min}"
    else:
        price_line = f"Preço: {formatted_min}"
    seller_line = f"\nVendido por: {seller.strip()}" if seller and seller.strip() else ""
    shipping_line = (
        f"\nFrete confirmado: {_format_brl(shipping_fee)}"
        if shipping_fee is not None and shipping_fee >= 0
        else ""
    )
    return RenderedMessage(
        text=(
            "OFERTA ALIEXPRESS\n\n"
            f"{title.strip()}\n"
            f"{price_line}{seller_line}{shipping_line}\n\n"
            f"Verificado em: {verified_at.isoformat()}\n"
            "Preço, frete e estoque podem mudar. "
            "Link de afiliado: posso receber comissão pela compra."
        ),
        button_label="Abrir oferta",
        button_url=affiliate_link,
    )


def _format_brl(value: Decimal) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
