import asyncio
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from promo_bot.cli import main
from tests.unit.test_coin_listener_pilot import _argv, _config_file, _settings
from tests.unit.test_coin_shadow_multi_contract import NOW, A, B

CANONICAL = "https://www.aliexpress.com/item/1005000000000001.html"
CANONICAL_SOURCE = "https://pt.aliexpress.com/item/1005000000000001.html"
PROMOTIONS = {
    A: "https://s.click.aliexpress.com/e/_Aa11Bb2",
    B: "https://s.click.aliexpress.com/e/_Cc33Dd4",
    CANONICAL_SOURCE: "https://s.click.aliexpress.com/e/_Ee55Ff6",
}


def install_runtime(monkeypatch, contents, requests, sends):
    from tests.offline_shadow_runtime import install_offline_shadow_runtime

    install_offline_shadow_runtime(monkeypatch)

    class Listener:
        def __init__(self):
            self.tasks = []

        async def connect(self):
            pass

        async def disconnect(self):
            await asyncio.gather(*self.tasks, return_exceptions=True)

        async def is_user_authorized(self):
            return True

        async def get_entity(self, _reference):
            return SimpleNamespace(id=1234567890)

        def add_event_handler(self, callback, _builder):
            async def emit():
                # Serial synthetic arrivals model the manual wait-between-posts pilot.
                for i, content in enumerate(contents):
                    message = SimpleNamespace(
                        id=i + 101,
                        date=NOW,
                        raw_text=content,
                        out=False,
                        buttons=None,
                        media=None,
                        get_entities_text=lambda: [],
                    )
                    await callback(SimpleNamespace(chat_id=-1001234567890, message=message))
                    await asyncio.sleep(0.05)

            self.tasks.append(asyncio.create_task(emit()))

        def remove_event_handler(self, *_a):
            pass

    class Bot:
        def __init__(self, _token):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            pass

        async def inspect_private_channel(self, _id):
            return SimpleNamespace(
                id=-1009876543210,
                type="channel",
                username=None,
                active_usernames=(),
                bot_membership_status="administrator",
                can_post_messages=True,
            )

        async def get_chat(self, _id):
            return SimpleNamespace(
                id=-1009876543210, type="channel", username=None, active_usernames=()
            )

        async def send_text(self, _id, text):
            sends.append(text)
            return str(len(sends) + 50)

    async def handler(request):
        assert (
            request.method == "POST"
            and request.url.params["method"] == "aliexpress.affiliate.link.generate"
        )
        form = parse_qs(request.content.decode())
        assert form["promotion_link_type"] == ["0"] and form["ship_to_country"] == ["BR"]
        source = form["source_values"][0]
        assert source in PROMOTIONS
        requests.append(source)
        return httpx.Response(
            200,
            json={
                "code": "0",
                "aliexpress_affiliate_link_generate_response": {
                    "resp_result": {
                        "resp_code": "200",
                        "result": {
                            "tracking_id": "synthetic-tracking",
                            "promotion_links": [
                                {
                                    "promotion_link": PROMOTIONS[source],
                                    **(
                                        {"source_value": source}
                                        if source == CANONICAL_SOURCE
                                        else {}
                                    ),
                                }
                            ],
                        },
                    }
                },
            },
            request=request,
        )

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=True))
    monkeypatch.setattr("promo_bot.cli.build_telegram_user_client", lambda *_a, **_kw: Listener())
    monkeypatch.setattr(
        "promo_bot.cli.build_offline_safe_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
        ),
    )
    monkeypatch.setattr("promo_bot.cli.ShadowBotTransport", Bot)
    monkeypatch.setattr("promo_bot.telegram.coin_shadow_bot.CoinShadowBotTransport", Bot)
    monkeypatch.setattr("promo_bot.telegram.monitor.utils.get_peer_id", lambda _e: -1001234567890)
    monkeypatch.setattr(
        "promo_bot.cli.AliExpressShortLinkResolver",
        lambda **_kw: pytest.fail("redirect resolver constructed"),
    )


def test_real_entrypoint_multi_pilot_cache_history_and_stdout_sanitization(
    tmp_path, monkeypatch, capsys, caplog
):
    requests, sends = [], []
    contents = [
        f"🔥 APP {A}",
        f"APP {A}\nPC {B}",
        f"{A}\n{B}",
        f"{A}\n{B}\n{A}",
        f"{A}\n{CANONICAL}",
    ]
    install_runtime(monkeypatch, contents, requests, sends)
    path = tmp_path / "pilot.sqlite3"
    argv = [*_argv(_config_file(tmp_path), path), "--allow-multiple-coin-shorts"]
    for flag, value in {
        "--max-messages": "5",
        "--run-seconds": "2",
        "--max-api-calls": "5",
        "--max-send-messages": "5",
        "--max-links-per-message": "3",
    }.items():
        argv[argv.index(flag) + 1] = value
    assert main(argv) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["api_calls"] == 2 and requests == [A, B]
    assert report["send_messages"] == report["deliveries_sent"] == len(sends) == 4
    assert report["cache_hits"] == 2 and report["rejected"] == 1
    assert report["coin_multi"] == {
        "occurrences_admitted": 8,
        "distinct_inputs_admitted": 7,
        "cache_distinct_inputs": 5,
        "generated_distinct_inputs_confirmed": 2,
        "in_message_reuses": 1,
        "all_cache_messages": 2,
        "partial_cache_messages": 1,
    }
    for secret in [A, B, *PROMOTIONS.values(), "synthetic-tracking", "APP"]:
        assert secret not in captured.out + captured.err + caplog.text
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM affiliate_link_generations WHERE state='CONFIRMED'"
        ).fetchone() == (2,)
        assert conn.execute(
            "SELECT COUNT(*) FROM affiliate_link_uses WHERE state='SEND_CONFIRMED'"
        ).fetchone() == (4,)
        assert conn.execute("SELECT COUNT(*) FROM deals").fetchone() == (0,)
    # Restart with the same identities never generates or sends again.
    assert main(argv) == 0
    after = json.loads(capsys.readouterr().out)
    assert after["api_calls"] == after["send_messages"] == 0
    assert len(requests) == 2 and len(sends) == 4


def test_opt_in_keeps_single_canonical_supported_without_enabling_canonical_batch(
    tmp_path, monkeypatch, capsys
):
    requests, sends = [], []
    install_runtime(monkeypatch, [CANONICAL], requests, sends)
    argv = [
        *_argv(_config_file(tmp_path), tmp_path / "canonical.sqlite3"),
        "--allow-multiple-coin-shorts",
    ]
    argv[argv.index("--max-links-per-message") + 1] = "3"
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert requests == [CANONICAL_SOURCE] and report["deliveries_sent"] == 1


def test_second_multi_instance_fails_before_transports(tmp_path, monkeypatch, capsys):
    from promo_bot.affiliate.shadow_listener_lock import ShadowListenerLock

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=True))

    async def forbidden(*_a, **_kw):
        pytest.fail("transport built behind listener lock")

    monkeypatch.setattr("promo_bot.cli.run_aliexpress_shadow_auto_delivery", forbidden)
    path = tmp_path / "locked.sqlite3"
    argv = [*_argv(_config_file(tmp_path), path), "--allow-multiple-coin-shorts"]
    argv[argv.index("--max-links-per-message") + 1] = "3"
    with ShadowListenerLock(path):
        assert main(argv) == 2
    assert "SHADOW_LISTENER_ALREADY_ACTIVE" in capsys.readouterr().err
    assert not path.exists()


def test_multi_requires_current_schema_before_any_transport_and_never_migrates_old_db(
    tmp_path, monkeypatch, capsys
):
    from alembic import command

    from promo_bot.database.session import create_affiliate_shadow_database
    from tests.unit.test_affiliate_history_schema import config_for

    path = tmp_path / "old-schema.sqlite3"
    command.upgrade(config_for(path), "9b3d5e7f1a20")

    async def old_database(_path):
        return create_affiliate_shadow_database(path)

    monkeypatch.setattr("promo_bot.cli._open_durable_shadow_database", old_database)
    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=True))
    monkeypatch.setattr(
        "promo_bot.cli.build_telegram_user_client",
        lambda *_a, **_kw: pytest.fail("listener constructed before schema check"),
    )
    argv = [*_argv(_config_file(tmp_path), path), "--allow-multiple-coin-shorts"]
    argv[argv.index("--max-links-per-message") + 1] = "3"
    assert main(argv) == 2
    assert "COIN_MULTI_SCHEMA_REQUIRED" in capsys.readouterr().err
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "9b3d5e7f1a20",
        )


@pytest.mark.asyncio
async def test_programmatic_opt_in_without_coin_path_fails_before_storage(tmp_path, monkeypatch):
    from promo_bot.affiliate.aliexpress_shadow_listener import ShadowRunLimits
    from promo_bot.cli import run_aliexpress_shadow_auto_delivery
    from promo_bot.config.loader import load_app_config

    async def forbidden(*_a, **_kw):
        pytest.fail("invalid options opened storage")

    monkeypatch.setattr("promo_bot.cli._open_durable_shadow_database", forbidden)
    with pytest.raises(ValueError, match="ALIEXPRESS_COIN_MULTI_REQUIRES_COIN_PATH"):
        await run_aliexpress_shadow_auto_delivery(
            _settings(coin_gate=True),
            load_app_config(_config_file(tmp_path)),
            tmp_path / "no.sqlite3",
            ShadowRunLimits(1, 1, 1, 1),
            "private-test",
            3,
            allow_multiple_coin_shorts=True,
        )
