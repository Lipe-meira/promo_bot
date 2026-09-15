"""JSON logs that avoid secrets and credential-bearing URLs."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import sys
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {
    "access_token",
    "api_hash",
    "api_key",
    "app_key",
    "app_secret",
    "authorization",
    "cookie",
    "credential_secret",
    "password",
    "secret",
    "session",
    "sign",
    "token",
    "tracking_id",
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
    r"(?i)\b([a-z0-9_-]*(?:access[_-]?token|api[_-]?(?:hash|key)|app[_-]?(?:key|secret)|"
    r"authorization|cookie|credential[_-]?secret|password|secret|session|sign|token|"
    r"tracking[_-]?id)[a-z0-9_-]*)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
URL_PATTERN = re.compile(r"https?://[^\s<>]*", re.IGNORECASE)
DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"\b[0-9]{5,15}:[a-z0-9_-]{20,}\b", re.IGNORECASE)
TELEGRAM_BOT_PATH_PATTERN = re.compile(r"^/bot[^/]+", re.IGNORECASE)
REDIRECT_DECISION_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,79}")
REDIRECT_SCHEME_PATTERN = re.compile(r"[a-z][a-z0-9+.-]{0,31}")
REDIRECT_HOST_MARKERS = frozenset({"[IP_LITERAL]", "[MISSING_HOST]", "[INVALID_HOST]"})
REDIRECT_EVENT_FIELDS = (
    "source_host",
    "destination_host",
    "redirect_index",
    "destination_scheme",
    "status_code",
    "decision_code",
)
TERMINAL_PATH_CLASSES = frozenset(
    {
        "root",
        "item_shape_mismatch",
        "product_shape_mismatch",
        "numeric_candidate_elsewhere",
        "no_numeric_candidate",
    }
)


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
        if getattr(record, "_aliexpress_redirect_rejection_event", False) is True:
            return _format_redirect_rejection_event(record)
        if getattr(record, "_aliexpress_terminal_rejection_event", False) is True:
            return _format_terminal_rejection_event(record)
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


class RedirectRejectionHandler(logging.StreamHandler[Any]):
    """Project-owned stderr handler for the bounded redirect event."""


class TerminalRejectionHandler(logging.StreamHandler[Any]):
    """Project-owned stderr handler for the bounded terminal event."""


def _format_redirect_rejection_event(record: logging.LogRecord) -> str:
    payload: dict[str, str | int] = {
        "source_host": _safe_redirect_host(getattr(record, "source_host", None)),
        "destination_host": _safe_redirect_host(getattr(record, "destination_host", None)),
        "redirect_index": _safe_bounded_int(
            getattr(record, "redirect_index", None), minimum=1, maximum=1_000
        ),
        "destination_scheme": _safe_redirect_scheme(getattr(record, "destination_scheme", None)),
        "status_code": _safe_bounded_int(
            getattr(record, "status_code", None), minimum=100, maximum=599
        ),
        "decision_code": _safe_redirect_decision_code(getattr(record, "decision_code", None)),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _format_terminal_rejection_event(record: logging.LogRecord) -> str:
    path_class = getattr(record, "path_class", None)
    payload: dict[str, str | int | bool] = {
        "terminal_host": _safe_redirect_host(getattr(record, "terminal_host", None)),
        "status_code": _safe_bounded_int(
            getattr(record, "status_code", None), minimum=100, maximum=599
        ),
        "redirect_index": _safe_bounded_int(
            getattr(record, "redirect_index", None), minimum=0, maximum=1_000
        ),
        "path_class": (
            path_class
            if isinstance(path_class, str) and path_class in TERMINAL_PATH_CLASSES
            else "invalid_path_class"
        ),
        "path_segment_count": _safe_bounded_int(
            getattr(record, "path_segment_count", None), minimum=0, maximum=1_000
        ),
        "has_numeric_path_candidate": (getattr(record, "has_numeric_path_candidate", None) is True),
        "decision_code": _safe_redirect_decision_code(getattr(record, "decision_code", None)),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _safe_redirect_host(value: object) -> str:
    if not isinstance(value, str):
        return "[INVALID_HOST]"
    if value in REDIRECT_HOST_MARKERS:
        return value
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        return "[INVALID_HOST]"
    safe = _sanitize_hostname(value)
    if safe is None:
        return "[INVALID_HOST]"
    try:
        ipaddress.ip_address(value.rstrip("."))
    except ValueError:
        return safe
    return "[IP_LITERAL]"


def _safe_redirect_scheme(value: object) -> str:
    if not isinstance(value, str):
        return "[INVALID_SCHEME]"
    candidate = value.casefold()
    if candidate == "[missing_scheme]":
        return "[MISSING_SCHEME]"
    if candidate == "[invalid_scheme]":
        return "[INVALID_SCHEME]"
    if REDIRECT_SCHEME_PATTERN.fullmatch(candidate) is None:
        return "[INVALID_SCHEME]"
    return candidate


def _safe_bounded_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        return 0
    return value


def _safe_redirect_decision_code(value: object) -> str:
    if not isinstance(value, str) or REDIRECT_DECISION_CODE_PATTERN.fullmatch(value) is None:
        return "INVALID_DECISION_CODE"
    return value


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
    install_redirect_rejection_handler()
    install_terminal_rejection_handler()


def install_redirect_rejection_handler() -> None:
    """Install exactly one project-owned handler on the dedicated logger."""

    redirect_logger = logging.getLogger("promo_bot.aliexpress_redirect_rejection")
    for handler in tuple(redirect_logger.handlers):
        if isinstance(handler, RedirectRejectionHandler):
            redirect_logger.removeHandler(handler)
            handler.close()
    handler = RedirectRejectionHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(SafeJsonFormatter())
    redirect_logger.addHandler(handler)
    redirect_logger.disabled = False
    redirect_logger.setLevel(logging.WARNING)
    redirect_logger.propagate = False


def install_terminal_rejection_handler() -> None:
    """Install exactly one project-owned handler on the terminal logger."""

    terminal_logger = logging.getLogger("promo_bot.aliexpress_terminal_rejection")
    for handler in tuple(terminal_logger.handlers):
        if isinstance(handler, TerminalRejectionHandler):
            terminal_logger.removeHandler(handler)
            handler.close()
    handler = TerminalRejectionHandler(sys.stderr)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(SafeJsonFormatter())
    terminal_logger.addHandler(handler)
    terminal_logger.disabled = False
    terminal_logger.setLevel(logging.WARNING)
    terminal_logger.propagate = False
