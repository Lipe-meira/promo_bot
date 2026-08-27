"""JSON logs that avoid secrets and credential-bearing URLs."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {
    "access_token",
    "api_hash",
    "api_key",
    "app_secret",
    "authorization",
    "cookie",
    "credential_secret",
    "password",
    "secret",
    "session",
    "token",
}
LOG_FIELDS = (
    "store",
    "product",
    "message_id",
    "stage",
    "duration_ms",
    "result",
    "error_code",
    "error_summary",
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b([a-z0-9_-]*(?:access[_-]?token|api[_-]?(?:hash|key)|app[_-]?secret|"
    r"authorization|cookie|credential[_-]?secret|password|secret|session|token)[a-z0-9_-]*)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def is_sensitive_key(value: str) -> bool:
    normalized = value.casefold().replace("-", "_")
    return any(
        normalized == key or normalized.endswith(f"_{key}") or normalized.startswith(f"{key}_")
        for key in SENSITIVE_KEYS
    )


def sanitize_url(value: str) -> str:
    """Remove user info, fragments, and sensitive query values from a URL."""

    try:
        parts = urlsplit(value)
    except ValueError:
        return "[INVALID_URL]"
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "[INVALID_URL]"
    host = parts.hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    safe_query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        safe_query.append((key, "[REDACTED]" if is_sensitive_key(key) else item))
    return urlunsplit((parts.scheme, host, parts.path, urlencode(safe_query), ""))


def redact_text(value: str) -> str:
    """Redact common credential assignments and sanitize embedded HTTP URLs."""

    redacted = ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value
    )
    return URL_PATTERN.sub(lambda match: sanitize_url(match.group(0)), redacted)


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for field_name in LOG_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = redact_text(str(value))
        if record.exc_info:
            payload["error_summary"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("promo_bot").disabled = False
