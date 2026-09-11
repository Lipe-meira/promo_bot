import asyncio
import json
import logging

import pytest
from test_shadow_delivery import LINK, SOURCE, TARGET, TEXT, FakeTransport, seed, settings

from promo_bot.cli import main


@pytest.fixture
def setup_cli(tmp_path, monkeypatch):
    import promo_bot.cli as cli

    path = tmp_path / "shadow.sqlite3"
    preview_id = asyncio.run(seed(path))
    configuration = tmp_path / "fixture.yaml"
    configuration.write_text(f'''templates: [fixture]
affiliate_disclosure: fixture
source_channels: ["{SOURCE}"]
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "{TARGET}"
      kind: private_channel
''')
    monkeypatch.setattr(cli, "load_settings", settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("Forbidden runtime boundary")

    for name in (
        "build_telegram_user_client",
        "TelegramMonitor",
        "AliExpressAffiliateApiClient",
        "Database",
        "DurableRelayQueue",
        "RelayProcessor",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    return path, preview_id, configuration


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_real_cli_entrypoint_sends_once_and_reports_no_payload(
    setup_cli, monkeypatch, capsys, caplog, cleanup_failure
):
    import promo_bot.affiliate.shadow_cli as shadow_cli
    from promo_bot.database.session import Database

    path, preview_id, configuration = setup_cli
    fake = FakeTransport()

    class TransportContext:
        def __init__(self, token):
            assert token == "123:fixture-token"

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *args):
            if cleanup_failure:
                raise RuntimeError("private cleanup details")

    monkeypatch.setattr(shadow_cli, "ShadowBotTransport", TransportContext)
    caplog.set_level(logging.DEBUG)
    args = [
        "affiliate",
        "shadow-deliver",
        "--preview-id",
        str(preview_id),
        "--destination",
        "private-test",
        "--confirm-send-one-test-message",
        "--config",
        str(configuration),
        "--shadow-database",
        str(path),
    ]
    # Advance fixture validity to wall-clock time without consulting any runtime database.
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from promo_bot.database.models import AffiliateLinkProofModel, AffiliateShadowPreviewModel

    async def refresh_fixture():
        database = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
        async with database.session() as session:
            await session.execute(
                update(AffiliateShadowPreviewModel).values(
                    content_expires_at=datetime.now(UTC) + timedelta(hours=1)
                )
            )
            await session.execute(
                update(AffiliateLinkProofModel).values(
                    expires_at=datetime.now(UTC) + timedelta(hours=1)
                )
            )
        await database.dispose()

    asyncio.run(refresh_fixture())
    caplog.clear()
    import sys

    from promo_bot.cli import entrypoint

    monkeypatch.setattr(sys, "argv", ["promo-bot", *args])
    with pytest.raises(SystemExit) as exited:
        entrypoint()
    assert exited.value.code == (2 if cleanup_failure else 0)
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["status"] == "sent"
    assert report["external_side_effect"] is True
    assert report["send_message_attempts"] == report["get_chat_attempts"] == 1
    assert report["production_publication"] is False
    if cleanup_failure:
        assert report["error_code"] == "SHADOW_CLEANUP_FAILED"
    assert fake.sends == [(TARGET, TEXT)]
    for sensitive in (TARGET, LINK, TEXT, "fixture-token"):
        assert sensitive not in output.out + output.err + caplog.text
    assert main(args) != 0
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["send_message_attempts"] == 0
    assert len(fake.sends) == 1


def test_cli_missing_confirmation_fails_closed(setup_cli, capsys):
    path, preview_id, configuration = setup_cli
    assert (
        main(
            [
                "affiliate",
                "shadow-deliver",
                "--preview-id",
                str(preview_id),
                "--destination",
                "private-test",
                "--config",
                str(configuration),
                "--shadow-database",
                str(path),
            ]
        )
        == 2
    )
    assert "SHADOW_SEND_CONFIRMATION_REQUIRED" in capsys.readouterr().out
