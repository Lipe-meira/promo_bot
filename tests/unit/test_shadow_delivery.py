from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from promo_bot.config.schema import AppConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.database.models import (
    AffiliateCandidateModel,
    AffiliateLinkProofModel,
    AffiliateShadowPreviewLinkModel,
    AffiliateShadowPreviewModel,
    Base,
    DealModel,
    DeliveryModel,
    SourceMessageLinkModel,
    SourceMessageModel,
)
from promo_bot.database.session import create_affiliate_shadow_database

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
LINK = "https://s.click.aliexpress.com/e/private-fixture"
TEXT = f"Oferta **literal** <b>😀</b>\n{LINK}\nhttps://amazon.com.br/item/test"
TARGET = "-1001111111111"
SOURCE = "-1002222222222"


def config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "source_channels": [SOURCE],
            "templates": ["fixture"],
            "affiliate_disclosure": "fixture",
            "telegram_shadow_delivery": {
                "allowed_destinations": {
                    "private-test": {"chat_id": TARGET, "kind": "private_channel"},
                }
            },
        }
    )


def settings() -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        telegram_bot_token="123:fixture-token",
        telegram_shadow_test_delivery_enabled=True,
    )


def automatic_settings() -> EnvironmentSettings:
    return EnvironmentSettings(
        _env_file=None,
        telegram_bot_token="123:fixture-token",
        aliexpress_live_api_enabled=True,
        aliexpress_telegram_shadow_auto_delivery_enabled=True,
        dry_run=True,
        publish_real_deals=False,
        publish_without_affiliate=False,
        search_enabled=False,
        coupon_browser_verification=False,
    )


async def seed(path: Path) -> int:
    database = create_affiliate_shadow_database(path)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        source = SourceMessageModel(
            platform="telegram",
            message_id="fixture",
            channel_id=SOURCE,
            occurred_at=NOW,
            original_text=TEXT,
            links=[],
            content_hash="fixture",
            processing_status="COMPLETED",
        )
        candidate = AffiliateCandidateModel(
            store="aliexpress",
            external_product_id="12345",
            variation_key="",
            canonical_url="https://www.aliexpress.com/item/12345.html",
        )
        session.add_all([source, candidate])
        await session.flush()
        session.add(
            SourceMessageLinkModel(
                source_message_id=source.id,
                ordinal=0,
                source_kind="TEXT",
                input_hash="fixture",
                input_url=candidate.canonical_url,
                store=candidate.store,
                external_product_id=candidate.external_product_id,
                canonical_url=candidate.canonical_url,
                affiliate_candidate_id=candidate.id,
                state="PENDING_AFFILIATE",
            )
        )
        proof = AffiliateLinkProofModel(
            candidate_id=candidate.id,
            provider="aliexpress_official",
            operation="aliexpress.affiliate.link.generate",
            requested_at=NOW,
            responded_at=NOW,
            source_external_product_id="12345",
            canonical_url=candidate.canonical_url,
            short_link=LINK,
            official_endpoint_host="api-sg.aliexpress.com",
            credential_profile_id="configured",
            contract_version="fixture",
            generation_state="CONFIRMED",
            official_response_validated=True,
            expires_at=NOW + timedelta(hours=24),
        )
        session.add(proof)
        await session.flush()
        preview = AffiliateShadowPreviewModel(
            source_message_id=source.id,
            affiliate_proof_id=proof.id,
            provider=proof.provider,
            store="aliexpress",
            replacement_count=1,
            cache_hit=False,
            affiliate_host="s.click.aliexpress.com",
            rendered_text=TEXT,
            affiliate_link=LINK,
            content_expires_at=NOW + timedelta(hours=24),
            created_at=NOW,
            status="READY",
        )
        session.add(preview)
        await session.flush()
        result = preview.id
    await database.dispose()
    return result


class FakeTransport:
    def __init__(
        self, database=None, *, get_error=None, send_error=None, public=False, message_id="42"
    ):
        self.database = database
        self.get_error, self.send_error = get_error, send_error
        self.public, self.message_id = public, message_id
        self.gets, self.sends = [], []

    async def get_chat(self, chat_id: str):
        self.gets.append(chat_id)
        if self.get_error:
            raise self.get_error
        return SimpleNamespace(
            id=int(chat_id),
            type="channel",
            username="public" if self.public else None,
            active_usernames=(),
        )

    async def send_text(self, chat_id: str, text: str):
        if self.database:
            from promo_bot.database.shadow_delivery_repository import ShadowDeliveryModel

            async with self.database.session() as session:
                row = (await session.execute(select(ShadowDeliveryModel))).scalar_one()
                assert row.state == "sending" and row.attempt_count == 1
                assert row.source_message_id is not None
        self.sends.append((chat_id, text))
        await asyncio.sleep(0)
        if self.send_error:
            raise self.send_error
        return self.message_id


async def deliver(database, transport, preview_id, **kwargs):
    from promo_bot.affiliate.shadow_delivery import ShadowDeliveryService

    return await ShadowDeliveryService(
        database, transport, settings(), config(), clock=lambda: NOW
    ).deliver(preview_id, "private-test", confirm=True, **kwargs)


@pytest.mark.asyncio
async def test_single_send_is_committed_first_and_never_repeated(tmp_path: Path) -> None:
    preview_id = await seed(tmp_path / "shadow.sqlite3")
    database = create_affiliate_shadow_database(tmp_path / "shadow.sqlite3")
    transport = FakeTransport(database)
    try:
        report = await deliver(database, transport, preview_id)
        assert report["status"] == "sent"
        assert report["external_side_effect"] is True
        assert report["production_publication"] is False
        assert report["get_chat_attempts"] == report["send_message_attempts"] == 1
        assert transport.sends == [(TARGET, TEXT)]
        duplicate = await deliver(database, transport, preview_id)
        assert duplicate["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
        assert duplicate["send_message_attempts"] == 0
        assert len(transport.sends) == 1
        async with database.session() as session:
            assert await session.scalar(select(func.count(DealModel.id))) == 0
            assert await session.scalar(select(func.count(DeliveryModel.id))) == 0
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_automatic_authorization_is_separate_and_counts_actual_send(tmp_path: Path) -> None:
    from promo_bot.affiliate.shadow_delivery import (
        ShadowDeliveryService,
        authorize_automatic_shadow_delivery,
    )

    preview_id = await seed(tmp_path / "automatic.sqlite3")
    database = create_affiliate_shadow_database(tmp_path / "automatic.sqlite3")
    transport = FakeTransport(database)
    dispatched = 0

    async def before_send() -> None:
        nonlocal dispatched
        dispatched += 1

    try:
        authorization = authorize_automatic_shadow_delivery(
            automatic_settings(), config(), destination="private-test"
        )
        report = await ShadowDeliveryService(
            database,
            transport,
            automatic_settings(),
            config(),
            clock=lambda: NOW,
        ).deliver_automatic(
            preview_id,
            "private-test",
            authorization=authorization,
            before_send=before_send,
        )

        assert report["status"] == "sent"
        assert dispatched == 1
        assert len(transport.sends) == 1
        with pytest.raises(ValueError, match="TELEGRAM_SHADOW_TEST_DELIVERY_DISABLED"):
            await ShadowDeliveryService(
                database,
                FakeTransport(),
                automatic_settings(),
                config(),
                clock=lambda: NOW,
            ).deliver(preview_id, "private-test", confirm=True)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_multi_link_delivery_rejects_tampered_secondary_correlation(tmp_path: Path) -> None:
    path = tmp_path / "tampered-multi.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    second_link = "https://s.click.aliexpress.com/e/second-fixture"
    try:
        async with database.session() as session:
            source = (await session.execute(select(SourceMessageModel))).scalar_one()
            first_source_link = (await session.execute(select(SourceMessageLinkModel))).scalar_one()
            first_proof = (await session.execute(select(AffiliateLinkProofModel))).scalar_one()
            preview = await session.get(AffiliateShadowPreviewModel, preview_id)
            assert preview is not None
            second_candidate = AffiliateCandidateModel(
                store="aliexpress",
                external_product_id="67890",
                variation_key="",
                canonical_url="https://www.aliexpress.com/item/67890.html",
            )
            session.add(second_candidate)
            await session.flush()
            second_source = SourceMessageLinkModel(
                source_message_id=source.id,
                ordinal=1,
                source_kind="TEXT",
                input_hash="second-fixture",
                input_url=second_candidate.canonical_url,
                store="aliexpress",
                external_product_id="67890",
                canonical_url=second_candidate.canonical_url,
                affiliate_candidate_id=second_candidate.id,
                state="PENDING_AFFILIATE",
            )
            session.add(second_source)
            second_proof = AffiliateLinkProofModel(
                candidate_id=second_candidate.id,
                provider="aliexpress_official",
                operation="aliexpress.affiliate.link.generate",
                requested_at=NOW,
                responded_at=NOW,
                source_external_product_id="99999",
                canonical_url=second_candidate.canonical_url,
                short_link=second_link,
                official_endpoint_host="api-sg.aliexpress.com",
                credential_profile_id="configured",
                contract_version="fixture",
                generation_state="CONFIRMED",
                official_response_validated=True,
                expires_at=NOW + timedelta(hours=24),
            )
            session.add(second_proof)
            await session.flush()
            preview.rendered_text = f"{TEXT}\n{second_link}"
            preview.replacement_count = 2
            session.add_all(
                [
                    AffiliateShadowPreviewLinkModel(
                        preview_id=preview.id,
                        source_message_link_id=first_source_link.id,
                        affiliate_proof_id=first_proof.id,
                        ordinal=0,
                        occurrence_count=1,
                        cache_hit=False,
                    ),
                    AffiliateShadowPreviewLinkModel(
                        preview_id=preview.id,
                        source_message_link_id=second_source.id,
                        affiliate_proof_id=second_proof.id,
                        ordinal=1,
                        occurrence_count=1,
                        cache_hit=False,
                    ),
                ]
            )

        transport = FakeTransport()
        report = await deliver(database, transport, preview_id)

        assert report["status"] == "failed_safe"
        assert report["error_code"] == "SHADOW_PROOF_MISMATCH"
        assert transport.sends == []
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "override",
    [
        {"aliexpress_telegram_shadow_auto_delivery_enabled": False},
        {"aliexpress_telegram_shadow_enabled": True},
        {"aliexpress_telegram_shadow_listener_enabled": True},
        {"telegram_shadow_test_delivery_enabled": True},
        {"dry_run": False},
        {"publish_real_deals": True},
        {"publish_without_affiliate": True},
        {"search_enabled": True},
        {"coupon_browser_verification": True},
    ],
)
def test_automatic_authorization_fails_closed_for_every_gate(override: dict[str, object]) -> None:
    from promo_bot.affiliate.shadow_delivery import authorize_automatic_shadow_delivery

    payload = automatic_settings().model_dump()
    payload.update(override)
    with pytest.raises(ValueError, match="SHADOW_AUTO_DELIVERY"):
        authorize_automatic_shadow_delivery(
            EnvironmentSettings(_env_file=None, **payload),
            config(),
            destination="private-test",
        )


@pytest.mark.asyncio
async def test_concurrent_deliveries_send_once(tmp_path: Path) -> None:
    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    first, second = create_affiliate_shadow_database(path), create_affiliate_shadow_database(path)
    transport = FakeTransport()
    try:
        reports = await asyncio.gather(
            deliver(first, transport, preview_id), deliver(second, transport, preview_id)
        )
        assert sum(r["send_message_attempts"] for r in reports) == 1
        assert sum(r["status"] == "sent" for r in reports) == 1
        assert len(transport.sends) == 1
    finally:
        await first.dispose()
        await second.dispose()


@pytest.mark.parametrize(
    "model,field,value,code",
    [
        (AffiliateShadowPreviewModel, "status", "EXPIRED", "SHADOW_PREVIEW_NOT_READY"),
        (AffiliateShadowPreviewModel, "content_expires_at", NOW, "SHADOW_PREVIEW_EXPIRED"),
        (AffiliateShadowPreviewModel, "rendered_text", None, "SHADOW_PREVIEW_CONTENT_MISSING"),
        (AffiliateShadowPreviewModel, "rendered_text", "x" * 4097, "SHADOW_MESSAGE_TOO_LONG"),
        (AffiliateShadowPreviewModel, "rendered_text", "😀" * 2049, "SHADOW_MESSAGE_TOO_LONG"),
        (AffiliateLinkProofModel, "expires_at", NOW, "SHADOW_PROOF_EXPIRED"),
        (AffiliateLinkProofModel, "generation_state", "PENDING", "SHADOW_PROOF_INVALID"),
        (AffiliateLinkProofModel, "official_response_validated", False, "SHADOW_PROOF_INVALID"),
        (AffiliateLinkProofModel, "provider", "other", "SHADOW_PROOF_MISMATCH"),
        (
            AffiliateLinkProofModel,
            "short_link",
            "https://other.invalid/e/test",
            "SHADOW_PROOF_MISMATCH",
        ),
        (AffiliateCandidateModel, "store", "kabum", "SHADOW_PROOF_MISMATCH"),
        (SourceMessageModel, "channel_id", TARGET, "SHADOW_DESTINATION_IS_SOURCE"),
        (SourceMessageLinkModel, "affiliate_candidate_id", None, "SHADOW_PROOF_MISMATCH"),
    ],
    ids=[
        "not-ready",
        "preview-expired",
        "no-content",
        "too-long",
        "utf16-limit",
        "proof-expired",
        "proof-pending",
        "unvalidated",
        "provider",
        "link",
        "store",
        "origin",
        "source-correlation",
    ],
)
@pytest.mark.asyncio
async def test_invalid_preview_rejected_before_network(tmp_path, model, field, value, code):
    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    try:
        async with database.session() as session:
            row = (await session.execute(select(model))).scalar_one()
            setattr(row, field, value)
        report = await deliver(database, transport, preview_id)
        assert report["status"] == "failed_safe"
        assert report["error_code"] == code
        assert report["get_chat_attempts"] == report["send_message_attempts"] == 0
        assert transport.gets == transport.sends == []
        again = await deliver(database, transport, preview_id)
        assert again["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "case", ["unknown-alias", "numeric-alias", "source-username", "source-target"]
)
@pytest.mark.asyncio
async def test_destination_boundaries_fail_closed(tmp_path, case):
    from promo_bot.affiliate.shadow_delivery import ShadowDeliveryService

    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    app_config = config()
    alias = "private-test"
    if case == "source-username":
        app_config = app_config.model_copy(update={"source_channels": ["@fixture"]})
    elif case == "source-target":
        app_config = app_config.model_copy(update={"source_channels": [TARGET]})
    else:
        alias = "unknown" if case == "unknown-alias" else TARGET
    try:
        service = ShadowDeliveryService(
            database, transport, settings(), app_config, clock=lambda: NOW
        )
        if case == "source-target":
            report = await service.deliver(preview_id, alias, confirm=True)
            assert report["error_code"] == "SHADOW_DESTINATION_IS_SOURCE"
        else:
            with pytest.raises(ValueError, match="SHADOW_"):
                await service.deliver(preview_id, alias, confirm=True)
        assert transport.gets == transport.sends == []
    finally:
        await database.dispose()


@pytest.mark.parametrize("case", ["sending-commit", "expiry-during-get", "explicit-rejection"])
@pytest.mark.asyncio
async def test_durable_preflight_and_definitive_rejection(tmp_path, monkeypatch, case):
    from promo_bot.affiliate.shadow_delivery import DefinitiveSendRejection
    from promo_bot.database.shadow_delivery_repository import ShadowDeliveryRepository

    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    if case == "sending-commit":

        async def fail(*args, **kwargs):
            raise RuntimeError("private SQL")

        monkeypatch.setattr(ShadowDeliveryRepository, "mark_sending", fail)
    elif case == "expiry-during-get":
        original_get = transport.get_chat

        async def expire(chat_id):
            async with database.session() as session:
                preview = await session.get(AffiliateShadowPreviewModel, preview_id)
                preview.content_expires_at = NOW
            return await original_get(chat_id)

        transport.get_chat = expire
    else:
        transport.send_error = DefinitiveSendRejection("private response")
    try:
        report = await deliver(database, transport, preview_id)
        assert report["status"] == "failed_safe"
        assert report["send_message_attempts"] == (1 if case == "explicit-rejection" else 0)
        duplicate = await deliver(database, transport, preview_id)
        assert duplicate["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_service_is_provider_neutral_and_alias_rename_cannot_resend(tmp_path):
    from promo_bot.affiliate.shadow_delivery import ShadowDeliveryService

    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    try:
        async with database.session() as session:
            for model in (
                AffiliateShadowPreviewModel,
                AffiliateCandidateModel,
                SourceMessageLinkModel,
            ):
                row = (await session.execute(select(model))).scalar_one()
                row.store = "kabum"
            for model in (AffiliateShadowPreviewModel, AffiliateLinkProofModel):
                row = (await session.execute(select(model))).scalar_one()
                row.provider = "future_provider_fixture"
        report = await deliver(database, transport, preview_id)
        assert report["status"] == "sent"
        app_config = config()
        destinations = app_config.telegram_shadow_delivery.allowed_destinations
        destinations["renamed-test"] = destinations.pop("private-test")
        report = await ShadowDeliveryService(
            database, transport, settings(), app_config, clock=lambda: NOW
        ).deliver(preview_id, "renamed-test", confirm=True)
        assert report["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
        assert len(transport.sends) == 1
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("telegram_shadow_test_delivery_enabled", False),
        ("dry_run", False),
        ("publish_real_deals", True),
        ("publish_without_affiliate", True),
        ("search_enabled", True),
        ("telegram_bot_token", None),
    ],
)
@pytest.mark.asyncio
async def test_closed_gates_never_call_transport(tmp_path, field, value):
    from promo_bot.affiliate.shadow_delivery import ShadowDeliveryService

    database = create_affiliate_shadow_database(tmp_path / "unused.sqlite3")
    transport = FakeTransport()
    try:
        with pytest.raises(ValueError):
            await ShadowDeliveryService(
                database, transport, settings().model_copy(update={field: value}), config()
            ).deliver(1, "private-test", confirm=True)
        assert transport.gets == transport.sends == []
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_missing_preview_has_no_network(tmp_path):
    path = tmp_path / "shadow.sqlite3"
    await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    try:
        report = await deliver(database, transport, 999)
        assert report["error_code"] == "SHADOW_PREVIEW_NOT_FOUND"
        assert transport.gets == transport.sends == []
    finally:
        await database.dispose()


@pytest.mark.parametrize("state", ["pending", "sending", "sent", "failed_safe", "uncertain"])
@pytest.mark.asyncio
async def test_every_existing_state_blocks_repeated_send(tmp_path, state):
    import hashlib

    from promo_bot.database.models import ShadowDeliveryModel

    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    try:
        async with database.session() as session:
            session.add(
                ShadowDeliveryModel(
                    preview_id=preview_id,
                    source_message_id=1,
                    destination_key=hashlib.sha256(f"telegram:{TARGET}".encode()).hexdigest(),
                    state=state,
                    attempt_count=1,
                )
            )
        report = await deliver(database, transport, preview_id)
        assert report["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
        assert report["persisted_state"] == state
        assert report["get_chat_attempts"] == report["send_message_attempts"] == 0
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_repository_and_service_reject_main_database(tmp_path):
    from promo_bot.affiliate.shadow_delivery import ShadowDeliveryService
    from promo_bot.database.session import Database
    from promo_bot.database.shadow_delivery_repository import ShadowDeliveryRepository

    database = Database(f"sqlite+aiosqlite:///{(tmp_path / 'main.sqlite3').as_posix()}")
    try:
        with pytest.raises(ValueError, match="AFFILIATE_SHADOW_DATABASE_REQUIRED"):
            ShadowDeliveryService(database, FakeTransport(), settings(), config())
        async with database.session() as session:
            with pytest.raises(ValueError, match="AFFILIATE_SHADOW_DATABASE_REQUIRED"):
                ShadowDeliveryRepository(session)
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "options,state,get_count,send_count",
    [
        ({"get_error": TimeoutError("private detail")}, "failed_safe", 1, 0),
        ({"public": True}, "failed_safe", 1, 0),
        ({"send_error": TimeoutError("private detail")}, "uncertain", 1, 1),
        ({"send_error": ConnectionError("private detail")}, "uncertain", 1, 1),
        ({"message_id": ""}, "uncertain", 1, 1),
    ],
)
@pytest.mark.asyncio
async def test_failures_have_no_retry_and_are_sanitized(
    tmp_path, options, state, get_count, send_count, caplog
):
    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport(**options)
    try:
        report = await deliver(database, transport, preview_id)
        assert report["status"] == state
        assert report["get_chat_attempts"] == get_count
        assert report["send_message_attempts"] == send_count
        again = await deliver(database, transport, preview_id)
        assert again["send_message_attempts"] == 0
        assert len(transport.sends) == send_count
        for sensitive in (TARGET, TEXT, LINK, "private detail", "fixture-token"):
            assert sensitive not in str(report) + caplog.text
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_final_persistence_failure_stays_sending_and_cannot_resend(tmp_path, monkeypatch):
    from promo_bot.database.shadow_delivery_repository import (
        ShadowDeliveryModel,
        ShadowDeliveryRepository,
    )

    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()

    async def fail(*args, **kwargs):
        raise RuntimeError("private SQL values")

    monkeypatch.setattr(ShadowDeliveryRepository, "finish", fail)
    try:
        report = await deliver(database, transport, preview_id)
        assert report["status"] == "uncertain"
        assert report["persisted_state"] == "sending"
        async with database.session() as session:
            row = (await session.execute(select(ShadowDeliveryModel))).scalar_one()
            assert row.state == "sending"
        again = await deliver(database, transport, preview_id)
        assert again["status"] == "uncertain"
        assert again["send_message_attempts"] == 0
        assert len(transport.sends) == 1
    finally:
        await database.dispose()


@pytest.mark.parametrize("fail_state", ["sending", "sent"])
@pytest.mark.asyncio
async def test_actual_transaction_commit_failure_never_retries(tmp_path, monkeypatch, fail_state):
    from sqlalchemy.ext.asyncio import AsyncSession

    from promo_bot.database.models import ShadowDeliveryModel

    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    original_commit = AsyncSession.commit

    async def failing_commit(session):
        state = await session.scalar(select(ShadowDeliveryModel.state))
        if state == fail_state:
            raise RuntimeError("private commit payload")
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    try:
        report = await deliver(database, transport, preview_id)
        assert report["status"] == ("uncertain" if fail_state == "sent" else "failed_safe")
        assert report["send_message_attempts"] == (1 if fail_state == "sent" else 0)
        again = await deliver(database, transport, preview_id)
        assert again["send_message_attempts"] == 0
        assert again["error_code"] == "SHADOW_DELIVERY_ALREADY_ATTEMPTED"
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "field,value",
    [("id", -10099999), ("type", "supergroup"), ("active_usernames", ("public_fixture",))],
)
@pytest.mark.asyncio
async def test_get_chat_must_match_private_channel(tmp_path, field, value):
    path = tmp_path / "shadow.sqlite3"
    preview_id = await seed(path)
    database = create_affiliate_shadow_database(path)
    transport = FakeTransport()
    original_get = transport.get_chat

    async def mismatched(chat_id):
        chat = await original_get(chat_id)
        setattr(chat, field, value)
        return chat

    transport.get_chat = mismatched
    try:
        report = await deliver(database, transport, preview_id)
        assert report["error_code"] == "SHADOW_DESTINATION_NOT_PRIVATE_CHANNEL"
        assert report["send_message_attempts"] == 0
        assert transport.sends == []
    finally:
        await database.dispose()
