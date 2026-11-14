"""Verification of Clerk session tokens (RS256 JWTs signed by the instance's JWKS).

The API trusts nothing sent by the browser except a token that verifies against the configured
issuer: signature, `exp`/`nbf`/`iat`, `iss` and the `azp` origin. Roles and workspace ids are
never read from the token or the request; authorization lives in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt

from promo_api.errors import ApiError

_ALGORITHM = "RS256"
_MAX_TOKEN_LENGTH = 8_192
_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "sub"]


@dataclass(frozen=True, slots=True)
class Principal:
    """A verified Clerk identity. `email` is only usable for admission when verified."""

    subject: str
    email: str | None
    email_verified: bool


class SigningKeySource(Protocol):
    async def get(self, kid: str) -> Any | None:
        """The public key for `kid`, or None when the issuer does not publish it."""


def invalid_token() -> ApiError:
    return ApiError(
        401,
        "AUTH_INVALID_TOKEN",
        "Authentication is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _expired_token() -> ApiError:
    return ApiError(
        401,
        "AUTH_TOKEN_EXPIRED",
        "The session token has expired.",
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


def _keys_unavailable() -> ApiError:
    return ApiError(503, "AUTH_KEYS_UNAVAILABLE", "Identity keys are temporarily unavailable.")


class JwksKeySource:
    """Fetches and caches the issuer's JWKS. Unknown `kid`s cannot force a fetch storm."""

    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_seconds: float = 3_600,
        min_refresh_interval_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._transport = transport
        self._ttl = ttl_seconds
        self._min_interval = min_refresh_interval_seconds
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> Any | None:
        if kid in self._keys and not self._stale():
            return self._keys[kid]
        async with self._lock:
            if kid in self._keys and not self._stale():
                return self._keys[kid]
            if self._may_refresh():
                await self._refresh()
            elif self._stale():
                raise _keys_unavailable()
            return self._keys.get(kid)

    def _stale(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at > self._ttl

    def _may_refresh(self) -> bool:
        return (
            self._attempted_at is None or self._clock() - self._attempted_at >= self._min_interval
        )

    async def _refresh(self) -> None:
        self._attempted_at = self._clock()
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=5.0, follow_redirects=False
            ) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                keys = jwt.PyJWKSet.from_dict(response.json()).keys
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError):
            if self._stale():
                self._keys = {}
                raise _keys_unavailable() from None
            return  # keep serving the still-valid cached keys
        self._keys = {
            key.key_id: key.key
            for key in keys
            if key.key_id and key.key_type == "RSA" and key.public_key_use in (None, "sig")
        }
        self._fetched_at = self._clock()


class ClerkTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        authorized_parties: tuple[str, ...],
        key_source: SigningKeySource,
        leeway_seconds: int = 5,
    ) -> None:
        if not authorized_parties:
            raise ValueError("authorized_parties cannot be empty")
        self._issuer = issuer
        self._authorized_parties = frozenset(authorized_parties)
        self._key_source = key_source
        self._leeway = leeway_seconds

    async def verify(self, token: str) -> Principal:
        if not token or len(token) > _MAX_TOKEN_LENGTH:
            raise invalid_token()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise invalid_token() from None
        kid = header.get("kid")
        if header.get("alg") != _ALGORITHM or not isinstance(kid, str) or not kid:
            raise invalid_token()
        key = await self._key_source.get(kid)
        if key is None:
            raise invalid_token()
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": _REQUIRED_CLAIMS, "verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise _expired_token() from None
        except jwt.PyJWTError:
            raise invalid_token() from None

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise invalid_token()
        # `azp` is the origin the session was issued to; a token minted for another site is refused.
        if claims.get("azp") not in self._authorized_parties:
            raise invalid_token()
        email = claims.get("email")
        normalized = email.strip().lower() if isinstance(email, str) and email.strip() else None
        return Principal(
            subject=subject,
            email=normalized if normalized and len(normalized) <= 320 else None,
            email_verified=claims.get("email_verified") is True,
        )
