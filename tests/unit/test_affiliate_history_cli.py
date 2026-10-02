from __future__ import annotations

import json

import pytest

from promo_bot import cli
from tests.unit.test_coin_shadow_delivery import make_stack


async def history_database(tmp_path):
    database, _client, _service, _preview, _transport, _delivery = await make_stack(tmp_path)
    path = tmp_path / "coin-delivery.sqlite3"
    await database.dispose()
    return path


def forbid_settings():
    raise AssertionError("history query must never load .env")


@pytest.mark.asyncio
async def test_history_cli_is_readonly_sanitized_and_never_loads_env(tmp_path, monkeypatch, capsys):
    path = await history_database(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(cli, "load_settings", forbid_settings)
    # main is synchronous; read-only history query must not require an async event loop.
    result = cli.main(
        ["affiliate", "link-history", "list", "--database", str(path), "--scope", "shadow"]
    )
    captured = capsys.readouterr()
    assert result == 0
    report = json.loads(captured.out)
    assert len(report["generations"]) == 1
    assert report["generations"][0]["state"] == "CONFIRMED"
    assert "https://" not in captured.out + captured.err
    assert captured.err == ""
    assert path.read_bytes() == before
    generation_id = report["generations"][0]["id"]
    assert (
        cli.main(
            [
                "affiliate",
                "link-history",
                "show",
                "--database",
                str(path),
                "--scope",
                "shadow",
                "--generation-id",
                generation_id,
                "--include-urls",
            ]
        )
        == 0
    )
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["generation"]["generated_url"].startswith("https://s.click.aliexpress.com/")
    assert path.read_bytes() == before


async def test_history_cli_generation_and_send_filters_are_independent(
    tmp_path, monkeypatch, capsys
):
    path = await history_database(tmp_path)
    monkeypatch.setattr(cli, "load_settings", forbid_settings)
    args = ["affiliate", "link-history", "list", "--database", str(path), "--scope", "shadow"]
    assert (
        cli.main([*args, "--generation-result", "CONFIRMED", "--send-result", "SEND_CONFIRMED"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["generations"] == []
    assert cli.main([*args, "--generation-result", "CONFIRMED"]) == 0
    assert len(json.loads(capsys.readouterr().out)["generations"]) == 1


async def test_history_show_retains_delivery_destination_and_confirmation_after_preview_purge(
    tmp_path,
):
    from sqlalchemy import delete, select

    from promo_bot.affiliate.history_query import read_history
    from promo_bot.database.history_models import AffiliateLinkGenerationModel
    from promo_bot.database.models import AliExpressCoinShadowEvidenceModel

    database, _client, _service, preview, _transport, delivery = await make_stack(tmp_path)
    try:
        assert (await delivery.deliver(preview.preview_id, "private-test")).status == "sent"
        async with database.session() as session:
            generation_id = (await session.scalar(select(AffiliateLinkGenerationModel))).id
            await session.execute(delete(AliExpressCoinShadowEvidenceModel))
        report = read_history(
            tmp_path / "coin-delivery.sqlite3", scope="shadow", generation_id=generation_id
        )
        send = next(use for use in report["generation"]["uses"] if use["kind"] == "SEND")
        assert len(send["destination_key"]) == 64
        assert send["telegram_message_id"] == "77" and send["state"] == "SEND_CONFIRMED"
        assert "https://" not in json.dumps(report)
    finally:
        await database.dispose()


def test_real_convert_requires_explicit_durable_database_before_transport(monkeypatch, capsys):
    from tests.unit.test_coin_shadow_delivery import config, settings

    monkeypatch.setattr(cli, "load_settings", settings)
    monkeypatch.setattr(cli, "load_app_config", lambda path: config())
    monkeypatch.setattr(
        cli, "build_offline_safe_http_client", lambda: pytest.fail("transport built")
    )
    assert cli.main(["aliexpress", "convert-preview", "--message-id", "1"]) == 2
    captured = capsys.readouterr()
    assert "AFFILIATE_HISTORY_STORAGE_REQUIRED" in captured.err


def test_legacy_cli_snapshot_is_explicit_and_query_never_changes_database(
    tmp_path, monkeypatch, capsys
):
    import asyncio

    from promo_bot.affiliate.history_query import read_history
    from tests.unit.test_affiliate_history_legacy import LEGACY_URL, legacy_database

    database = asyncio.run(legacy_database(tmp_path))
    asyncio.run(database.dispose())
    path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(cli, "load_settings", forbid_settings)

    # Only the offline storage validator is injected. The actual CLI, lock,
    # repository, transactions and read-only SQLite URI are exercised.
    async def offline_validator(database, *, real):
        from promo_bot.affiliate.history_context import validate_history_storage

        await validate_history_storage(database, real=False)

    monkeypatch.setattr(
        "promo_bot.affiliate.history_cli.validate_history_storage", offline_validator
    )
    monkeypatch.setattr("promo_bot.affiliate.history_cli.durable_sqlite_path", lambda url: path)
    base = ["affiliate", "link-history"]
    common = ["--database", str(path), "--scope", "shadow"]
    before = path.read_bytes()
    assert cli.main([*base, "legacy-blocks", *common]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["legacy_blocks"][0]["eligible"] is True
    assert path.read_bytes() == before
    request = [
        *base,
        "request-legacy-generation",
        *common,
        "--legacy-kind",
        "coin-evidence",
        "--legacy-id",
        "1",
    ]
    assert cli.main(request) == 2
    assert "OPERATOR_CONFIRMATION_REQUIRED" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert cli.main([*request, "--confirm-new-generation"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["api_calls"] == 0 and first["state"] == "REQUESTED"
    assert cli.main([*request, "--confirm-new-generation"]) == 0
    assert json.loads(capsys.readouterr().out)["generation_request"] == first["generation_request"]
    before = path.read_bytes()
    hidden = read_history(
        path, scope="shadow", generation_id=first["generation_request"], include_legacy=True
    )
    assert LEGACY_URL not in json.dumps(hidden)
    explicit = read_history(
        path,
        scope="shadow",
        generation_id=first["generation_request"],
        include_legacy=True,
        include_urls=True,
    )
    assert explicit["generation"]["generated_at"] is None
    assert explicit["generation"]["tracking_confirmed"] is False
    assert explicit["generation"]["legacy_snapshot"]["record"]["promotion_link"] == LEGACY_URL
    assert path.read_bytes() == before
