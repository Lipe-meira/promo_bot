"""Owned child process for real CLI admission with only fake external adapters."""

from __future__ import annotations

import contextlib
import io
import json
import socket
import sys
from pathlib import Path

import pytest

from promo_bot import cli
from promo_bot.config.settings import EnvironmentSettings
from tests.unit.test_coin_listener_pilot import _argv, _config_file, _settings
from tests.unit.test_coin_shadow_multi_entrypoint import install_runtime
from tests.unit.test_history_schema_readonly_preflight import allow_test_storage


def run(action: str, path: Path, opt_in: bool) -> dict:
    EnvironmentSettings.model_config["env_file"] = None
    stdout, stderr = io.StringIO(), io.StringIO()
    requests, sends, constructions, external = [], [], [], []
    getaddrinfo = socket.getaddrinfo
    connect = socket.socket.connect

    def local_dns(host, *args, **kwargs):
        if host not in {None, "127.0.0.1", "::1", "localhost"}:
            external.append(True)
            raise AssertionError("external DNS forbidden")
        return getaddrinfo(host, *args, **kwargs)

    def local_connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            external.append(True)
            raise AssertionError("external socket forbidden")
        return connect(sock, address)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("PROMO_BOT_DATABASE_URL", raising=False)
        monkeypatch.setattr(socket, "getaddrinfo", local_dns)
        monkeypatch.setattr(socket.socket, "connect", local_connect)
        settings = _settings(coin_gate=True).model_copy(
            update={"runtime_dir": path.parent / "runtime"}
        )
        monkeypatch.setattr(cli, "load_settings", lambda: settings)
        if action == "admit":
            real_open = cli._open_durable_shadow_database
            install_runtime(monkeypatch, [], requests, sends)
            monkeypatch.setattr(cli, "load_settings", lambda: settings)
            monkeypatch.setattr(cli, "_open_durable_shadow_database", real_open)
            allow_test_storage(monkeypatch, path)
            for name in ("build_telegram_user_client", "build_offline_safe_http_client"):
                factory = getattr(cli, name)

                def counted(*args, _factory=factory, **kwargs):
                    constructions.append(True)
                    return _factory(*args, **kwargs)

                monkeypatch.setattr(cli, name, counted)
            argv = _argv(_config_file(path.parent), path)
            argv[argv.index("--run-seconds") + 1] = "0.5"
            if opt_in:
                argv.append("--allow-multiple-coin-shorts")
                argv[argv.index("--max-links-per-message") + 1] = "3"
        else:
            assert action == "init"
            argv = ["init-db", "--database-url", f"sqlite+aiosqlite:///{path.as_posix()}"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
    report = json.loads(stdout.getvalue()) if code == 0 else None
    error_code = next(
        (
            value
            for value in (
                "AFFILIATE_HISTORY_WAL_UNVERIFIABLE",
                "AFFILIATE_HISTORY_SCHEMA_REQUIRED",
                "AFFILIATE_HISTORY_STORAGE_UNSTABLE",
                "AFFILIATE_HISTORY_RECOVERY_REQUIRED",
                "AFFILIATE_HISTORY_STORAGE_UNAVAILABLE",
            )
            if value in stderr.getvalue()
        ),
        None,
    )
    assert external == requests == sends == []
    return {
        "exit_code": code,
        "error_code": error_code,
        "transports": len(constructions),
        "external_calls": len(external),
        "top_calls": len(requests),
        "send_calls": len(sends),
        "report": report,
    }


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], Path(sys.argv[2]), sys.argv[3] == "multi")))
