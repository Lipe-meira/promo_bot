"""Atomic claims and minimal persistence for AliExpress coin-shadow evidence."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.models import (
    AliExpressCoinShadowDeliveryModel,
    AliExpressCoinShadowEvidenceModel,
    AliExpressCoinShadowPreviewModel,
)
from promo_bot.providers.aliexpress.coin_shadow import CoinShadowCorrelationMode


class CoinShadowFingerprintDomain(StrEnum):
    INPUT = "aliexpress-coin-shadow-input-v1"
    TRACKING = "aliexpress-coin-shadow-tracking-v1"
    MESSAGE = "aliexpress-coin-shadow-message-v1"
    DESTINATION = "aliexpress-coin-shadow-destination-v1"


class CoinShadowEvidenceState(StrEnum):
    GENERATING = "GENERATING"
    READY = "READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNCERTAIN = "UNCERTAIN"


class CoinShadowClaimDisposition(StrEnum):
    GENERATE = "GENERATE"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class CoinShadowClaim:
    evidence_id: int
    disposition: CoinShadowClaimDisposition
    state: CoinShadowEvidenceState
    lease_token: str | None = None
    promotion_link: str | None = None
    correlation_mode: str | None = None
    expires_at: datetime | None = None


class CoinShadowTransitionConflict(RuntimeError):
    pass


def coin_shadow_fingerprint(
    app_secret: str,
    value: str,
    domain: CoinShadowFingerprintDomain,
) -> str:
    if not app_secret or not value:
        raise ValueError("COIN_SHADOW_FINGERPRINT_INPUT_REQUIRED")
    secret = app_secret.encode("utf-8")
    purpose_key = hmac.new(secret, domain.value.encode("ascii"), hashlib.sha256).digest()
    return hmac.new(purpose_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


class CoinShadowEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def claim(
        self,
        *,
        input_fingerprint: str,
        tracking_fingerprint: str,
        promotion_link_type: int,
        now: datetime,
        lease_until: datetime,
    ) -> CoinShadowClaim:
        self._validate_key(input_fingerprint, tracking_fingerprint, promotion_link_type)
        await self.session.execute(
            update(AliExpressCoinShadowEvidenceModel)
            .where(
                AliExpressCoinShadowEvidenceModel.state == CoinShadowEvidenceState.GENERATING.value,
                AliExpressCoinShadowEvidenceModel.lease_until.is_not(None),
                AliExpressCoinShadowEvidenceModel.lease_until <= now,
            )
            .values(
                state=CoinShadowEvidenceState.UNCERTAIN.value,
                lease_until=None,
                lease_token=None,
                error_code="ALIEXPRESS_COIN_GENERATION_LEASE_EXPIRED",
                updated_at=now,
            )
        )
        expired_ids = select(AliExpressCoinShadowEvidenceModel.id).where(
            AliExpressCoinShadowEvidenceModel.state == CoinShadowEvidenceState.READY.value,
            AliExpressCoinShadowEvidenceModel.expires_at.is_not(None),
            AliExpressCoinShadowEvidenceModel.expires_at <= now,
        )
        expired_preview_ids = select(AliExpressCoinShadowPreviewModel.id).where(
            AliExpressCoinShadowPreviewModel.evidence_id.in_(expired_ids)
        )
        await self.session.execute(
            update(AliExpressCoinShadowDeliveryModel)
            .where(AliExpressCoinShadowDeliveryModel.preview_id.in_(expired_preview_ids))
            .values(preview_id=None, updated_at=now)
        )
        await self.session.execute(
            delete(AliExpressCoinShadowPreviewModel).where(
                AliExpressCoinShadowPreviewModel.evidence_id.in_(expired_ids)
            )
        )
        await self.session.execute(
            delete(AliExpressCoinShadowEvidenceModel).where(
                AliExpressCoinShadowEvidenceModel.state == CoinShadowEvidenceState.READY.value,
                AliExpressCoinShadowEvidenceModel.expires_at.is_not(None),
                AliExpressCoinShadowEvidenceModel.expires_at <= now,
            )
        )

        existing = await self._find(input_fingerprint, tracking_fingerprint, promotion_link_type)
        if existing is not None:
            return self._claim_for_existing(existing)

        lease_token = uuid4().hex
        inserted_id = await self.session.scalar(
            insert(AliExpressCoinShadowEvidenceModel)
            .values(
                input_fingerprint=input_fingerprint,
                tracking_fingerprint=tracking_fingerprint,
                promotion_link_type=promotion_link_type,
                state=CoinShadowEvidenceState.GENERATING.value,
                generation_count=1,
                generation_started_at=now,
                lease_until=lease_until,
                lease_token=lease_token,
                tracking_confirmed=False,
                attribution_unverified=True,
                route_preservation_manually_observed=False,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "input_fingerprint",
                    "tracking_fingerprint",
                    "promotion_link_type",
                ]
            )
            .returning(AliExpressCoinShadowEvidenceModel.id)
        )
        if inserted_id is not None:
            return CoinShadowClaim(
                evidence_id=inserted_id,
                disposition=CoinShadowClaimDisposition.GENERATE,
                state=CoinShadowEvidenceState.GENERATING,
                lease_token=lease_token,
            )

        winner = await self._find(input_fingerprint, tracking_fingerprint, promotion_link_type)
        if winner is None:
            raise CoinShadowTransitionConflict("COIN_SHADOW_CLAIM_WINNER_MISSING")
        return self._claim_for_existing(winner)

    async def get(self, evidence_id: int) -> CoinShadowClaim | None:
        row = await self.session.get(AliExpressCoinShadowEvidenceModel, evidence_id)
        return None if row is None else self._claim_for_existing(row)

    async def finish_ready(
        self,
        evidence_id: int,
        lease_token: str | None,
        *,
        now: datetime,
        expires_at: datetime,
        promotion_link: str,
        affiliate_host: str,
        correlation_mode: CoinShadowCorrelationMode,
    ) -> None:
        await self._finish(
            evidence_id,
            lease_token,
            now=now,
            values={
                "state": CoinShadowEvidenceState.READY.value,
                "lease_until": None,
                "lease_token": None,
                "generated_at": now,
                "expires_at": expires_at,
                "promotion_link": promotion_link,
                "affiliate_host": affiliate_host,
                "correlation_mode": correlation_mode.value,
                "tracking_confirmed": True,
                "error_code": None,
                "updated_at": now,
            },
        )

    async def finish_review_required(
        self,
        evidence_id: int,
        lease_token: str | None,
        *,
        now: datetime,
        error_code: str,
    ) -> None:
        await self._finish_terminal(
            evidence_id,
            lease_token,
            state=CoinShadowEvidenceState.REVIEW_REQUIRED,
            now=now,
            error_code=error_code,
        )

    async def finish_uncertain(
        self,
        evidence_id: int,
        lease_token: str | None,
        *,
        now: datetime,
        error_code: str,
    ) -> None:
        await self._finish_terminal(
            evidence_id,
            lease_token,
            state=CoinShadowEvidenceState.UNCERTAIN,
            now=now,
            error_code=error_code,
        )

    async def _finish_terminal(
        self,
        evidence_id: int,
        lease_token: str | None,
        *,
        state: CoinShadowEvidenceState,
        now: datetime,
        error_code: str,
    ) -> None:
        await self._finish(
            evidence_id,
            lease_token,
            now=now,
            values={
                "state": state.value,
                "lease_until": None,
                "lease_token": None,
                "tracking_confirmed": False,
                "error_code": error_code,
                "updated_at": now,
            },
        )

    async def _finish(
        self,
        evidence_id: int,
        lease_token: str | None,
        *,
        now: datetime,
        values: dict[str, object],
    ) -> None:
        if lease_token is None:
            raise CoinShadowTransitionConflict("COIN_SHADOW_LEASE_TOKEN_REQUIRED")
        transitioned = await self.session.scalar(
            update(AliExpressCoinShadowEvidenceModel)
            .where(
                AliExpressCoinShadowEvidenceModel.id == evidence_id,
                AliExpressCoinShadowEvidenceModel.state == CoinShadowEvidenceState.GENERATING.value,
                AliExpressCoinShadowEvidenceModel.lease_token == lease_token,
                AliExpressCoinShadowEvidenceModel.lease_until.is_not(None),
                AliExpressCoinShadowEvidenceModel.lease_until > now,
            )
            .values(**values)
            .returning(AliExpressCoinShadowEvidenceModel.id)
        )
        if transitioned is None:
            raise CoinShadowTransitionConflict("COIN_SHADOW_LEASE_LOST")

    async def _find(
        self,
        input_fingerprint: str,
        tracking_fingerprint: str,
        promotion_link_type: int,
    ) -> AliExpressCoinShadowEvidenceModel | None:
        return cast(
            AliExpressCoinShadowEvidenceModel | None,
            await self.session.scalar(
                select(AliExpressCoinShadowEvidenceModel).where(
                    AliExpressCoinShadowEvidenceModel.input_fingerprint == input_fingerprint,
                    AliExpressCoinShadowEvidenceModel.tracking_fingerprint == tracking_fingerprint,
                    AliExpressCoinShadowEvidenceModel.promotion_link_type == promotion_link_type,
                )
            ),
        )

    @staticmethod
    def _claim_for_existing(row: AliExpressCoinShadowEvidenceModel) -> CoinShadowClaim:
        state = CoinShadowEvidenceState(row.state)
        if state is CoinShadowEvidenceState.READY:
            disposition = CoinShadowClaimDisposition.READY
        elif state is CoinShadowEvidenceState.GENERATING:
            disposition = CoinShadowClaimDisposition.IN_PROGRESS
        else:
            disposition = CoinShadowClaimDisposition.BLOCKED
        return CoinShadowClaim(
            evidence_id=row.id,
            disposition=disposition,
            state=state,
            promotion_link=row.promotion_link,
            correlation_mode=row.correlation_mode,
            expires_at=row.expires_at,
        )

    @staticmethod
    def _validate_key(input_fingerprint: str, tracking_fingerprint: str, link_type: int) -> None:
        if len(input_fingerprint) != 64 or len(tracking_fingerprint) != 64:
            raise ValueError("COIN_SHADOW_FINGERPRINT_INVALID")
        if link_type != 0:
            raise ValueError("COIN_SHADOW_PROMOTION_LINK_TYPE_INVALID")


class CoinShadowPreviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def save_ready(
        self,
        *,
        evidence_id: int,
        source_message_fingerprint: str,
        rendered_text: str,
        now: datetime,
        content_expires_at: datetime,
    ) -> tuple[AliExpressCoinShadowPreviewModel, bool]:
        if len(source_message_fingerprint) != 64:
            raise ValueError("COIN_SHADOW_MESSAGE_FINGERPRINT_INVALID")
        evidence = await self.session.scalar(
            select(AliExpressCoinShadowEvidenceModel).where(
                AliExpressCoinShadowEvidenceModel.id == evidence_id,
                AliExpressCoinShadowEvidenceModel.state == CoinShadowEvidenceState.READY.value,
                AliExpressCoinShadowEvidenceModel.expires_at.is_not(None),
                AliExpressCoinShadowEvidenceModel.expires_at > now,
            )
        )
        if evidence is None:
            raise ValueError("COIN_SHADOW_READY_EVIDENCE_REQUIRED")
        inserted_id = await self.session.scalar(
            insert(AliExpressCoinShadowPreviewModel)
            .values(
                evidence_id=evidence_id,
                evidence_state=CoinShadowEvidenceState.READY.value,
                source_message_fingerprint=source_message_fingerprint,
                rendered_text=rendered_text,
                content_expires_at=content_expires_at,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["source_message_fingerprint"])
            .returning(AliExpressCoinShadowPreviewModel.id)
        )
        preview = await self.session.scalar(
            select(AliExpressCoinShadowPreviewModel).where(
                AliExpressCoinShadowPreviewModel.source_message_fingerprint
                == source_message_fingerprint
            )
        )
        if not isinstance(preview, AliExpressCoinShadowPreviewModel):
            raise CoinShadowTransitionConflict("COIN_SHADOW_PREVIEW_WINNER_MISSING")
        if preview.evidence_id != evidence_id or preview.rendered_text != rendered_text:
            raise ValueError("COIN_SHADOW_SOURCE_MESSAGE_CHANGED")
        return preview, inserted_id is not None

    async def get_ready(
        self, preview_id: int, *, now: datetime
    ) -> AliExpressCoinShadowPreviewModel | None:
        return cast(
            AliExpressCoinShadowPreviewModel | None,
            await self.session.scalar(
                select(AliExpressCoinShadowPreviewModel).where(
                    AliExpressCoinShadowPreviewModel.id == preview_id,
                    AliExpressCoinShadowPreviewModel.evidence_state
                    == CoinShadowEvidenceState.READY.value,
                    AliExpressCoinShadowPreviewModel.content_expires_at > now,
                )
            ),
        )


class CoinShadowDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def reserve(
        self,
        *,
        preview_id: int,
        destination_fingerprint: str,
        now: datetime,
    ) -> tuple[AliExpressCoinShadowDeliveryModel, bool]:
        if len(destination_fingerprint) != 64:
            raise ValueError("COIN_SHADOW_DESTINATION_FINGERPRINT_INVALID")
        preview = await self.session.get(AliExpressCoinShadowPreviewModel, preview_id)
        if preview is None:
            raise ValueError("COIN_SHADOW_PREVIEW_NOT_FOUND")
        inserted_id = await self.session.scalar(
            insert(AliExpressCoinShadowDeliveryModel)
            .values(
                preview_id=preview.id,
                source_message_fingerprint=preview.source_message_fingerprint,
                destination_fingerprint=destination_fingerprint,
                state="pending",
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["source_message_fingerprint", "destination_fingerprint"]
            )
            .returning(AliExpressCoinShadowDeliveryModel.id)
        )
        row = await self.session.scalar(
            select(AliExpressCoinShadowDeliveryModel).where(
                AliExpressCoinShadowDeliveryModel.source_message_fingerprint
                == preview.source_message_fingerprint,
                AliExpressCoinShadowDeliveryModel.destination_fingerprint
                == destination_fingerprint,
            )
        )
        if not isinstance(row, AliExpressCoinShadowDeliveryModel):
            raise CoinShadowTransitionConflict("COIN_SHADOW_DELIVERY_WINNER_MISSING")
        return row, inserted_id is not None

    async def mark_sending(self, delivery_id: int, *, now: datetime) -> None:
        transitioned = await self.session.scalar(
            update(AliExpressCoinShadowDeliveryModel)
            .where(
                AliExpressCoinShadowDeliveryModel.id == delivery_id,
                AliExpressCoinShadowDeliveryModel.state == "pending",
                AliExpressCoinShadowDeliveryModel.attempt_count == 0,
            )
            .values(state="sending", attempt_count=1, started_at=now, updated_at=now)
            .returning(AliExpressCoinShadowDeliveryModel.id)
        )
        if transitioned is None:
            raise CoinShadowTransitionConflict("COIN_SHADOW_DELIVERY_TRANSITION_CONFLICT")

    async def finish(
        self,
        delivery_id: int,
        *,
        state: str,
        now: datetime,
        error_code: str | None = None,
        telegram_message_id: str | None = None,
    ) -> None:
        if state not in {"sent", "failed_safe", "uncertain"}:
            raise ValueError("COIN_SHADOW_DELIVERY_STATE_INVALID")
        allowed_states = ("pending", "sending") if state == "failed_safe" else ("sending",)
        transitioned = await self.session.scalar(
            update(AliExpressCoinShadowDeliveryModel)
            .where(
                AliExpressCoinShadowDeliveryModel.id == delivery_id,
                AliExpressCoinShadowDeliveryModel.state.in_(allowed_states),
            )
            .values(
                state=state,
                finished_at=now,
                updated_at=now,
                error_code=error_code,
                telegram_message_id=telegram_message_id,
            )
            .returning(AliExpressCoinShadowDeliveryModel.id)
        )
        if transitioned is None:
            raise CoinShadowTransitionConflict("COIN_SHADOW_DELIVERY_TRANSITION_CONFLICT")
