"""Manual delivery of existing, valid shadow content; no provider clients or workers."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import select

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    AffiliateShadowPreviewModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.database.shadow_delivery_repository import ShadowDeliveryRepository
from promo_bot.observability.shadow import mute_shadow_payload_logs


class ShadowDeliveryRejected(ValueError):
    """Only locally defined, sanitized codes cross this boundary."""


class DefinitiveSendRejection(RuntimeError):
    """The server explicitly rejected sendMessage without delivering it."""


class ChatInfo(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def type(self) -> str: ...
    @property
    def username(self) -> str | None: ...
    @property
    def active_usernames(self) -> tuple[str, ...]: ...


class ShadowTextTransport(Protocol):
    async def get_chat(self, chat_id: str) -> ChatInfo: ...
    async def send_text(self, chat_id: str, text: str) -> str: ...


def assert_shadow_delivery_gates(settings: EnvironmentSettings, *, confirm: bool) -> None:
    if not confirm:
        raise ShadowDeliveryRejected("SHADOW_SEND_CONFIRMATION_REQUIRED")
    if not settings.telegram_shadow_test_delivery_enabled:
        raise ShadowDeliveryRejected("TELEGRAM_SHADOW_TEST_DELIVERY_DISABLED")
    if (
        not settings.dry_run
        or settings.publish_real_deals
        or settings.publish_without_affiliate
        or settings.search_enabled
    ):
        raise ShadowDeliveryRejected("SHADOW_DELIVERY_SAFETY_GATE_CLOSED")
    if settings.telegram_bot_token is None:
        raise ShadowDeliveryRejected("TELEGRAM_BOT_TOKEN_MISSING")


class ShadowDeliveryService:
    def __init__(
        self,
        database: AffiliateShadowDatabase,
        transport: ShadowTextTransport,
        settings: EnvironmentSettings,
        config: AppConfig,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise ShadowDeliveryRejected("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.database, self.transport = database, transport
        self.settings, self.config = settings, config
        self.clock = clock or (lambda: datetime.now(UTC))

    async def deliver(
        self, preview_id: int, destination: str, *, confirm: bool
    ) -> dict[str, object]:
        with mute_shadow_payload_logs():
            return await self._deliver(preview_id, destination, confirm=confirm)

    async def _deliver(
        self, preview_id: int, destination: str, *, confirm: bool
    ) -> dict[str, object]:
        assert_shadow_delivery_gates(self.settings, confirm=confirm)
        target = self.config.telegram_shadow_delivery.allowed_destinations.get(destination)
        if target is None:
            raise ShadowDeliveryRejected("SHADOW_DESTINATION_NOT_ALLOWED")
        # Fail closed: numeric IDs make exclusion of every configured source locally provable.
        sources = self.config.source_channels
        if any(re.fullmatch(r"-100[1-9][0-9]*", s) is None for s in sources):
            raise ShadowDeliveryRejected("SHADOW_SOURCE_NUMERIC_ID_REQUIRED")
        key = hashlib.sha256(f"telegram:{target.chat_id}".encode()).hexdigest()
        report: dict[str, object] = {
            "status": "failed_safe",
            "preview_id": preview_id,
            "destination": destination,
            "get_chat_attempts": 0,
            "send_message_attempts": 0,
            "external_side_effect": False,
            "production_publication": False,
            "error_code": None,
            "persisted_state": None,
        }
        async with self.database.session() as session:
            if await session.get(AffiliateShadowPreviewModel, preview_id) is None:
                report["error_code"] = "SHADOW_PREVIEW_NOT_FOUND"
                return report
            row, created = await ShadowDeliveryRepository(session).reserve(
                preview_id, key, self.clock()
            )
            internal_id, persisted_state = row.id, row.state
        report["delivery_id"] = internal_id
        if not created:
            report.update(
                status="uncertain" if persisted_state == "sending" else persisted_state,
                persisted_state=persisted_state,
                error_code="SHADOW_DELIVERY_ALREADY_ATTEMPTED",
            )
            return report

        state, error, message_id = "failed_safe", None, None
        persisted_state = "pending"
        try:
            if target.chat_id in sources:
                raise ShadowDeliveryRejected("SHADOW_DESTINATION_IS_SOURCE")
            text = await self._validated_text(preview_id, target.chat_id)
            report["get_chat_attempts"] = 1
            try:
                async with asyncio.timeout(15):
                    chat = await self.transport.get_chat(target.chat_id)
            except Exception:
                raise ShadowDeliveryRejected("SHADOW_GET_CHAT_FAILED") from None
            if (
                str(chat.id) != target.chat_id
                or chat.type != "channel"
                or chat.username
                or chat.active_usernames
            ):
                raise ShadowDeliveryRejected("SHADOW_DESTINATION_NOT_PRIVATE_CHANNEL")
            # Recheck expiry/content after network preflight. Do not reconstruct or renew anything.
            if await self._validated_text(preview_id, target.chat_id) != text:
                raise ShadowDeliveryRejected("SHADOW_PREVIEW_CHANGED")
            async with self.database.session() as session:
                await ShadowDeliveryRepository(session).mark_sending(internal_id, self.clock())
            persisted_state = "sending"
            report["send_message_attempts"] = 1
            report["external_side_effect"] = True
            async with asyncio.timeout(15):
                message_id = await self.transport.send_text(target.chat_id, text)
            if not isinstance(message_id, str) or not re.fullmatch(r"[1-9][0-9]*", message_id):
                raise RuntimeError("SHADOW_SEND_RESPONSE_AMBIGUOUS")
            state = "sent"
        except ShadowDeliveryRejected as exc:
            error = str(exc)
        except DefinitiveSendRejection:
            error = "SHADOW_SEND_REJECTED"
        except asyncio.CancelledError:
            state = "uncertain" if persisted_state == "sending" else "failed_safe"
            await self._finish(internal_id, state, "SHADOW_DELIVERY_CANCELLED", None)
            raise
        except Exception:
            state = "uncertain" if persisted_state == "sending" else "failed_safe"
            error = "SHADOW_SEND_UNCERTAIN" if state == "uncertain" else "SHADOW_PREFLIGHT_FAILED"
        saved = await self._finish(
            internal_id, state, error, message_id if state == "sent" else None
        )
        if saved:
            persisted_state = state
        else:
            state = "uncertain" if persisted_state == "sending" else "failed_safe"
            error = "SHADOW_RESULT_PERSIST_FAILED"
        report.update(status=state, persisted_state=persisted_state, error_code=error)
        return report

    async def _finish(
        self, internal_id: int, state: str, error: str | None, message_id: str | None
    ) -> bool:
        try:
            async with self.database.session() as session:
                await ShadowDeliveryRepository(session).finish(
                    internal_id, state, self.clock(), error_code=error, message_id=message_id
                )
            return True
        except Exception:
            return False

    async def _validated_text(self, preview_id: int, chat_id: str) -> str:
        now = self.clock()
        async with self.database.session() as session:
            preview = await session.get(AffiliateShadowPreviewModel, preview_id)
            if preview is None:
                raise ShadowDeliveryRejected("SHADOW_PREVIEW_NOT_FOUND")
            if preview.status != "READY":
                raise ShadowDeliveryRejected("SHADOW_PREVIEW_NOT_READY")
            if preview.content_expires_at <= now or preview.created_at > now:
                raise ShadowDeliveryRejected("SHADOW_PREVIEW_EXPIRED")
            if not preview.rendered_text or not preview.affiliate_link or preview.purged_at:
                raise ShadowDeliveryRejected("SHADOW_PREVIEW_CONTENT_MISSING")
            # Conservative UTF-16 budget; never split or transform the original text.
            if len(preview.rendered_text.encode("utf-16-le")) // 2 > 4096:
                raise ShadowDeliveryRejected("SHADOW_MESSAGE_TOO_LONG")
            proof = await session.get(AffiliateLinkProofModel, preview.affiliate_proof_id)
            if (
                proof is None
                or proof.generation_state != "CONFIRMED"
                or not proof.official_response_validated
            ):
                raise ShadowDeliveryRejected("SHADOW_PROOF_INVALID")
            if proof.expires_at is None or proof.expires_at <= now or proof.responded_at > now:
                raise ShadowDeliveryRejected("SHADOW_PROOF_EXPIRED")
            candidate = await session.get(AffiliateCandidateModel, proof.candidate_id)
            if (
                candidate is None
                or candidate.store != preview.store
                or proof.provider != preview.provider
                or proof.short_link != preview.affiliate_link
                or proof.source_external_product_id != candidate.external_product_id
                or proof.canonical_url != candidate.canonical_url
                or preview.affiliate_link not in preview.rendered_text
                or urlsplit(preview.affiliate_link).scheme != "https"
                or urlsplit(preview.affiliate_link).hostname != preview.affiliate_host
            ):
                raise ShadowDeliveryRejected("SHADOW_PROOF_MISMATCH")
            source = await session.get(SourceMessageModel, preview.source_message_id)
            if source is None:
                raise ShadowDeliveryRejected("SHADOW_SOURCE_NOT_FOUND")
            if source.channel_id == chat_id:
                raise ShadowDeliveryRejected("SHADOW_DESTINATION_IS_SOURCE")
            correlated = await session.scalar(
                select(SourceMessageLinkModel.id)
                .where(
                    SourceMessageLinkModel.source_message_id == source.id,
                    SourceMessageLinkModel.affiliate_candidate_id == proof.candidate_id,
                    SourceMessageLinkModel.store == preview.store,
                    SourceMessageLinkModel.external_product_id == proof.source_external_product_id,
                    SourceMessageLinkModel.canonical_url == proof.canonical_url,
                )
                .limit(1)
            )
            if correlated is None:
                raise ShadowDeliveryRejected("SHADOW_PROOF_MISMATCH")
            return preview.rendered_text
