import json
import logging

import pytest

from promo_bot.observability import configure_logging, redact_text, sanitize_url


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
