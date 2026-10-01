"""Durable affiliate facts. All methods participate in the caller's transaction."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.history_models import AffiliateLinkGenerationModel
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    AliExpressCoinShadowEvidenceModel,
)

LINK_GENERATE = "aliexpress.affiliate.link.generate"


class AffiliateHistoryError(ValueError):
    """Only stable, sanitized codes cross the history boundary."""


async def serialize_history_write(session: AsyncSession) -> None:
    # A first write serializes claims before reads with SQLite's existing policy.
    await session.execute(
        text("UPDATE affiliate_link_generations SET updated_at=updated_at WHERE id=''")
    )


def legacy_snapshot(
    row: AffiliateLinkProofModel | AliExpressCoinShadowEvidenceModel,
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name == "generation_id":
            continue
        value = getattr(row, column.name)
        record[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return {"label": "LEGACY_NOT_REVALIDATED", "record": record}


def legacy_identity(row: AffiliateLinkProofModel | AliExpressCoinShadowEvidenceModel) -> str:
    if isinstance(row, AffiliateLinkProofModel):
        return f"canonical:{row.candidate_id}"
    return f"coin:{row.input_fingerprint}"


class AffiliateLinkHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, generation_id: str) -> AffiliateLinkGenerationModel | None:
        return await self.session.get(AffiliateLinkGenerationModel, generation_id)

    async def check_identity(self, *, scope: str, identity_key: str) -> None:
        blocked = await self.session.scalar(
            select(AffiliateLinkGenerationModel).where(
                AffiliateLinkGenerationModel.scope == scope,
                AffiliateLinkGenerationModel.platform == "aliexpress",
                AffiliateLinkGenerationModel.identity_key == identity_key,
                AffiliateLinkGenerationModel.state.in_(
                    ("PREPARED", "CALL_STARTED", "UNCERTAIN", "FAILED", "REJECTED")
                ),
            )
        )
        if blocked is not None:
            code = (
                "AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED"
                if blocked.state in {"UNCERTAIN", "CALL_STARTED"}
                else "AFFILIATE_HISTORY_GENERATION_IN_PROGRESS"
                if blocked.state == "PREPARED"
                else "AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE"
            )
            raise AffiliateHistoryError(code)

    async def legacy_target(
        self,
        *,
        scope: str,
        legacy_kind: str,
        legacy_id: int,
    ) -> AffiliateLinkProofModel | AliExpressCoinShadowEvidenceModel:
        if scope not in {"runtime", "shadow"} or legacy_id <= 0:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
        row: AffiliateLinkProofModel | AliExpressCoinShadowEvidenceModel
        if legacy_kind == "canonical-proof":
            proof = await self.session.get(AffiliateLinkProofModel, legacy_id)
            if (
                proof is None
                or proof.provider != "aliexpress_official"
                or proof.operation != LINK_GENERATE
                or proof.generation_state != "CONFIRMED"
                or not proof.official_response_validated
            ):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
            row = proof
            candidate = await self.session.get(AffiliateCandidateModel, row.candidate_id)
            if candidate is None:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
            if candidate.state == "GENERATING_AFFILIATE":
                raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_IN_PROGRESS")
            if candidate.state not in {
                "AFFILIATE_GENERATED",
                "PENDING_AFFILIATE",
                "AWAITING_AFFILIATE_GENERATION",
            }:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
        elif legacy_kind == "coin-evidence" and scope == "shadow":
            coin_row = await self.session.get(AliExpressCoinShadowEvidenceModel, legacy_id)
            if coin_row is None:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
            if coin_row.state in {"GENERATING", "UNCERTAIN"}:
                raise AffiliateHistoryError(
                    "AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED"
                    if coin_row.state == "UNCERTAIN"
                    else "AFFILIATE_HISTORY_GENERATION_IN_PROGRESS"
                )
            if coin_row.state != "READY":
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
            row = coin_row
        else:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
        if row.generation_id is not None:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
        await self.check_identity(scope=scope, identity_key=legacy_identity(row))
        return row

    async def request_legacy_generation(
        self,
        *,
        scope: str,
        legacy_kind: str,
        legacy_id: int,
        now: datetime,
    ) -> AffiliateLinkGenerationModel:
        await serialize_history_write(self.session)
        row = await self.legacy_target(scope=scope, legacy_kind=legacy_kind, legacy_id=legacy_id)
        existing = await self.session.scalar(
            select(AffiliateLinkGenerationModel).where(
                AffiliateLinkGenerationModel.scope == scope,
                AffiliateLinkGenerationModel.legacy_kind == legacy_kind,
                AffiliateLinkGenerationModel.legacy_id == legacy_id,
                AffiliateLinkGenerationModel.state == "REQUESTED",
            )
        )
        if existing is not None:
            return existing
        request = AffiliateLinkGenerationModel(
            id=str(uuid4()),
            platform="aliexpress",
            provider="aliexpress_official",
            operation=LINK_GENERATE,
            scope=scope,
            state="REQUESTED",
            identity_key=legacy_identity(row),
            request_reason="LEGACY_CACHE_TRANSITION",
            operator_requested_at=now,
            legacy_kind=legacy_kind,
            legacy_id=legacy_id,
            legacy_record_snapshot=legacy_snapshot(row),
            origin_missing_reason="OPERATOR_REQUEST_WITHOUT_MESSAGE",
            created_at=now,
            updated_at=now,
            tracking_confirmed=False,
            attribution_unverified=True,
        )
        self.session.add(request)
        await self.session.flush()
        return request

    async def legacy_blocks(self, *, scope: str) -> list[dict[str, Any]]:
        report: list[dict[str, Any]] = []
        proofs = (
            await self.session.scalars(
                select(AffiliateLinkProofModel).where(
                    AffiliateLinkProofModel.generation_id.is_(None)
                )
            )
        ).all()
        coins = (
            (
                await self.session.scalars(
                    select(AliExpressCoinShadowEvidenceModel).where(
                        AliExpressCoinShadowEvidenceModel.generation_id.is_(None)
                    )
                )
            ).all()
            if scope == "shadow"
            else []
        )
        for kind, rows in (("canonical-proof", proofs), ("coin-evidence", coins)):
            for row in rows:
                code = "AFFILIATE_HISTORY_GENERATION_LINK_MISSING"
                eligible = True
                try:
                    await self.legacy_target(scope=scope, legacy_kind=kind, legacy_id=row.id)
                except AffiliateHistoryError as exc:
                    eligible, code = False, str(exc)
                report.append(
                    {
                        "legacy_kind": kind,
                        "legacy_id": row.id,
                        "eligible": eligible,
                        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                        "block_code": code,
                    }
                )
        return report
