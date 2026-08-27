import json
import logging

from promo_bot.observability import configure_logging, redact_text, sanitize_url


def test_sensitive_query_values_are_redacted() -> None:
    value = sanitize_url("https://example.com/product?id=1&token=top-secret#fragment")

    assert "top-secret" not in value
    assert "fragment" not in value
    assert "id=1" in value


def test_prefixed_sensitive_query_values_are_redacted() -> None:
    value = sanitize_url("https://example.com/product?telegram_bot_token=top-secret")

    assert "top-secret" not in value


def test_assignment_secrets_are_redacted() -> None:
    assert "abc123" not in redact_text("TELEGRAM_BOT_TOKEN=abc123")


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
