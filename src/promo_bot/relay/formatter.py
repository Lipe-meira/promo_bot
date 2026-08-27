"""Predictable Phase 2 templates that never reuse source-channel advertising."""

from __future__ import annotations

from dataclasses import dataclass

from promo_bot.domain.enums import RelayLinkState

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
