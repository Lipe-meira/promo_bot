"""Admission of message creation time, not arrival time, at the real pilot entrypoint."""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from promo_bot.affiliate.aliexpress_shadow_listener import ShadowRunController, ShadowRunLimits
from promo_bot.cli import main
from promo_bot.config.schema import AppConfig, TelegramRelayConfig
from promo_bot.config.settings import EnvironmentSettings
from promo_bot.observability.logging import SafeJsonFormatter
from promo_bot.telegram.monitor import (
    TelegramMessageReference,
    TelegramMonitor,
    TelegramOneShotReader,
)
from tests.unit.test_coin_listener_pilot import _argv, _config_file
from tests.unit.test_coin_shadow_multi_contract import A, B
from tests.unit.test_coin_shadow_multi_entrypoint import install_runtime
from tests.unit.test_telegram_monitor_offline import (
    FakeMessage,
    FakeReadOnlyClient,
    FakeResolvedChannel,
    make_event_monitor,
)

READY = datetime(2026, 10, 14, 13, 45, 44, 142454, tzinfo=UTC)


@pytest.mark.parametrize("multi", [False, True])
def test_old_message_arriving_after_ready_is_excluded_before_persistence_and_transport(
    tmp_path, monkeypatch, capsys, caplog, multi
):
    caplog.set_level(logging.INFO, logger="promo_bot.telegram.monitor")
    requests, sends = [], []
    content = f"APP {A}\nPC {B}" if multi else A
    install_runtime(
        monkeypatch,
        [content],
        requests,
        sends,
        dates=[READY - timedelta(minutes=10)],
        ready_at=READY,
    )
    path = tmp_path / "admission.sqlite3"
    argv = _argv(_config_file(tmp_path), path)
    argv[argv.index("--run-seconds") + 1] = "0.1"
    if multi:
        argv.append("--allow-multiple-coin-shorts")
        argv[argv.index("--max-links-per-message") + 1] = "3"
        argv[argv.index("--max-api-calls") + 1] = "2"

    assert main(argv) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["messages_received"] == 0, report
    assert report["api_calls"] == report["send_messages"] == report["deliveries_sent"] == 0
    assert report["processed"] == report["rejected"] == report["skipped"] == 0
    assert report["excluded_events"] == 1
    assert report["exclusion_codes"] == ["TELEGRAM_MESSAGE_BEFORE_WINDOW"]
    assert requests == sends == []
    with sqlite3.connect(path) as connection:
        for table in (
            "source_messages",
            "affiliate_link_generations",
            "affiliate_link_uses",
            "aliexpress_coin_shadow_evidence",
            "aliexpress_coin_shadow_previews",
            "aliexpress_coin_shadow_multi_previews",
            "aliexpress_coin_shadow_deliveries",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    for sensitive in (A, B, "synthetic-tracking", content):
        assert sensitive not in captured.out + captured.err + caplog.text


@pytest.mark.asyncio
async def test_excluded_event_emits_sanitized_operational_log(monkeypatch, caplog):
    # Temporary Alembic fixture setup can disable existing loggers. Exercise the
    # real handler's log outside that setup, without suppressing its side effects.
    logger = logging.getLogger("promo_bot.telegram.monitor")
    monkeypatch.setattr(logger, "disabled", False)
    caplog.set_level(logging.INFO, logger=logger.name)
    monitor, relay, _client = make_event_monitor(frozenset({"-1001234567890"}))
    controller = ShadowRunController(ShadowRunLimits(1, 1, 1, 1))
    controller.mark_ready()
    monitor._bounded = controller
    monitor._bounded_ready_second = READY.replace(microsecond=0)
    message = FakeMessage(12, own=False, text=f"sensitive-title {A}")
    message.date = READY - timedelta(minutes=5)
    await monitor._handle_new_message(SimpleNamespace(chat_id=-1001234567890, message=message))
    records = [
        record
        for record in caplog.records
        if getattr(record, "stage", None) == "telegram_admission"
    ]
    assert len(records) == 1
    rendered = SafeJsonFormatter().format(records[0])
    assert json.loads(rendered)["error_code"] == "TELEGRAM_MESSAGE_BEFORE_WINDOW"
    assert "sensitive-title" not in rendered and A not in rendered
    assert relay.messages == []


@pytest.mark.parametrize(
    "date,admitted,code",
    [
        (datetime(2026, 10, 14, 13, 45, 43, tzinfo=UTC), False, "TELEGRAM_MESSAGE_BEFORE_WINDOW"),
        (
            datetime(2026, 10, 14, 13, 45, 44, tzinfo=UTC),
            False,
            "TELEGRAM_MESSAGE_READY_SECOND_AMBIGUOUS",
        ),
        (
            datetime(2026, 10, 14, 13, 45, 44, 999999, tzinfo=UTC),
            False,
            "TELEGRAM_MESSAGE_READY_SECOND_AMBIGUOUS",
        ),
        (datetime(2026, 10, 14, 13, 45, 45, tzinfo=UTC), True, None),
        (
            datetime(2026, 10, 14, 10, 45, 44, tzinfo=timezone(timedelta(hours=-3))),
            False,
            "TELEGRAM_MESSAGE_READY_SECOND_AMBIGUOUS",
        ),
        (datetime(2026, 10, 14, 10, 45, 45, tzinfo=timezone(timedelta(hours=-3))), True, None),
        (datetime(2026, 10, 14, 15, 45, 45, tzinfo=timezone(timedelta(hours=2))), True, None),
        (datetime(2026, 10, 14, 13, 45, 45), False, "TELEGRAM_MESSAGE_TIMESTAMP_INVALID"),
        (None, False, "TELEGRAM_MESSAGE_TIMESTAMP_INVALID"),
        ("invalid-secret-value", False, "TELEGRAM_MESSAGE_TIMESTAMP_INVALID"),
        (123, False, "TELEGRAM_MESSAGE_TIMESTAMP_INVALID"),
    ],
)
def test_creation_timestamp_boundary_utc_and_invalid_values(
    tmp_path, monkeypatch, capsys, date, admitted, code
):
    requests, sends = [], []
    install_runtime(monkeypatch, [A], requests, sends, dates=[date], ready_at=READY)
    path = tmp_path / "boundary.sqlite3"
    argv = _argv(_config_file(tmp_path), path)
    argv[argv.index("--run-seconds") + 1] = "0.2"
    assert main(argv) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    expected_count = int(admitted)
    assert (
        report["messages_received"]
        == report["api_calls"]
        == report["deliveries_sent"]
        == expected_count
    )
    assert report["excluded_events"] == int(not admitted)
    assert report["exclusion_codes"] == ([] if admitted else [code])
    assert len(requests) == len(sends) == expected_count
    assert "invalid-secret-value" not in captured.out + captured.err


def test_missing_timestamp_fails_closed_without_consuming_slots(tmp_path, monkeypatch, capsys):
    requests, sends = [], []
    install_runtime(monkeypatch, [A], requests, sends, ready_at=READY, missing_date=True)
    argv = _argv(_config_file(tmp_path), tmp_path / "missing.sqlite3")
    argv[argv.index("--run-seconds") + 1] = "0.1"
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["messages_received"] == report["api_calls"] == report["send_messages"] == 0
    assert report["exclusion_codes"] == ["TELEGRAM_MESSAGE_TIMESTAMP_INVALID"]
    assert requests == sends == []


@pytest.mark.parametrize("multi", [False, True])
def test_excluded_events_leave_slots_for_new_message_and_duplicate_has_no_second_send(
    tmp_path, monkeypatch, capsys, multi
):
    requests, sends = [], []
    content = f"APP {A}\nPC {B}" if multi else A
    install_runtime(
        monkeypatch,
        [content, content],
        requests,
        sends,
        dates=[READY - timedelta(minutes=10), READY + timedelta(seconds=1)],
        ready_at=READY,
    )
    path = tmp_path / "new.sqlite3"
    argv = _argv(_config_file(tmp_path), path)
    if multi:
        argv.append("--allow-multiple-coin-shorts")
        argv[argv.index("--max-links-per-message") + 1] = "3"
        argv[argv.index("--max-api-calls") + 1] = "2"
    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["excluded_events"] == first["messages_received"] == first["deliveries_sent"] == 1
    assert first["api_calls"] == len(requests) == (2 if multi else 1)
    assert len(sends) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT message_id FROM source_messages").fetchall() == [("102",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM aliexpress_coin_shadow_deliveries"
        ).fetchone() == (1,)
    # Restart observes the same eligible message identity, not a new delivery.
    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["excluded_events"] == second["messages_received"] == second["skipped"] == 1
    assert second["api_calls"] == second["send_messages"] == 0
    assert len(sends) == 1


@pytest.mark.parametrize("creation_is_new", [False, True])
def test_forward_date_cannot_change_creation_time_admission(
    tmp_path, monkeypatch, capsys, creation_is_new
):
    requests, sends = [], []
    creation = READY + timedelta(seconds=1) if creation_is_new else READY - timedelta(days=1)
    forwarded = READY - timedelta(days=1) if creation_is_new else READY + timedelta(seconds=1)
    install_runtime(
        monkeypatch,
        [A],
        requests,
        sends,
        dates=[creation],
        ready_at=READY,
        forward_dates=[forwarded],
    )
    argv = _argv(_config_file(tmp_path), tmp_path / "forward.sqlite3")
    argv[argv.index("--run-seconds") + 1] = "0.2"
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["messages_received"] == report["deliveries_sent"] == int(creation_is_new)
    assert len(sends) == int(creation_is_new)


def test_exclusions_do_not_become_unfinished_accepted_messages():
    controller = ShadowRunController(ShadowRunLimits(1, 1, 1, 1))
    controller.record_excluded("TELEGRAM_MESSAGE_BEFORE_WINDOW")
    assert controller.messages_received == controller.processed == controller.skipped == 0
    assert controller.api_calls == controller.send_messages == 0
    assert controller.stop_reason is None
    assert controller.reconcile_unfinished("SHADOW_UNFINISHED") == 0


@pytest.mark.asyncio
async def test_normal_event_handler_still_accepts_historical_message():
    monitor, relay, _client = make_event_monitor(frozenset({"-1001234567890"}))
    message = FakeMessage(12, own=False, text="normal-monitor-fixture")
    message.date = READY - timedelta(days=1)
    await monitor._handle_new_message(SimpleNamespace(chat_id=-1001234567890, message=message))
    assert len(relay.messages) == 1
    assert relay.messages[0].occurred_at == READY - timedelta(days=1)


@pytest.mark.asyncio
async def test_manual_reader_still_fetches_historical_message():
    entity = object()
    message = FakeMessage(12, own=False)
    message.date = READY - timedelta(days=1)
    client = FakeReadOnlyClient(
        resolved={-1001234567890: FakeResolvedChannel(-1001234567890, -1001234567890, entity)},
        message=message,
    )
    reader = TelegramOneShotReader(client, source_channels=("-1001234567890",))
    incoming = await reader.fetch(TelegramMessageReference(12, chat_id=-1001234567890))
    assert incoming.occurred_at == READY - timedelta(days=1)


@pytest.mark.asyncio
async def test_normal_monitor_deliberate_catchup_still_selects_previous_messages():
    message = FakeMessage(12, own=False)
    message.date = READY - timedelta(minutes=5)

    class RecentClient:
        async def iter_messages(self, *_args, **_kwargs):
            yield message

    config = AppConfig(
        source_channels=("-1001234567890",),
        telegram_relay=TelegramRelayConfig(catch_up_on_start=True),
        affiliate_disclosure="fixture",
    )
    monitor = TelegramMonitor(
        EnvironmentSettings(_env_file=None),
        config,
        SimpleNamespace(),
        clock=lambda: READY,
        client=RecentClient(),
    )
    selected = await monitor._fetch_recent(object(), "-1001234567890", None)
    assert [incoming.message_id for incoming in selected] == [12]
    assert selected[0].occurred_at == READY - timedelta(minutes=5)
