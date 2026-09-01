"""Fail-closed validation for links copied from the official generator UI."""

from __future__ import annotations

from urllib.parse import urlsplit

from promo_bot.stores.urls import normalize_hostname


def validate_affiliate_link(value: str, *, allowed_hosts: frozenset[str]) -> tuple[str, str]:
    """Return the exact link and sanitized hostname after strict validation."""

    link = value.strip()
    if not link or not allowed_hosts:
        raise ValueError("affiliate link validation requires a non-empty confirmed host allowlist")
    try:
        parts = urlsplit(link)
        hostname = normalize_hostname(parts.hostname) if parts.hostname else ""
        port = parts.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError("affiliate link is malformed") from exc
    confirmed_hosts = frozenset(normalize_hostname(host) for host in allowed_hosts)
    if (
        parts.scheme.casefold() != "https"
        or not hostname
        or hostname not in confirmed_hosts
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or not parts.path
    ):
        raise ValueError("affiliate link is not an allowed official HTTPS result")
    return link, hostname
