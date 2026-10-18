"""The real limited entrypoint, with only external boundaries replaced."""

import json
import sqlite3

import httpcore
import httpx
import pytest

from promo_bot.cli import main
from tests.unit.test_coin_listener_pilot import _argv, _config_file
from tests.unit.test_coin_shadow_multi_contract import A, B
from tests.unit.test_coin_shadow_multi_entrypoint import (
    CANONICAL,
    PROMOTIONS,
    install_runtime,
)

W = "https://seuhardware.com/s/Ab12Cd3"
V = "https://seuhardware.com/s/Ef45Gh6"


def install_wrapper_http(monkeypatch, locations, gets, *, status=302):
    from promo_bot.security.aliexpress_short_links import PinnedAliExpressHttpTransport
    from promo_bot.security.urls import SystemDnsResolver

    async def dns(_self, hostname, port):
        assert (hostname, port) == ("seuhardware.com", 443)
        return frozenset({"8.8.8.8"})

    async def request(_self, req):
        assert req.method == "GET"
        gets.append(str(req.url))
        destination = locations[str(req.url)]
        if isinstance(destination, Exception):
            raise destination
        stream = type("Peer", (), {"get_extra_info": lambda _self, _key: ("8.8.8.8", 443)})()
        return httpx.Response(
            status,
            headers={} if destination is None else {"location": destination},
            request=req,
            extensions={"network_stream": stream},
        )

    monkeypatch.setattr(SystemDnsResolver, "resolve", dns)
    monkeypatch.setattr(PinnedAliExpressHttpTransport, "handle_async_request", request)


def arguments(tmp_path, count=1, calls=3, *, enabled=True):
    argv = _argv(_config_file(tmp_path), tmp_path / "pilot.sqlite3")
    argv += ["--allow-multiple-coin-shorts"]
    if enabled:
        argv += ["--allow-seuhardware-wrappers"]
    for flag, value in {
        "--max-messages": count,
        "--max-links-per-message": 3,
        "--max-api-calls": calls,
        "--max-send-messages": count + 1,
        "--run-seconds": 5,
    }.items():
        argv[argv.index(flag) + 1] = str(value)
    return argv


def test_wrapper_entrypoint_generates_from_resolved_short_and_sends_original_spans(
    tmp_path, monkeypatch, capsys, caplog
):
    gets, tops, sends = [], [], []
    content = f"🔥 APP {W}\r\nPreço R$ 10 — cupom TESTE"
    install_runtime(monkeypatch, [content], tops, sends)
    install_wrapper_http(monkeypatch, {W: A}, gets)
    assert main(arguments(tmp_path)) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["deliveries_sent"] == 1
    assert report["api_calls"] == 1 and tops == [A]
    assert gets == [W]
    assert sends == [f"🔥 APP {PROMOTIONS[A]}\r\nPreço R$ 10 — cupom TESTE"]
    assert report["wrapper_http_requests"] == 1
    assert not any(s in captured.out + captured.err + caplog.text for s in (W, A, "Ab12Cd3"))
    with sqlite3.connect(tmp_path / "pilot.sqlite3") as conn:
        assert conn.execute("SELECT original_text FROM source_messages").fetchone() == (content,)
        assert conn.execute("SELECT COUNT(*) FROM deals").fetchone() == (0,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        uses = conn.execute(
            "SELECT kind, origin FROM affiliate_link_uses ORDER BY created_at"
        ).fetchall()
        assert len(uses) == 2
        preview, send = (json.loads(row[1]) for row in uses)
        assert preview["wrapper_resolution_version"] == 1
        assert preview["wrapper_resolutions"] == send["wrapper_resolutions"]
        observation = preview["wrapper_resolutions"][0]
        assert observation["ordinal"] == 0 and observation["http_status"] == 302
        assert observation["span_start"] == 6 and observation["span_end"] == 6 + len(W)
        assert len(observation["original_input_fingerprint"]) == 64
        assert len(observation["resolved_input_fingerprint"]) == 64
        assert observation["generation_id"] == preview["occurrences"][0]["generation_id"]


@pytest.mark.parametrize("destination", [A, B])
def test_wrappers_deduplicate_by_resolved_literal_and_reresolve_each_new_message(
    tmp_path, monkeypatch, capsys, destination
):
    gets, tops, sends = [], [], []
    contents = [f"{W}\n{V}\n{W}", f"{V}\n{destination}"]
    install_runtime(monkeypatch, contents, tops, sends)
    install_wrapper_http(monkeypatch, {W: destination, V: destination}, gets)
    assert main(arguments(tmp_path, count=2)) == 0
    report = json.loads(capsys.readouterr().out)
    assert tops == [destination] and gets == [W, V, V]
    assert sends == [
        "\n".join([PROMOTIONS[destination]] * 3),
        "\n".join([PROMOTIONS[destination]] * 2),
    ]
    assert report["wrapper_http_requests"] == 3
    assert report["cache_hits"] == 1 and report["deliveries_sent"] == 2
    assert report["coin_multi"]["distinct_inputs_admitted"] == 2
    assert report["coin_multi"]["in_message_reuses"] == 3
    with sqlite3.connect(tmp_path / "pilot.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM affiliate_link_generations").fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM aliexpress_coin_shadow_deliveries"
        ).fetchone() == (2,)
    assert main(arguments(tmp_path, count=2)) == 0
    assert len(tops) == 1 and len(gets) == 3 and len(sends) == 2
    capsys.readouterr()


@pytest.mark.parametrize(
    "content,enabled",
    [
        (W, False),
        (f"{W}\n{CANONICAL}", True),
        (f"{W}\nhttps://example.com/", True),
        (W + "?q=1", True),
        (W + "?", True),
        (W + "/", True),
        ("\n".join([W] * 4), True),
    ],
)
def test_ineligible_surface_and_disabled_opt_in_never_resolve_or_call_top(
    tmp_path, monkeypatch, capsys, content, enabled
):
    gets, tops, sends = [], [], []
    install_runtime(monkeypatch, [content], tops, sends)
    install_wrapper_http(monkeypatch, {W: A}, gets)
    assert main(arguments(tmp_path, enabled=enabled)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["rejected"] == 1
    assert gets == tops == sends == []


def test_resolve_every_input_before_top_and_fail_without_partial_delivery(
    tmp_path, monkeypatch, capsys
):
    gets, tops, sends = [], [], []
    install_runtime(monkeypatch, [f"{W}\n{V}"], tops, sends)
    install_wrapper_http(monkeypatch, {W: A, V: "https://example.com/"}, gets)
    assert main(arguments(tmp_path)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["rejected"] == 1 and report["api_calls"] == 0
    assert gets == [W, V] and tops == sends == []


def test_resolved_misses_exceeding_top_budget_do_not_generate_anything(
    tmp_path, monkeypatch, capsys
):
    gets, tops, sends = [], [], []
    install_runtime(monkeypatch, [f"{W}\n{B}"], tops, sends)
    install_wrapper_http(monkeypatch, {W: A}, gets)
    assert main(arguments(tmp_path, calls=1)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["rejection_codes"] == ["ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT"]
    assert gets == [W] and tops == sends == []


def test_three_distinct_wrappers_are_bounded_and_all_resolve_before_top(
    tmp_path, monkeypatch, capsys
):
    gets, tops, sends = [], [], []
    third = "https://seuhardware.com/s/Ij78Kl9"
    short = "https://a.aliexpress.com/_Mn01Pq2"
    monkeypatch.setitem(PROMOTIONS, short, "https://s.click.aliexpress.com/e/_Rs34Tu5")
    install_runtime(monkeypatch, [f"{W}\n{V}\n{third}"], tops, sends)
    install_wrapper_http(monkeypatch, {W: A, V: B, third: short}, gets)
    # Observe ordering at the actual wire boundary, not by running another converter.
    from promo_bot.providers.aliexpress.transport import AliExpressHttpTransport

    before = AliExpressHttpTransport.execute

    async def sending(self, request):
        assert gets == [W, V, third]
        with sqlite3.connect(tmp_path / "pilot.sqlite3") as conn:
            pending = conn.execute(
                "SELECT origin FROM affiliate_link_generations "
                "WHERE state IN ('PREPARED','CALL_STARTED')"
            ).fetchall()
            assert len(pending) == 1
            # A separate connection proves the association is committed before
            # the wire request. The existing offline client is conservatively
            # marked CALL_STARTED before entering this signed-wire simulator.
            origin = json.loads(pending[0][0])
            assert origin["wrapper_resolution_version"] == 1
            assert len(origin["wrapper_resolutions"]) == 1
        return await before(self, request)

    monkeypatch.setattr(AliExpressHttpTransport, "execute", sending)
    assert main(arguments(tmp_path)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["wrapper_http_requests"] == 3 and report["api_calls"] == 3
    assert tops == [A, B, short] and sends == ["\n".join(PROMOTIONS[v] for v in tops)]


@pytest.mark.parametrize("target", [W, A, CANONICAL])
def test_single_url_remains_supported_without_multi_opt_in(tmp_path, monkeypatch, capsys, target):
    gets, tops, sends = [], [], []
    install_runtime(monkeypatch, [target], tops, sends)
    install_wrapper_http(monkeypatch, {W: A}, gets)
    argv = _argv(_config_file(tmp_path), tmp_path / "pilot.sqlite3")
    assert main([*argv, "--allow-seuhardware-wrappers"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["deliveries_sent"] == report["api_calls"] == 1
    assert gets == ([W] if target == W else [])


@pytest.mark.parametrize(
    "response,status,code",
    [
        (None, 302, "SEUHARDWARE_LOCATION_MISSING"),
        (A, 301, "SEUHARDWARE_STATUS_UNSUPPORTED"),
        (A, 503, "SEUHARDWARE_STATUS_UNSUPPORTED"),
        (httpx.ReadTimeout("sensitive"), 302, "SEUHARDWARE_TIMEOUT"),
        (httpcore.RemoteProtocolError("synthetic header"), 302, "SEUHARDWARE_RESOLUTION_FAILED"),
    ],
)
def test_wrapper_failure_in_real_entrypoint_never_reaches_top_or_delivery(
    tmp_path, monkeypatch, capsys, response, status, code
):
    gets, tops, sends = [], [], []
    install_runtime(monkeypatch, [W], tops, sends)
    install_wrapper_http(monkeypatch, {W: response}, gets, status=status)
    assert main(arguments(tmp_path)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["rejection_codes"] == [code]
    assert len(gets) == 1 and tops == sends == []


@pytest.mark.parametrize("coin_gate,include", [(False, True), (True, False)])
def test_invalid_wrapper_gate_or_wiring_fails_before_runtime(
    tmp_path, monkeypatch, capsys, coin_gate, include
):
    from tests.unit.test_coin_listener_pilot import _settings

    monkeypatch.setattr("promo_bot.cli.load_settings", lambda: _settings(coin_gate=coin_gate))
    monkeypatch.setattr(
        "promo_bot.cli.run_aliexpress_shadow_auto_delivery",
        lambda *_a, **_kw: pytest.fail("runtime must not be constructed"),
    )
    argv = _argv(_config_file(tmp_path), tmp_path / "pilot.sqlite3")
    if not include:
        argv.remove("--include-coin-shorts")
    assert main([*argv, "--allow-seuhardware-wrappers"]) == 2
    assert not (tmp_path / "pilot.sqlite3").exists()
    capsys.readouterr()
