"""Durable processing of persisted Telegram messages."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from promo_bot.config.schema import TelegramRelayConfig
from promo_bot.database.repositories import (
    AffiliateCandidateRepository,
    ProcessedItemRepository,
    SourceMessageLinkRepository,
    SourceMessageRepository,
)
from promo_bot.database.session import Database
from promo_bot.domain.enums import RelayLinkState, Store
from promo_bot.relay.models import ExtractedLink, RelayProcessingError
from promo_bot.relay.retry import BackoffPolicy
from promo_bot.security.urls import (
    SafeUrlError,
    SafeUrlExpander,
    TransientUrlError,
    UrlExpansionResult,
)
from promo_bot.stores.urls import (
    canonicalize_store_url,
    is_aliexpress_redirector_url,
    is_allowed_network_url,
    is_shortener_url,
)

LOGGER = logging.getLogger("promo_bot.relay")


class UrlExpander(Protocol):
    async def expand(self, url: str) -> UrlExpansionResult: ...


class RelayProcessor:
    def __init__(
        self,
        database: Database,
        config: TelegramRelayConfig,
        *,
        expander: UrlExpander | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        self.expander = expander or SafeUrlExpander(
            timeout_seconds=config.http_timeout_seconds,
            max_redirects=config.redirect_max_hops,
        )
        self.backoff = BackoffPolicy(
            initial_seconds=config.retry_initial_seconds,
            maximum_seconds=config.retry_max_seconds,
        )

    async def process(self, internal_id: int) -> None:
        now = self.clock()
        lease_until = now + timedelta(minutes=self.config.processing_stale_after_minutes)
        async with self.database.session() as session:
            claimed = await SourceMessageRepository(session).claim(
                internal_id,
                now=now,
                lease_until=lease_until,
                max_attempts=self.config.processing_max_attempts,
            )
            if claimed is None:
                return
            links = tuple(ExtractedLink.from_dict(item) for item in claimed.links)
            attempt_count = claimed.attempt_count

        try:
            for link in links:
                async with self.database.session() as session:
                    await SourceMessageRepository(session).renew_lease(
                        internal_id,
                        lease_until=self.clock()
                        + timedelta(minutes=self.config.processing_stale_after_minutes),
                    )
                await self._process_link(internal_id, link)
        except RelayProcessingError as exc:
            failure_time = self.clock()
            retry_at = (
                self.backoff.next_attempt_at(failure_time, attempt_count) if exc.retryable else None
            )
            async with self.database.session() as session:
                state = await SourceMessageRepository(session).fail(
                    internal_id,
                    retryable=exc.retryable,
                    max_attempts=self.config.processing_max_attempts,
                    next_attempt_at=retry_at,
                    error_code=exc.code,
                    error_summary=exc.summary,
                )
            LOGGER.warning(
                "relay processing failed",
                extra={
                    "message_id": str(internal_id),
                    "stage": "process",
                    "result": state.value,
                    "error_code": exc.code,
                },
            )
            return
        except Exception:
            failure_time = self.clock()
            async with self.database.session() as session:
                state = await SourceMessageRepository(session).fail(
                    internal_id,
                    retryable=True,
                    max_attempts=self.config.processing_max_attempts,
                    next_attempt_at=self.backoff.next_attempt_at(failure_time, attempt_count),
                    error_code="UNEXPECTED_PROCESSING_ERROR",
                    error_summary=None,
                )
            LOGGER.error(
                "unexpected relay processing failure",
                extra={
                    "message_id": str(internal_id),
                    "stage": "process",
                    "result": state.value,
                    "error_code": "UNEXPECTED_PROCESSING_ERROR",
                },
            )
            return

        async with self.database.session() as session:
            await SourceMessageRepository(session).complete(internal_id, now=self.clock())
        LOGGER.info(
            "relay processing completed",
            extra={
                "message_id": str(internal_id),
                "stage": "process",
                "result": "COMPLETED",
            },
        )

    async def _process_link(self, source_message_id: int, link: ExtractedLink) -> None:
        input_hash = hashlib.sha256(link.url.encode()).hexdigest()
        async with self.database.session() as session:
            record = await SourceMessageLinkRepository(session).record_received(
                source_message_id=source_message_id,
                ordinal=link.ordinal,
                source_kind=link.source.value,
                input_hash=input_hash,
                input_url=link.url,
            )
            link_id = record.id
            if record.state != RelayLinkState.RECEIVED.value:
                return

        expanded_url = link.url
        redirect_count = 0
        if is_aliexpress_redirector_url(link.url):
            await self._set_link_outcome(
                link_id,
                state=RelayLinkState.REJECTED,
                reason_code="ALIEXPRESS_SHORT_URL_UNSUPPORTED",
            )
            return
        if is_shortener_url(link.url):
            try:
                expanded = await self.expander.expand(link.url)
            except TransientUrlError as exc:
                raise RelayProcessingError(exc.code, retryable=True) from exc
            except SafeUrlError as exc:
                await self._set_link_outcome(
                    link_id, state=RelayLinkState.REJECTED, reason_code=exc.code
                )
                return
            expanded_url = expanded.url
            redirect_count = expanded.redirect_count
        elif not is_allowed_network_url(link.url):
            await self._set_link_outcome(
                link_id,
                state=RelayLinkState.IGNORED,
                reason_code="UNSUPPORTED_DOMAIN",
            )
            return

        outcome = canonicalize_store_url(expanded_url)
        if (
            outcome.state is RelayLinkState.PENDING_AFFILIATE
            and outcome.store is not None
            and outcome.external_product_id is not None
            and outcome.canonical_url is not None
        ):
            canonical_hash = hashlib.sha256(outcome.canonical_url.encode()).hexdigest()
            async with self.database.session() as session:
                processed = ProcessedItemRepository(session)
                existing = await processed.find(
                    outcome.store.value,
                    outcome.external_product_id,
                    outcome.variation_key or "",
                )
                if existing is not None and existing.deal_hash == canonical_hash:
                    links = SourceMessageLinkRepository(session)
                    await links.set_outcome(
                        link_id,
                        state=RelayLinkState.IGNORED,
                        reason_code="DUPLICATE_CANONICAL",
                        expanded_url=expanded_url,
                        redirect_count=redirect_count,
                        store=outcome.store.value,
                        external_product_id=outcome.external_product_id,
                        canonical_url=outcome.canonical_url,
                    )
                    if outcome.store in {Store.SHOPEE, Store.ALIEXPRESS}:
                        await AffiliateCandidateRepository(session).ensure_for_link(
                            link_id,
                            variation_key=outcome.variation_key or "",
                        )
                    return
                await processed.record(
                    store=outcome.store.value,
                    external_product_id=outcome.external_product_id,
                    variation_key=outcome.variation_key or "",
                    deal_hash=canonical_hash,
                    details={"state": RelayLinkState.PENDING_AFFILIATE.value},
                )
                await SourceMessageLinkRepository(session).set_outcome(
                    link_id,
                    state=outcome.state,
                    reason_code=outcome.reason_code,
                    expanded_url=expanded_url if expanded_url != link.url else None,
                    redirect_count=redirect_count,
                    store=outcome.store.value,
                    external_product_id=outcome.external_product_id,
                    canonical_url=outcome.canonical_url,
                )
                if outcome.store in {Store.SHOPEE, Store.ALIEXPRESS}:
                    await AffiliateCandidateRepository(session).ensure_for_link(
                        link_id,
                        variation_key=outcome.variation_key or "",
                    )
                LOGGER.info(
                    "canonical candidate retained; Bot API publication is blocked",
                    extra={
                        "store": outcome.store.value,
                        "product": outcome.canonical_url,
                        "stage": "canonicalize",
                        "result": RelayLinkState.PENDING_AFFILIATE.value,
                    },
                )
                return

        await self._set_link_outcome(
            link_id,
            state=outcome.state,
            reason_code=outcome.reason_code,
            expanded_url=expanded_url if expanded_url != link.url else None,
            redirect_count=redirect_count,
            store=outcome.store.value if outcome.store else None,
            external_product_id=outcome.external_product_id,
            canonical_url=outcome.canonical_url,
        )

    async def _set_link_outcome(
        self,
        link_id: int,
        *,
        state: RelayLinkState,
        reason_code: str,
        expanded_url: str | None = None,
        redirect_count: int = 0,
        store: str | None = None,
        external_product_id: str | None = None,
        canonical_url: str | None = None,
    ) -> None:
        async with self.database.session() as session:
            await SourceMessageLinkRepository(session).set_outcome(
                link_id,
                state=state,
                reason_code=reason_code,
                expanded_url=expanded_url,
                redirect_count=redirect_count,
                store=store,
                external_product_id=external_product_id,
                canonical_url=canonical_url,
            )
