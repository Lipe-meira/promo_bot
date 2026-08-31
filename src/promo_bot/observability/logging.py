"""JSON logs that avoid secrets and credential-bearing URLs."""

from __future__ import annotations

import ipaddress
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
    "channel_id",
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
URL_PATTERN = re.compile(r"https?://[^\s<>]*", re.IGNORECASE)
DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"\b[0-9]{5,15}:[a-z0-9_-]{20,}\b", re.IGNORECASE)
TELEGRAM_BOT_PATH_PATTERN = re.compile(r"^/bot[^/]+", re.IGNORECASE)


def is_sensitive_key(value: str) -> bool:
    normalized = value.casefold().replace("-", "_")
    return any(
        normalized == key or normalized.endswith(f"_{key}") or normalized.startswith(f"{key}_")
        for key in SENSITIVE_KEYS
    )


def sanitize_url(value: str) -> str:
    """Remove user info, fragments, and sensitive query values from a URL."""

    try:
        parts = urlsplit(_safe_text(value))
        scheme = parts.scheme.casefold()
        hostname = parts.hostname
        port = parts.port
        if scheme not in {"http", "https"} or not hostname:
            return "[INVALID_URL]"
        safe_hostname = _sanitize_hostname(hostname)
        if safe_hostname is None:
            return "[INVALID_URL]"
        host = safe_hostname if port is None else f"{safe_hostname}:{port}"
        safe_query = [
            (key, "[REDACTED]" if is_sensitive_key(key) else item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
        ]
        path = parts.path
        if safe_hostname == "api.telegram.org":
            path = TELEGRAM_BOT_PATH_PATTERN.sub("/bot[REDACTED]", path)
        return urlunsplit((scheme, host, path, urlencode(safe_query), ""))
    except Exception:
        # Logging must remain available while handling hostile or malformed input.
        return "[INVALID_URL]"


def redact_text(value: str) -> str:
    """Redact common credential assignments and sanitize embedded HTTP URLs."""

    try:
        redacted = ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", _safe_text(value)
        )
        redacted = TELEGRAM_BOT_TOKEN_PATTERN.sub("[REDACTED]", redacted)
        return URL_PATTERN.sub(lambda match: sanitize_url(match.group(0)), redacted)
    except Exception:
        # Fail closed: do not return the original value when redaction itself fails.
        return "[REDACTION_FAILED]"


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = "[UNFORMATTABLE_LOG_MESSAGE]"
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": _safe_text(record.levelname),
            "logger": _safe_text(record.name),
            "message": redact_text(message),
        }
        for field_name in LOG_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = redact_text(_safe_text(value))
        if record.exc_info:
            try:
                exception_text = self.formatException(record.exc_info)
            except Exception:
                exception_text = "[UNFORMATTABLE_EXCEPTION]"
            payload["error_summary"] = redact_text(exception_text)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _safe_text(value: object) -> str:
    try:
        text = value if isinstance(value, str) else str(value)
        return text.encode("utf-8", errors="replace").decode("utf-8")
    except Exception:
        return "[UNFORMATTABLE_VALUE]"


def _sanitize_hostname(hostname: str) -> str | None:
    candidate = hostname.rstrip(".")
    if not candidate or "%" in candidate:
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        if ":" in candidate:
            return None
        try:
            ascii_hostname = candidate.encode("idna").decode("ascii").casefold()
        except UnicodeError:
            return None
        labels = ascii_hostname.split(".")
        if len(ascii_hostname) > 253 or any(
            not DNS_LABEL_PATTERN.fullmatch(label) for label in labels
        ):
            return None
        return ascii_hostname
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    return address.compressed


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("promo_bot").disabled = False
