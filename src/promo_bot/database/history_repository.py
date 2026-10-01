"""Durable affiliate facts. All methods participate in the caller's transaction."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import case, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.history_models import AffiliateLinkGenerationModel
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    AffiliateShadowPreviewModel,
    AliExpressCoinShadowEvidenceModel,
    AliExpressCoinShadowPreviewModel,
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
    names = (
        (
            "id",
            "candidate_id",
            "provider",
            "operation",
            "requested_at",
            "responded_at",
            "source_external_product_id",
            "canonical_url",
            "short_link",
            "official_endpoint_host",
            "contract_version",
            "promotion_link_type",
            "tracking_fingerprint",
            "expires_at",
            "generation_state",
            "official_response_validated",
            "created_at",
            "updated_at",
        )
        if isinstance(row, AffiliateLinkProofModel)
        else (
            "id",
            "input_fingerprint",
            "tracking_fingerprint",
            "promotion_link_type",
            "state",
            "generation_count",
            "generation_started_at",
            "generated_at",
            "expires_at",
            "correlation_mode",
            "tracking_confirmed",
            "attribution_unverified",
            "route_preservation_manually_observed",
            "promotion_link",
            "affiliate_host",
            "error_code",
            "created_at",
            "updated_at",
        )
    )
    for name in names:
        value = getattr(row, name)
        record[name] = value.isoformat() if isinstance(value, datetime) else value
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

    async def snapshot_target(
        self,
        row: AffiliateLinkProofModel | AliExpressCoinShadowEvidenceModel,
    ) -> dict[str, Any]:
        snapshot = legacy_snapshot(row)
        if isinstance(row, AffiliateLinkProofModel):
            candidate = await self.session.get(AffiliateCandidateModel, row.candidate_id)
            if candidate is None:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
            snapshot["candidate_identity"] = {
                "candidate_id": candidate.id,
                "product_id": candidate.external_product_id,
                "variation_key": candidate.variation_key,
                "canonical_url": candidate.canonical_url,
            }
            preview_ids = list(
                await self.session.scalars(
                    select(AffiliateShadowPreviewModel.id).where(
                        AffiliateShadowPreviewModel.affiliate_proof_id == row.id,
                    )
                )
            )
        else:
            preview_ids = list(
                await self.session.scalars(
                    select(AliExpressCoinShadowPreviewModel.id).where(
                        AliExpressCoinShadowPreviewModel.evidence_id == row.id,
                    )
                )
            )
        snapshot["references"] = {
            "preview_count": len(preview_ids),
            "preview_id_high_watermark": max(preview_ids, default=0),
        }
        return snapshot

    async def check_identity(
        self, *, scope: str, identity_key: str, now: datetime | None = None
    ) -> None:
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
                if blocked.state == "UNCERTAIN"
                or (
                    blocked.state == "CALL_STARTED"
                    and (now is None or blocked.lease_until is None or blocked.lease_until <= now)
                )
                else "AFFILIATE_HISTORY_GENERATION_IN_PROGRESS"
                if blocked.state in {"PREPARED", "CALL_STARTED"}
                else "AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE"
            )
            raise AffiliateHistoryError(code)

    async def recover(self, *, scope: str, now: datetime) -> None:
        await serialize_history_write(self.session)
        expired = list(
            await self.session.scalars(
                select(AffiliateLinkGenerationModel).where(
                    AffiliateLinkGenerationModel.scope == scope,
                    AffiliateLinkGenerationModel.state.in_(("PREPARED", "CALL_STARTED")),
                    AffiliateLinkGenerationModel.lease_until <= now,
                )
            )
        )
        for generation in expired:
            generation.state = "UNCERTAIN" if generation.call_started_at else "FAILED"
            generation.finished_at = now
            generation.lease_until = None
            generation.error_code = "AFFILIATE_HISTORY_INTERRUPTED_GENERATION"
            generation.updated_at = now
            if generation.identity_key.startswith("canonical:"):
                candidate_id = int(generation.identity_key.split(":", 1)[1])
                await self.session.execute(
                    update(AffiliateCandidateModel)
                    .where(
                        AffiliateCandidateModel.id == candidate_id,
                        AffiliateCandidateModel.state == "GENERATING_AFFILIATE",
                    )
                    .values(
                        state="MANUAL_REVIEW",
                        processing_lease_until=None,
                        error_code=generation.error_code,
                        updated_at=now,
                    )
                )
            else:
                await self.session.execute(
                    update(AliExpressCoinShadowEvidenceModel)
                    .where(
                        AliExpressCoinShadowEvidenceModel.generation_id == generation.id,
                        AliExpressCoinShadowEvidenceModel.state == "GENERATING",
                    )
                    .values(
                        state="UNCERTAIN",
                        lease_until=None,
                        lease_token=None,
                        error_code=generation.error_code,
                        updated_at=now,
                    )
                )

    async def check_key_rotation(self, *, scope: str, key_fingerprint: str) -> None:
        unknown = await self.session.scalar(
            select(AffiliateLinkGenerationModel.id).where(
                AffiliateLinkGenerationModel.scope == scope,
                AffiliateLinkGenerationModel.identity_key.like("coin:%"),
                AffiliateLinkGenerationModel.state.in_(
                    ("PREPARED", "CALL_STARTED", "UNCERTAIN", "FAILED")
                ),
                AffiliateLinkGenerationModel.key_fingerprint != key_fingerprint,
            )
        )
        if unknown is not None:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_UNCERTAIN_BLOCKED")

    async def validate_link(
        self,
        generation_id: str | None,
        *,
        generated_url: str,
        tracking_fingerprint: str | None,
        contract_version: str | None = None,
    ) -> AffiliateLinkGenerationModel:
        if not generation_id:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_LINK_MISSING")
        generation = await self.get(generation_id)
        if (
            generation is None
            or generation.state != "CONFIRMED"
            or generation.generated_url != generated_url
            or not generation.tracking_confirmed
            or generation.tracking_fingerprint != tracking_fingerprint
            or (contract_version is not None and generation.contract_version != contract_version)
            or not generation.validation_facts
            or not generation.correlation_mode
            or not generation.call_started_at
            or not generation.generated_at
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_LINK_INVALID")
        return generation

    async def validate_request(
        self,
        request_id: str,
        *,
        scope: str,
        identity_key: str,
        legacy_kind: str,
    ) -> AffiliateLinkGenerationModel:
        request = await self.get(request_id)
        if (
            request is None
            or request.state != "REQUESTED"
            or request.scope != scope
            or request.identity_key != identity_key
            or request.legacy_kind != legacy_kind
            or request.legacy_id is None
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
        current = await self.legacy_target(
            scope=scope,
            legacy_kind=legacy_kind,
            legacy_id=request.legacy_id,
        )
        if await self.snapshot_target(current) != request.legacy_record_snapshot:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_CHANGED")
        return request

    async def validate_canonical_proof(
        self,
        proof: AffiliateLinkProofModel,
        *,
        scope: str,
    ) -> AffiliateLinkGenerationModel:
        generation = await self.validate_link(
            proof.generation_id,
            generated_url=proof.short_link,
            tracking_fingerprint=proof.tracking_fingerprint,
            contract_version=proof.contract_version,
        )
        candidate = await self.session.get(AffiliateCandidateModel, proof.candidate_id)
        if (
            generation.scope != scope
            or generation.identity_key != f"canonical:{proof.candidate_id}"
            or generation.provider != proof.provider
            or generation.operation != proof.operation
            or generation.promotion_link_type != proof.promotion_link_type
            or generation.expires_at != proof.expires_at
            or generation.generated_at != proof.responded_at
            or generation.prepared_at != proof.requested_at
            or candidate is None
            or generation.origin is None
            or generation.origin.get("product_id") != proof.source_external_product_id
            or generation.origin.get("variation_key") != candidate.variation_key
            or proof.canonical_url != candidate.canonical_url
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_LINK_INVALID")
        return generation

    async def validate_coin_evidence(
        self,
        evidence: AliExpressCoinShadowEvidenceModel,
    ) -> AffiliateLinkGenerationModel:
        generation = await self.validate_link(
            evidence.generation_id,
            generated_url=evidence.promotion_link or "",
            tracking_fingerprint=evidence.tracking_fingerprint,
            contract_version="coin-short-v1",
        )
        if (
            generation.scope != "shadow"
            or generation.identity_key != f"coin:{evidence.input_fingerprint}"
            or generation.promotion_link_type != evidence.promotion_link_type
            or generation.generated_at != evidence.generated_at
            or generation.expires_at != evidence.expires_at
            or generation.correlation_mode != evidence.correlation_mode
            or not evidence.tracking_confirmed
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_LINK_INVALID")
        return generation

    async def prepare(
        self,
        *,
        scope: str,
        identity_key: str,
        now: datetime,
        lease_until: datetime,
        lease_token: str,
        call_id: str,
        call_ordinal: int,
        input_fingerprint: str,
        tracking_fingerprint: str,
        key_fingerprint: str,
        origin: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> AffiliateLinkGenerationModel:
        await serialize_history_write(self.session)
        await self.check_identity(scope=scope, identity_key=identity_key, now=now)
        if request_id is None:
            row = AffiliateLinkGenerationModel(
                id=str(uuid4()),
                scope=scope,
                platform="aliexpress",
                provider="aliexpress_official",
                operation=LINK_GENERATE,
                identity_key=identity_key,
                tracking_confirmed=False,
                attribution_unverified=True,
                created_at=now,
            )
            self.session.add(row)
        else:
            existing = await self.get(request_id)
            if (
                existing is None
                or existing.state != "REQUESTED"
                or existing.scope != scope
                or existing.identity_key != identity_key
            ):
                raise AffiliateHistoryError("AFFILIATE_HISTORY_LEGACY_TARGET_INELIGIBLE")
            row = existing
        row.state = "PREPARED"
        row.prepared_at, row.updated_at, row.lease_until = now, now, lease_until
        row.lease_token, row.call_id, row.call_ordinal = lease_token, call_id, call_ordinal
        row.input_fingerprint, row.tracking_fingerprint = input_fingerprint, tracking_fingerprint
        row.key_fingerprint, row.promotion_link_type = key_fingerprint, 0
        row.origin = origin
        row.origin_missing_reason = None if origin else "NOT_PROVIDED"
        await self.session.flush()
        return row

    async def start_call(
        self, generation_ids: tuple[str, ...], *, now: datetime, lease_tokens: tuple[str, ...]
    ) -> None:
        await serialize_history_write(self.session)
        for generation_id, token in zip(generation_ids, lease_tokens, strict=True):
            changed = await self.session.scalar(
                update(AffiliateLinkGenerationModel)
                .where(
                    AffiliateLinkGenerationModel.id == generation_id,
                    AffiliateLinkGenerationModel.state == "PREPARED",
                    AffiliateLinkGenerationModel.lease_token == token,
                    AffiliateLinkGenerationModel.lease_until > now,
                )
                .values(state="CALL_STARTED", call_started_at=now, updated_at=now)
                .returning(AffiliateLinkGenerationModel.id)
            )
            if changed is None:
                raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_IN_PROGRESS")

    async def confirm(
        self,
        generation_id: str,
        *,
        now: datetime,
        generated_url: str,
        expires_at: datetime,
        contract_version: str,
        correlation_mode: str,
        validation_facts: dict[str, Any],
    ) -> None:
        row = await self.get(generation_id)
        if (
            row is None
            or row.state != "CALL_STARTED"
            or row.lease_until is None
            or row.lease_until <= now
            or row.call_started_at is None
            or row.call_started_at > now
        ):
            raise AffiliateHistoryError("AFFILIATE_HISTORY_GENERATION_LEASE_LOST")
        row.state, row.generated_url, row.generated_at = "CONFIRMED", generated_url, now
        row.finished_at, row.updated_at, row.expires_at = now, now, expires_at
        row.contract_version, row.correlation_mode = contract_version, correlation_mode
        row.tracking_confirmed, row.validation_facts = True, validation_facts
        row.lease_until = None
        await self.session.flush()

    async def fail(
        self, generation_ids: tuple[str, ...], *, now: datetime, state: str, error_code: str
    ) -> None:
        if state not in {"FAILED", "REJECTED", "UNCERTAIN"}:
            raise AffiliateHistoryError("AFFILIATE_HISTORY_STATE_INVALID")
        await self.session.execute(
            update(AffiliateLinkGenerationModel)
            .where(
                AffiliateLinkGenerationModel.id.in_(generation_ids),
                AffiliateLinkGenerationModel.state.in_(("PREPARED", "CALL_STARTED")),
            )
            .values(
                state=case(
                    (AffiliateLinkGenerationModel.call_started_at.is_(None), "FAILED"),
                    else_=state,
                ),
                finished_at=now,
                lease_until=None,
                error_code=error_code,
                updated_at=now,
            )
        )

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
            legacy_record_snapshot=await self.snapshot_target(row),
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
