"""What a workspace can do today, stated honestly.

Nothing here is read from configuration that a user could flip: a capability becomes available
only when the code that implements it ships. Until then the reason is explicit, so the web app
can show a feature as unavailable instead of faking it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from promo_api.schemas import (
    CapabilitiesOut,
    Capability,
    ComponentStatus,
    WorkspaceStatusOut,
)

_CAPABILITIES: tuple[Capability, ...] = (
    Capability(key="workspace_settings", title="Configurações do workspace", available=True),
    Capability(
        key="telegram_connection",
        title="Conexão com o Telegram",
        available=False,
        reason_code="REQUIRES_SECRET_VAULT",
        reason="O login por QR e o armazenamento seguro de sessões ainda não foram implementados.",
    ),
    Capability(
        key="channel_selection",
        title="Canais de origem e destino",
        available=False,
        reason_code="REQUIRES_TELEGRAM_CONNECTION",
        reason="Depende de uma conexão com o Telegram, ainda indisponível.",
    ),
    Capability(
        key="aliexpress_credentials",
        title="Credenciais AliExpress",
        available=False,
        reason_code="REQUIRES_SECRET_VAULT",
        reason="Nenhuma credencial é aceita antes do cofre de segredos existir.",
    ),
    Capability(
        key="relay_automation",
        title="Clonagem automática de promoções",
        available=False,
        reason_code="REQUIRES_WORKER_SUPERVISOR",
        reason="Os workers hospedados por workspace ainda não foram implementados.",
    ),
    Capability(
        key="promotion_history",
        title="Histórico de promoções",
        available=False,
        reason_code="REQUIRES_WORKER_SUPERVISOR",
        reason="Só existirá quando um worker real registrar promoções deste workspace.",
    ),
    Capability(
        key="automatic_publishing",
        title="Publicação automática",
        available=False,
        reason_code="REQUIRES_EXPLICIT_AUTHORIZATION",
        reason="Permanece desabilitada até validação e autorização explícitas.",
    ),
)


def workspace_capabilities(workspace_id: UUID) -> CapabilitiesOut:
    return CapabilitiesOut(
        workspace_id=workspace_id,
        capabilities=[item.model_copy() for item in _CAPABILITIES],
    )


def workspace_status(workspace_id: UUID) -> WorkspaceStatusOut:
    """No worker, session or credential exists yet, so nothing can be online."""

    return WorkspaceStatusOut(
        workspace_id=workspace_id,
        operational=False,
        worker=ComponentStatus(
            state="not_provisioned",
            reason="Nenhum worker foi provisionado para este workspace.",
        ),
        telegram=ComponentStatus(
            state="not_connected",
            reason="Nenhuma sessão do Telegram está conectada.",
        ),
        aliexpress=ComponentStatus(
            state="not_configured",
            reason="Nenhuma credencial AliExpress foi configurada.",
        ),
        checked_at=datetime.now(UTC),
    )
