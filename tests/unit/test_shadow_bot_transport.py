import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from promo_bot.telegram.shadow_bot import ShadowBotRequest as HTTPXRequest


@pytest.mark.asyncio
async def test_bot_wire_uses_only_get_chat_and_literal_single_send(monkeypatch, caplog):
    from promo_bot.telegram.shadow_bot import ShadowBotTransport

    seen = []
    text = "**Oferta** <b>literal</b> 😀\nhttps://s.click.aliexpress.com/e/fixture-secret"

    def handler(request):
        seen.append((request.url.path.rsplit("/", 1)[-1], parse_qs(request.content.decode())))
        if request.url.path.endswith("/getChat"):
            result = {
                "id": -10012345,
                "type": "channel",
                "title": "fixture",
                "accent_color_id": 1,
                "max_reaction_count": 0,
            }
        else:
            result = {
                "message_id": 42,
                "date": 1700000000,
                "text": text,
                "chat": {"id": -10012345, "type": "channel", "title": "fixture"},
            }
        return httpx.Response(200, json={"ok": True, "result": result})

    request = HTTPXRequest(
        httpx_kwargs={
            "transport": httpx.MockTransport(handler),
            "trust_env": False,
            "follow_redirects": False,
        }
    )
    monkeypatch.setattr("promo_bot.telegram.shadow_bot.build_request", lambda: request)
    caplog.set_level(logging.DEBUG)
    async with ShadowBotTransport("12345:fixture-token") as transport:
        chat = await transport.get_chat("-10012345")
        assert chat.type == "channel" and not chat.username
        assert await transport.send_text("-10012345", text) == "42"
    assert [name for name, _ in seen] == ["getChat", "sendMessage"]
    payload = seen[1][1]
    assert payload["text"] == [text]
    assert "parse_mode" not in payload
    assert "entities" not in payload and "reply_markup" not in payload
    assert json.loads(payload["link_preview_options"][0]) == {"is_disabled": True}
    for secret in (text, "fixture-token", "-10012345", "fixture-secret"):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "status,result",
    [
        (400, {"ok": False, "description": "sensitive upstream detail"}),
        (429, {"ok": False, "parameters": {"retry_after": 1}}),
        (502, {"ok": False, "description": "sensitive upstream detail"}),
    ],
)
@pytest.mark.asyncio
async def test_transport_never_retries_or_leaks_raw_errors(monkeypatch, status, result):
    from promo_bot.telegram.shadow_bot import ShadowBotTransport

    monkeypatch.setenv("PTB_TIMEDELTA", "true")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=result)

    request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(handler)})
    monkeypatch.setattr("promo_bot.telegram.shadow_bot.build_request", lambda: request)
    async with ShadowBotTransport("12345:fixture-token") as transport:
        with pytest.raises(RuntimeError) as error:
            await transport.send_text("-10012345", "fixture")
    assert len(calls) == 1
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
@pytest.mark.parametrize("body", [b"<html>private gateway error</html>", b"{}"])
@pytest.mark.asyncio
async def test_unproven_4xx_is_ambiguous_not_definitive(monkeypatch, status, body):
    from promo_bot.affiliate.shadow_delivery import DefinitiveSendRejection
    from promo_bot.telegram.shadow_bot import ShadowBotTransport

    request = HTTPXRequest(
        httpx_kwargs={
            "transport": httpx.MockTransport(lambda request: httpx.Response(status, content=body))
        }
    )
    monkeypatch.setattr("promo_bot.telegram.shadow_bot.build_request", lambda: request)
    async with ShadowBotTransport("12345:fixture-token") as transport:
        with pytest.raises(RuntimeError) as error:
            await transport.send_text("-10012345", "fixture")
    assert not isinstance(error.value, DefinitiveSendRejection)
    assert str(error.value) == "SHADOW_SEND_UNCERTAIN"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
@pytest.mark.asyncio
async def test_validated_bot_api_rejection_is_definitive(monkeypatch, status):
    from promo_bot.affiliate.shadow_delivery import DefinitiveSendRejection
    from promo_bot.telegram.shadow_bot import ShadowBotTransport

    monkeypatch.setenv("PTB_TIMEDELTA", "true")
    body = {"ok": False, "error_code": status, "description": "private server description"}
    if status == 429:
        body["parameters"] = {"retry_after": 1}
    request = HTTPXRequest(
        httpx_kwargs={
            "transport": httpx.MockTransport(lambda request: httpx.Response(status, json=body))
        }
    )
    monkeypatch.setattr("promo_bot.telegram.shadow_bot.build_request", lambda: request)
    async with ShadowBotTransport("12345:fixture-token") as transport:
        with pytest.raises(DefinitiveSendRejection, match=r"^SHADOW_SEND_REJECTED$"):
            await transport.send_text("-10012345", "fixture")
