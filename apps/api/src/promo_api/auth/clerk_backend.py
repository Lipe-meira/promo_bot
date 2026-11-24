"""Confirming a user's *primary* e-mail address with Clerk's Backend API.

The session-token shortcode `{{user.email_verified}}` is documented as "whether the user has a
verified email address" (any address), and no shortcode exposes the verification status of the
primary one. A token can thus carry `email = <primary>` with `email_verified = true` while only a
different address is verified. Admission to the private preview is a one-time decision, so it
asks Clerk directly: is the claimed address the user's primary address *and* verified?

Fail-closed by construction: only a 200 response that positively shows a verified primary address
equal to the claimed one returns True; an unknown user returns False; everything else (network,
timeout, rejected credential, rate limit, server error, unexpected shape) is a retryable 503 that
never admits anyone. The secret key and the address are never logged or placed in an error.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from promo_api.errors import ApiError

_log = logging.getLogger("promo_api.clerk")

CLERK_BACKEND_URL = "https://api.clerk.com/v1"


class PrimaryEmailVerifier(Protocol):
    async def is_verified_primary(self, subject: str, email: str) -> bool:
        """True only if `email` is the verified primary address of the Clerk user `subject`."""


def identity_provider_unavailable() -> ApiError:
    return ApiError(
        503,
        "IDENTITY_PROVIDER_UNAVAILABLE",
        "Identity verification is temporarily unavailable. Try again shortly.",
        headers={"Retry-After": "5"},
    )


def _normalized(email: str) -> str:
    return email.strip().lower()


def _verified_primary_email(user: dict[str, Any]) -> str | None:
    primary_id = user.get("primary_email_address_id")
    addresses = user.get("email_addresses")
    if not isinstance(primary_id, str) or not isinstance(addresses, list):
        return None
    for item in addresses:
        if not isinstance(item, dict) or item.get("id") != primary_id:
            continue
        verification = item.get("verification")
        email = item.get("email_address")
        if (
            isinstance(verification, dict)
            and verification.get("status") == "verified"
            and isinstance(email, str)
        ):
            return _normalized(email)
        return None
    return None


class ClerkBackendClient:
    def __init__(
        self,
        secret_key: str | SecretStr,
        *,
        base_url: str = CLERK_BACKEND_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._secret = secret_key if isinstance(secret_key, SecretStr) else SecretStr(secret_key)
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout_seconds

    def __repr__(self) -> str:
        return f"ClerkBackendClient(base_url={self._base_url!r})"

    async def is_verified_primary(self, subject: str, email: str) -> bool:
        url = f"{self._base_url}/users/{quote(subject, safe='')}"
        headers = {
            "Authorization": f"Bearer {self._secret.get_secret_value()}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout, follow_redirects=False
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as error:
            _log.warning("clerk backend unreachable (%s)", type(error).__name__)
            raise identity_provider_unavailable() from None

        if response.status_code == 404:
            return False  # not a Clerk user of this instance: nothing to confirm
        if response.status_code != 200:
            level = logging.ERROR if response.status_code in {401, 403} else logging.WARNING
            _log.log(level, "clerk backend answered HTTP %s", response.status_code)
            raise identity_provider_unavailable()
        try:
            user = response.json()
        except ValueError:
            user = None
        if not isinstance(user, dict):
            _log.warning("clerk backend answered with an unexpected shape")
            raise identity_provider_unavailable()
        verified = _verified_primary_email(user)
        return verified is not None and verified == _normalized(email)
