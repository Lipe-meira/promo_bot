"""Atomic complete-message previews; repeated spans retain distinct ordinals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from promo_bot.database.history_repository import (
    AffiliateLinkHistoryRepository,
    serialize_history_write,
)
from promo_bot.database.models import (
    AliExpressCoinShadowDeliveryModel,
    AliExpressCoinShadowEvidenceModel,
    AliExpressCoinShadowMultiOccurrenceModel,
    AliExpressCoinShadowMultiPreviewModel,
)


@dataclass(frozen=True, slots=True)
class CoinMultiOccurrenceInput:
    ordinal: int
    start: int
    end: int
    evidence_id: int
    generation_id: str


class CoinShadowMultiPreviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        if session.info.get("affiliate_shadow_database") is not True:
            raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.session = session

    async def save_ready(
        self,
        *,
        source_message_fingerprint: str,
        rendered_text: str,
        occurrences: Sequence[CoinMultiOccurrenceInput],
        cache_hits: Mapping[str, bool],
        now: datetime,
    ) -> tuple[AliExpressCoinShadowMultiPreviewModel, bool]:
        if len(source_message_fingerprint) != 64 or not 2 <= len(occurrences) <= 3:
            raise ValueError("COIN_MULTI_PREVIEW_CONTRACT_INVALID")
        if len(rendered_text.encode("utf-16-le")) // 2 > 4096:
            raise ValueError("COIN_SHADOW_MESSAGE_TOO_LONG")
        end = -1
        for i, row in enumerate(occurrences):
            if row.ordinal != i or row.start < end or row.end <= row.start:
                raise ValueError("COIN_MULTI_PREVIEW_SPANS_INVALID")
            end = row.end
        await serialize_history_write(self.session)
        history = AffiliateLinkHistoryRepository(self.session)
        expiry: list[datetime] = []
        for row in occurrences:
            evidence = await self.session.get(AliExpressCoinShadowEvidenceModel, row.evidence_id)
            if (
                evidence is None
                or evidence.state != "READY"
                or evidence.expires_at is None
                or evidence.expires_at <= now
            ):
                raise ValueError("COIN_SHADOW_READY_EVIDENCE_REQUIRED")
            if (await history.validate_coin_evidence(evidence)).id != row.generation_id:
                raise ValueError("AFFILIATE_HISTORY_PREVIEW_GENERATION_CHANGED")
            expiry.append(evidence.expires_at)
        existing = await self.session.scalar(
            select(AliExpressCoinShadowMultiPreviewModel).where(
                AliExpressCoinShadowMultiPreviewModel.source_message_fingerprint
                == source_message_fingerprint
            )
        )
        origin = {
            "source_message_fingerprint": source_message_fingerprint,
            "occurrence_map_version": 1,
            "occurrences": [
                {"ordinal": row.ordinal, "generation_id": row.generation_id} for row in occurrences
            ],
        }
        if existing is not None:
            stored = await self.occurrences(existing.id)
            if existing.rendered_text != rendered_text or tuple(
                (r.ordinal, r.span_start, r.span_end, r.evidence_id, r.generation_id)
                for r in stored
            ) != tuple(
                (r.ordinal, r.start, r.end, r.evidence_id, r.generation_id) for r in occurrences
            ):
                raise ValueError("COIN_SHADOW_SOURCE_MESSAGE_CHANGED")
            await history.validate_preview(existing)
            return existing, False
        preview = AliExpressCoinShadowMultiPreviewModel(
            source_message_fingerprint=source_message_fingerprint,
            rendered_text=rendered_text,
            content_expires_at=min(expiry),
            created_at=now,
            updated_at=now,
        )
        self.session.add(preview)
        await self.session.flush()
        for row in occurrences:
            self.session.add(
                AliExpressCoinShadowMultiOccurrenceModel(
                    preview_id=preview.id,
                    ordinal=row.ordinal,
                    span_start=row.start,
                    span_end=row.end,
                    evidence_id=row.evidence_id,
                    evidence_state="READY",
                    generation_id=row.generation_id,
                )
            )
        use = await history.record_use(
            scope="shadow",
            kind="PREVIEW",
            generation_ids=tuple(dict.fromkeys(row.generation_id for row in occurrences)),
            cache_hits=cache_hits,
            now=now,
            origin=origin,
            operational_kind="coin-multi-preview",
            operational_id=preview.id,
        )
        preview.history_use_id = use.id
        await self.session.flush()
        return preview, True

    async def occurrences(
        self, preview_id: int
    ) -> tuple[AliExpressCoinShadowMultiOccurrenceModel, ...]:
        return tuple(
            await self.session.scalars(
                select(AliExpressCoinShadowMultiOccurrenceModel)
                .where(AliExpressCoinShadowMultiOccurrenceModel.preview_id == preview_id)
                .order_by(AliExpressCoinShadowMultiOccurrenceModel.ordinal)
            )
        )

    async def get_ready(
        self, preview_id: int, *, now: datetime
    ) -> AliExpressCoinShadowMultiPreviewModel | None:
        preview = await self.session.get(AliExpressCoinShadowMultiPreviewModel, preview_id)
        if preview is None or preview.content_expires_at <= now:
            return None
        for row in await self.occurrences(preview.id):
            evidence = await self.session.get(AliExpressCoinShadowEvidenceModel, row.evidence_id)
            if evidence is None or evidence.expires_at is None or evidence.expires_at <= now:
                return None
        await AffiliateLinkHistoryRepository(self.session).validate_preview(preview)
        return preview


async def purge_multi_for_evidence(
    session: AsyncSession, evidence_ids: Sequence[int], *, now: datetime
) -> None:
    """Remove whole parents before RESTRICT evidence; reservations/history survive."""
    parent_ids = select(AliExpressCoinShadowMultiOccurrenceModel.preview_id).where(
        AliExpressCoinShadowMultiOccurrenceModel.evidence_id.in_(evidence_ids)
    )
    await session.execute(
        update(AliExpressCoinShadowDeliveryModel)
        .where(AliExpressCoinShadowDeliveryModel.multi_preview_id.in_(parent_ids))
        .values(multi_preview_id=None, updated_at=now)
    )
    await session.execute(
        delete(AliExpressCoinShadowMultiPreviewModel).where(
            AliExpressCoinShadowMultiPreviewModel.id.in_(parent_ids)
        )
    )


async def validate_coin_multi_schema(database: object) -> None:
    from promo_bot.database.session import AffiliateShadowDatabase

    if not isinstance(database, AffiliateShadowDatabase):
        raise ValueError("AFFILIATE_SHADOW_DATABASE_REQUIRED")
    try:
        async with database.session() as session:
            tables = set(
                await session.scalars(text("SELECT name FROM sqlite_master WHERE type='table'"))
            )
            columns = {
                row[1]
                for row in await session.execute(
                    text("PRAGMA table_info(aliexpress_coin_shadow_deliveries)")
                )
            }
            if (
                not {
                    "aliexpress_coin_shadow_multi_previews",
                    "aliexpress_coin_shadow_multi_preview_occurrences",
                }
                <= tables
                or "multi_preview_id" not in columns
            ):
                raise ValueError("COIN_MULTI_SCHEMA_REQUIRED")
            if (
                await session.scalar(text("SELECT version_num FROM alembic_version"))
                != "b8c2e4f6a901"
            ):
                raise ValueError("COIN_MULTI_SCHEMA_REQUIRED")
    except Exception:
        raise ValueError("COIN_MULTI_SCHEMA_REQUIRED") from None
