"""Layered validation for URLs that arrive from outside the process.

``urllib.parse.urlsplit`` silently drops ASCII tab, carriage return and line feed characters
before it inspects a URL, so a validator that looks only at parsed components can accept text such
as ``https://official.example/a\\nhttps://evil.example``. Every check here therefore looks at the
raw characters first and only then at the individual URL components.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

MAX_URL_LENGTH = 4_096
MAX_PROMOTION_LINK_LENGTH = 2_048

# RFC 3986 URI characters: unreserved, reserved and the percent sign. Anything else (spaces,
# control, format and bidi characters, quotes, angle brackets, non-ASCII text) is not part of a
# URI and must have been percent-encoded by the sender.
_URI_CHARACTERS = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class UnsafeLinkError(ValueError):
    """Carries only a fixed diagnostic code, never the rejected value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def has_forbidden_url_characters(value: str) -> bool:
    """Return whether *value* holds control, format, separator or whitespace characters.

    This covers CR, LF, tab, NUL and DEL, spaces, the Unicode line and paragraph separators and
    invisible format characters such as the bidi overrides and zero-width characters. Ordinary
    non-ASCII letters are allowed, because real store redirects can carry them.
    """

    return any(unicodedata.category(character).startswith(("C", "Z")) for character in value)


def validate_official_https_link(value: str, *, allowed_hosts: frozenset[str]) -> None:
    """Validate a link returned by an official API; never rewrites or normalizes it.

    The link must be a strict URI (see ``_URI_CHARACTERS``), use HTTPS on a host from
    *allowed_hosts*, carry no credentials, fragment or unusual port, and have a real path.
    Query parameters are left alone.
    """

    if not isinstance(value, str) or not value or len(value) > MAX_PROMOTION_LINK_LENGTH:
        raise UnsafeLinkError("LINK_LENGTH_INVALID")
    if _URI_CHARACTERS.fullmatch(value) is None or _MALFORMED_PERCENT_ESCAPE.search(value):
        raise UnsafeLinkError("LINK_CHARACTERS_INVALID")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise UnsafeLinkError("LINK_URL_INVALID") from None
    if parts.scheme != "https":
        raise UnsafeLinkError("LINK_SCHEME_INVALID")
    if "@" in parts.netloc:
        raise UnsafeLinkError("LINK_USERINFO_FORBIDDEN")
    if port not in {None, 443}:
        raise UnsafeLinkError("LINK_PORT_FORBIDDEN")
    if parts.hostname not in allowed_hosts:
        raise UnsafeLinkError("LINK_HOST_FORBIDDEN")
    if len(parts.path) <= 1:
        raise UnsafeLinkError("LINK_PATH_REQUIRED")
    if parts.fragment:
        raise UnsafeLinkError("LINK_FRAGMENT_FORBIDDEN")
