import asyncio
import io
import json
import logging
import sys
from pathlib import Path

import pytest

from promo_bot.database.migrations import upgrade_database_async
from promo_bot.database.shadow import shadow_database_url
from promo_bot.observability import configure_logging, redact_text, sanitize_url
from promo_bot.observability import logging as safe_logging

REDIRECT_EVENT = {
    "source_host": "a.aliexpress.com",
    "destination_host": "[INVALID_HOST]",
    "redirect_index": 1,
    "destination_scheme": "https",
    "status_code": 302,
    "decision_code": "ALIEXPRESS_REDIRECT_HOST_FORBIDDEN",
}


def _stderr_json(captured: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in captured.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payloads.append(value)
    return payloads


def test_redirect_handler_reinstalls_after_repeated_real_migrations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("CRITICAL")
    installer = getattr(safe_logging, "install_redirect_rejection_handler", None)
    assert installer is not None
    database_url = shadow_database_url(tmp_path / "repeated-migrations.sqlite3")

    asyncio.run(upgrade_database_async(database_url))
    installer()
    asyncio.run(upgrade_database_async(database_url))
    installer()
    logging.getLogger("promo_bot.aliexpress_redirect_rejection").warning(
        "AliExpress redirect rejected",
        extra={"_aliexpress_redirect_rejection_event": True, **REDIRECT_EVENT},
    )

    assert _stderr_json(capsys.readouterr().err) == [REDIRECT_EVENT]


def test_redirect_handler_is_idempotent_and_preserves_external_handlers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("CRITICAL")
    installer = getattr(safe_logging, "install_redirect_rejection_handler", None)
    handler_type = getattr(safe_logging, "RedirectRejectionHandler", None)
    assert installer is not None
    assert handler_type is not None
    logger = logging.getLogger("promo_bot.aliexpress_redirect_rejection")

    class ExternalHandler(logging.StreamHandler):
        was_closed = False

        def close(self) -> None:
            self.was_closed = True
            super().close()

    external = ExternalHandler(io.StringIO())
    external.setLevel(logging.CRITICAL)
    logger.addHandler(external)
    try:
        installer()
        installer()

        owned = [handler for handler in logger.handlers if isinstance(handler, handler_type)]
        assert len(owned) == 1
        assert external in logger.handlers
        assert external.was_closed is False
        assert owned[0].stream is sys.stderr
        assert logger.disabled is False
        assert logger.level == logging.WARNING
        assert logger.propagate is False
        logger.warning(
            "AliExpress redirect rejected",
            extra={"_aliexpress_redirect_rejection_event": True, **REDIRECT_EVENT},
        )
        assert _stderr_json(capsys.readouterr().err) == [REDIRECT_EVENT]
    finally:
        logger.removeHandler(external)
        external.close()


def test_sensitive_query_values_are_redacted() -> None:
    value = sanitize_url("https://example.com/product?id=1&token=top-secret#fragment")

    assert "top-secret" not in value
    assert "fragment" not in value
    assert "id=1" in value


def test_prefixed_sensitive_query_values_are_redacted() -> None:
    value = sanitize_url("https://example.com/product?telegram_bot_token=top-secret")

    assert "top-secret" not in value


def test_aliexpress_query_and_tracking_values_are_redacted() -> None:
    sensitive_values = {
        "app_key": "fixture-sensitive-app-key",
        "sign": "ABCDEF0123456789",
        "tracking_id": "fixture-sensitive-tracking",
        "session": "fixture-sensitive-session",
    }
    query = "&".join(f"{name}={value}" for name, value in sensitive_values.items())

    sanitized = sanitize_url(f"https://api-sg.aliexpress.com/sync?{query}")
    redacted = redact_text(" ".join(f"{name}={value}" for name, value in sensitive_values.items()))

    for value in sensitive_values.values():
        assert value not in sanitized
        assert value not in redacted


def test_assignment_secrets_are_redacted() -> None:
    assert "abc123" not in redact_text("TELEGRAM_BOT_TOKEN=abc123")


def test_telegram_bot_token_is_redacted_from_http_client_url() -> None:
    token = "1234567890:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefgh"
    message = f"HTTP Request: POST https://api.telegram.org/bot{token}/sendMessage HTTP/1.1 200 OK"

    redacted = redact_text(message)

    assert token not in redacted
    assert "https://api.telegram.org/bot[REDACTED]/sendMessage" in redacted


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com:invalid/path",
        "https://example.com:70000/path",
        "https://-invalid.example/path",
        "https://[2001:db8::1/path",
        "https://",
    ],
)
def test_malformed_urls_are_sanitized_without_raising(value: str) -> None:
    assert sanitize_url(value) == "[INVALID_URL]"


def test_text_with_multiple_malformed_urls_is_redacted_without_raising() -> None:
    value = "bad https://example.com:invalid/path then https://[broken/path and incomplete https://"

    redacted = redact_text(value)

    assert redacted.count("[INVALID_URL]") == 3
    assert "https://" not in redacted


def test_structured_log_contains_context_without_secret(capsys: object) -> None:
    configure_logging("INFO")
    logging.getLogger("test").info(
        "request token=abc123",
        extra={"store": "kabum", "stage": "fixture", "result": "ok"},
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.err)
    assert payload["store"] == "kabum"
    assert payload["stage"] == "fixture"
    assert "abc123" not in captured.err


def test_structured_logging_survives_malformed_urls(capsys: object) -> None:
    configure_logging("INFO")

    logging.getLogger("test").error(
        "failed urls: https://example.com:invalid/path https://[broken/path https://",
        extra={"error_summary": "redirect https://example.com:70000/path"},
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.err)
    assert payload["message"].count("[INVALID_URL]") == 3
    assert payload["error_summary"] == "redirect [INVALID_URL]"
