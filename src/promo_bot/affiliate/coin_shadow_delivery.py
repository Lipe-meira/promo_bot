"""One-attempt delivery of isolated AliExpress coin-shadow previews."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from promo_bot.affiliate.shadow_delivery import DefinitiveSendRejection
from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.coin_shadow_repository import (
    CoinShadowDeliveryRepository,
    CoinShadowFingerprintDomain,
    CoinShadowPreviewRepository,
    coin_shadow_fingerprint,
)
from promo_bot.database.session import AffiliateShadowDatabase
from promo_bot.observability.shadow import mute_shadow_payload_logs

LOGGER = logging.getLogger("promo_bot.affiliate.coin_shadow_delivery")
PRIVATE_TEST_ALIAS = "private-test"


class CoinShadowDeliveryRejected(ValueError):
    """A fail-closed, sanitized local policy rejection."""


class CoinShadowChannelAccess(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def type(self) -> str: ...

    @property
    def username(self) -> str | None: ...

    @property
    def active_usernames(self) -> tuple[str, ...]: ...

    @property
    def bot_membership_status(self) -> str: ...

    @property
    def can_post_messages(self) -> bool: ...


class CoinShadowTextTransport(Protocol):
    async def inspect_private_channel(self, chat_id: str) -> CoinShadowChannelAccess: ...

    async def send_text(self, chat_id: str, text: str) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class CoinShadowDeliveryOutcome:
    delivery_id: int | None
    preview_id: int
    status: str
    inspection_attempts: int = 0
    send_message_attempts: int = 0
    external_side_effect: bool = False
    error_code: str | None = None

    def sanitized_output(self) -> dict[str, object]:
        return {
            "status": self.status,
            "delivery_id": self.delivery_id,
            "preview_id": self.preview_id,
            "inspection_attempts": self.inspection_attempts,
            "send_message_attempts": self.send_message_attempts,
            "external_side_effect": self.external_side_effect,
            "error_code": self.error_code,
            "attribution_unverified": True,
            "route_preservation_manually_observed": False,
            "production_publication": False,
            "content_included": False,
        }


class CoinShadowDeliveryService:
    def __init__(
        self,
        database: AffiliateShadowDatabase,
        transport: CoinShadowTextTransport,
        settings: EnvironmentSettings,
        config: AppConfig,
        *,
        app_secret: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(database, AffiliateShadowDatabase):
            raise CoinShadowDeliveryRejected("AFFILIATE_SHADOW_DATABASE_REQUIRED")
        self.database = database
        self.transport = transport
        self.settings = settings
        self.config = config
        self.app_secret = app_secret
        self.clock = clock or (lambda: datetime.now(UTC))

    async def deliver(self, preview_id: int, destination: str) -> CoinShadowDeliveryOutcome:
        target_chat_id = self._authorized_target(destination)
        destination_fingerprint = coin_shadow_fingerprint(
            self.app_secret,
            f"telegram\0{target_chat_id}",
            CoinShadowFingerprintDomain.DESTINATION,
        )
        with mute_shadow_payload_logs():
            now = self.clock()
            async with self.database.session() as session:
                preview = await CoinShadowPreviewRepository(session).get_ready(preview_id, now=now)
                if preview is None:
                    return CoinShadowDeliveryOutcome(
                        None,
                        preview_id,
                        "failed_safe",
                        error_code="COIN_SHADOW_PREVIEW_NOT_READY",
                    )
                row, created = await CoinShadowDeliveryRepository(session).reserve(
                    preview_id=preview_id,
                    destination_fingerprint=destination_fingerprint,
                    now=now,
                )
            if not created:
                return CoinShadowDeliveryOutcome(
                    row.id,
                    preview_id,
                    "uncertain" if row.state == "sending" else row.state,
                    error_code="COIN_SHADOW_DELIVERY_ALREADY_ATTEMPTED",
                )
            return await self._deliver_reserved(
                row.id,
                preview_id,
                target_chat_id,
                expected_text=preview.rendered_text,
            )

    async def _deliver_reserved(
        self,
        delivery_id: int,
        preview_id: int,
        target_chat_id: str,
        *,
        expected_text: str,
    ) -> CoinShadowDeliveryOutcome:
        inspection_attempts = 0
        send_attempts = 0
        sending = False
        try:
            inspection_attempts = 1
            async with asyncio.timeout(15):
                access = await self.transport.inspect_private_channel(target_chat_id)
            self._validate_access(access, target_chat_id)
            text = await self._validated_text(preview_id)
            if text != expected_text:
                raise CoinShadowDeliveryRejected("COIN_SHADOW_PREVIEW_CHANGED")
            async with self.database.session() as session:
                await CoinShadowDeliveryRepository(session).mark_sending(
                    delivery_id, now=self.clock()
                )
            sending = True
            send_attempts = 1
            async with asyncio.timeout(15):
                telegram_message_id = await self.transport.send_text(target_chat_id, text)
            if re.fullmatch(r"[1-9][0-9]*", telegram_message_id) is None:
                raise RuntimeError("COIN_SHADOW_SEND_RESPONSE_AMBIGUOUS")
            if not await self._finish(
                delivery_id,
                state="sent",
                telegram_message_id=telegram_message_id,
            ):
                return self._outcome(
                    delivery_id,
                    preview_id,
                    "uncertain",
                    inspection_attempts,
                    send_attempts,
                    "COIN_SHADOW_RESULT_PERSIST_FAILED",
                )
            outcome = self._outcome(
                delivery_id,
                preview_id,
                "sent",
                inspection_attempts,
                send_attempts,
                None,
            )
        except asyncio.CancelledError:
            state = "uncertain" if sending else "failed_safe"
            await self._finish(
                delivery_id,
                state=state,
                error_code="COIN_SHADOW_DELIVERY_CANCELLED",
            )
            raise
        except CoinShadowDeliveryRejected as exc:
            error_code = str(exc)
            await self._finish(delivery_id, state="failed_safe", error_code=error_code)
            outcome = self._outcome(
                delivery_id,
                preview_id,
                "failed_safe",
                inspection_attempts,
                send_attempts,
                error_code,
            )
        except DefinitiveSendRejection:
            await self._finish(
                delivery_id,
                state="failed_safe",
                error_code="COIN_SHADOW_SEND_REJECTED",
            )
            outcome = self._outcome(
                delivery_id,
                preview_id,
                "failed_safe",
                inspection_attempts,
                send_attempts,
                "COIN_SHADOW_SEND_REJECTED",
            )
        except Exception:
            state = "uncertain" if sending else "failed_safe"
            error_code = (
                "COIN_SHADOW_SEND_UNCERTAIN" if sending else "COIN_SHADOW_CHANNEL_PREFLIGHT_FAILED"
            )
            await self._finish(delivery_id, state=state, error_code=error_code)
            outcome = self._outcome(
                delivery_id,
                preview_id,
                state,
                inspection_attempts,
                send_attempts,
                error_code,
            )
        LOGGER.info(
            "AliExpress coin-shadow delivery finished",
            extra={
                "stage": "aliexpress_coin_shadow_delivery",
                "result": outcome.status,
                "delivery_id": delivery_id,
                "error_code": outcome.error_code,
            },
        )
        return outcome

    async def _validated_text(self, preview_id: int) -> str:
        async with self.database.session() as session:
            preview = await CoinShadowPreviewRepository(session).get_ready(
                preview_id, now=self.clock()
            )
        if preview is None:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_PREVIEW_NOT_READY")
        if len(preview.rendered_text.encode("utf-16-le")) // 2 > 4096:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_MESSAGE_TOO_LONG")
        return preview.rendered_text

    async def _finish(
        self,
        delivery_id: int,
        *,
        state: str,
        error_code: str | None = None,
        telegram_message_id: str | None = None,
    ) -> bool:
        try:
            async with self.database.session() as session:
                await CoinShadowDeliveryRepository(session).finish(
                    delivery_id,
                    state=state,
                    now=self.clock(),
                    error_code=error_code,
                    telegram_message_id=telegram_message_id,
                )
            return True
        except Exception:
            return False

    def _authorized_target(self, destination: str) -> str:
        if (
            not self.settings.aliexpress_coin_short_shadow_enabled
            or not self.settings.aliexpress_live_api_enabled
            or not self.settings.dry_run
            or self.settings.publish_real_deals
            or self.settings.publish_without_affiliate
            or self.settings.search_enabled
            or self.settings.coupon_browser_verification
            or self.settings.aliexpress_telegram_shadow_enabled
            or self.settings.aliexpress_telegram_shadow_listener_enabled
            or self.settings.aliexpress_telegram_shadow_auto_delivery_enabled
            or self.settings.telegram_shadow_test_delivery_enabled
        ):
            raise CoinShadowDeliveryRejected("COIN_SHADOW_SAFETY_GATE_CLOSED")
        if self.settings.telegram_bot_token is None:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_BOT_TOKEN_MISSING")
        if destination != PRIVATE_TEST_ALIAS:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_DESTINATION_REQUIRED")
        target = self.config.telegram_shadow_delivery.allowed_destinations.get(destination)
        if target is None or target.kind != "private_channel":
            raise CoinShadowDeliveryRejected("COIN_SHADOW_DESTINATION_NOT_ALLOWED")
        if any(
            re.fullmatch(r"-100[1-9][0-9]*", source) is None
            for source in self.config.source_channels
        ):
            raise CoinShadowDeliveryRejected("COIN_SHADOW_NUMERIC_SOURCE_REQUIRED")
        if target.chat_id in self.config.source_channels:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_DESTINATION_IS_SOURCE")
        return target.chat_id

    @staticmethod
    def _validate_access(access: CoinShadowChannelAccess, target_chat_id: str) -> None:
        if (
            str(access.id) != target_chat_id
            or access.type != "channel"
            or access.username is not None
            or bool(access.active_usernames)
        ):
            raise CoinShadowDeliveryRejected("COIN_SHADOW_DESTINATION_NOT_PRIVATE_CHANNEL")
        if access.bot_membership_status not in {"administrator", "creator", "owner"}:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_BOT_NOT_CHANNEL_MEMBER")
        if not access.can_post_messages:
            raise CoinShadowDeliveryRejected("COIN_SHADOW_BOT_CANNOT_POST")

    @staticmethod
    def _outcome(
        delivery_id: int,
        preview_id: int,
        state: str,
        inspection_attempts: int,
        send_attempts: int,
        error_code: str | None,
    ) -> CoinShadowDeliveryOutcome:
        return CoinShadowDeliveryOutcome(
            delivery_id=delivery_id,
            preview_id=preview_id,
            status=state,
            inspection_attempts=inspection_attempts,
            send_message_attempts=send_attempts,
            external_side_effect=send_attempts == 1,
            error_code=error_code,
        )
