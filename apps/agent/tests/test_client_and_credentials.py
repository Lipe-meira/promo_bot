"""What leaves the PC and what stays in it: the token is sent only to the paired API, never in
clear over a network, and is never stored readable."""

from __future__ import annotations

from pathlib import Path

import pytest

from promo_agent.client import ApiClient, BadPairingCode, NotPaired, Unreachable, api_origin
from promo_agent.credentials import Pairing, forget, load, save
from tests.conftest import TOKEN, WORKSPACE_ID, FakeApi


@pytest.mark.parametrize(
    "url",
    [
        "https://promo.example.test",
        "https://promo.example.test/",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
)
def test_https_or_this_pc_only(url: str) -> None:
    assert api_origin(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://promo.example.test",
        "ftp://promo.example.test",
        "https://user:secret@promo.example.test",
        "https://promo.example.test/v1",
        "https://promo.example.test?x=1",
        "promo.example.test",
        "",
    ],
)
def test_other_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        api_origin(url)


def test_pairing_returns_the_token(api: FakeApi) -> None:
    token, workspace_id = ApiClient(api.url).pair("ABCD-EFGH-JKMN")

    assert (token, workspace_id) == (TOKEN, WORKSPACE_ID)
    assert api.pairings == [{"code": "ABCD-EFGH-JKMN"}]


def test_a_wrong_code_is_said_to_be_wrong(api: FakeApi) -> None:
    with pytest.raises(BadPairingCode):
        ApiClient(api.url).pair("ZZZZ-ZZZZ-ZZZZ")


def test_the_report_carries_only_the_process_state(api: FakeApi) -> None:
    api.command = {"id": 4, "action": "start"}

    reply = ApiClient(api.url, TOKEN).sync("stopped")

    assert reply.command is not None and (reply.command.id, reply.command.action) == (4, "start")
    assert api.reports == [
        {
            "process_state": "stopped",
            "completed_command_id": None,
            "last_event": None,
            "closing": False,
        }
    ]


def test_anything_but_start_and_stop_is_ignored(api: FakeApi) -> None:
    api.command = {"id": 4, "action": "run", "program": "calc.exe"}

    assert ApiClient(api.url, TOKEN).sync("stopped").command is None


def test_a_revoked_token_is_not_the_same_as_no_network(api: FakeApi) -> None:
    with pytest.raises(NotPaired):
        ApiClient(api.url, "pbagent_old").sync("stopped")
    api.status = 503
    with pytest.raises(Unreachable):
        ApiClient(api.url, TOKEN).sync("stopped")
    with pytest.raises(Unreachable):
        ApiClient("http://127.0.0.1:1", TOKEN, timeout=1).sync("stopped")


def test_a_redirect_is_not_followed_with_the_token(api: FakeApi, other_api: FakeApi) -> None:
    api.redirect_to = other_api.url

    with pytest.raises(Unreachable):
        ApiClient(api.url, TOKEN).sync("stopped")

    assert other_api.authorizations == []


def test_the_token_is_stored_encrypted_for_this_windows_user(tmp_path: Path) -> None:
    pairing = Pairing("https://promo.example.test", WORKSPACE_ID, TOKEN)

    save(tmp_path, pairing)
    written = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())

    assert TOKEN.encode() not in written
    assert load(tmp_path) == pairing
    assert TOKEN not in repr(pairing)
    forget(tmp_path)
    assert load(tmp_path) is None
    forget(tmp_path)
