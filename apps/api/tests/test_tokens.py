"""Clerk session-token verification: the real verifier against synthetic RSA keys."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from promo_api.auth.tokens import ClerkTokenVerifier, JwksKeySource
from promo_api.errors import ApiError
from tests.conftest import ISSUER, KID, WEB_ORIGIN, StaticKeys, TokenFactory


@pytest.fixture
def verifier(key_source: StaticKeys) -> ClerkTokenVerifier:
    return ClerkTokenVerifier(
        issuer=ISSUER, authorized_parties=(WEB_ORIGIN,), key_source=key_source, leeway_seconds=5
    )


async def code_of(verifier: ClerkTokenVerifier, token: str) -> str:
    with pytest.raises(ApiError) as caught:
        await verifier.verify(token)
    assert caught.value.status_code == 401
    assert "WWW-Authenticate" in (caught.value.headers or {})
    assert not token or token not in caught.value.message
    return caught.value.code


async def test_a_valid_token_yields_the_verified_identity(
    verifier: ClerkTokenVerifier, make_token: TokenFactory
) -> None:
    principal = await verifier.verify(make_token("user_1", email="  Person@Example.TEST "))

    assert principal.subject == "user_1"
    assert principal.email == "person@example.test"
    assert principal.email_verified is True


@pytest.mark.parametrize("value", [False, "true", 1, None, "yes"])
async def test_only_a_literal_true_marks_the_email_as_verified(
    verifier: ClerkTokenVerifier, make_token: TokenFactory, value: object
) -> None:
    assert (await verifier.verify(make_token(email_verified=value))).email_verified is False


async def test_a_token_without_email_is_authenticated_but_carries_no_email(
    verifier: ClerkTokenVerifier, make_token: TokenFactory
) -> None:
    principal = await verifier.verify(make_token(email=None))

    assert principal.email is None


async def test_an_expired_token_is_refused_with_its_own_code(
    verifier: ClerkTokenVerifier, make_token: TokenFactory
) -> None:
    now = int(time.time())
    token = make_token(iat=now - 300, nbf=now - 300, exp=now - 120)

    assert await code_of(verifier, token) == "AUTH_TOKEN_EXPIRED"


def _unsigned(payload: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'kid': KID, 'typ': 'JWT'})}.{part(payload)}."


def _hmac_with_public_key(make_token: TokenFactory, key: rsa.RSAPrivateKey) -> str:
    """Algorithm confusion: HS256 keyed with the published RSA public key."""

    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    payload = jwt.decode(make_token(), options={"verify_signature": False})
    header = {"alg": "HS256", "kid": KID, "typ": "JWT"}

    def part(value: dict[str, Any]) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")

    import hashlib
    import hmac

    signing_input = part(header) + b"." + part(payload)
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


async def test_forged_and_malformed_tokens_are_refused(
    verifier: ClerkTokenVerifier,
    make_token: TokenFactory,
    signing_key: rsa.RSAPrivateKey,
    other_key: rsa.RSAPrivateKey,
) -> None:
    now = int(time.time())
    valid = make_token()
    head, body, signature = valid.split(".")
    tampered_body = jwt.utils.base64url_encode(
        json.dumps(
            {**jwt.decode(valid, options={"verify_signature": False}), "sub": "admin"}
        ).encode()
    ).decode()
    forged = {
        "signed_by_another_key": make_token(key=other_key),
        "unknown_kid": make_token(kid="rotated-away"),
        "missing_kid": make_token(kid=None),
        "payload_tampered": f"{head}.{tampered_body}.{signature}",
        "alg_none": _unsigned(jwt.decode(valid, options={"verify_signature": False})),
        "hmac_with_public_key": _hmac_with_public_key(make_token, signing_key),
        "wrong_issuer": make_token(iss="https://attacker.example"),
        "issuer_prefix": make_token(iss=ISSUER + ".attacker.example"),
        "wrong_azp": make_token(azp="https://attacker.example"),
        "missing_azp": make_token(drop=("azp",)),
        "not_yet_valid": make_token(nbf=now + 600),
        "missing_exp": make_token(drop=("exp",)),
        "missing_iat": make_token(drop=("iat",)),
        "missing_nbf": make_token(drop=("nbf",)),
        "missing_sub": make_token(drop=("sub",)),
        "empty_sub": make_token(""),
        "non_string_sub": make_token(sub=123),
        "oversized": valid + "A" * 9_000,
        "garbage": "not-a-token",
        "empty": "",
        "two_parts": f"{head}.{body}",
    }

    for name, token in forged.items():
        assert await code_of(verifier, token) == "AUTH_INVALID_TOKEN", name


def test_a_verifier_without_authorized_parties_cannot_be_built(key_source: StaticKeys) -> None:
    with pytest.raises(ValueError):
        ClerkTokenVerifier(issuer=ISSUER, authorized_parties=(), key_source=key_source)


# --- JWKS fetching -------------------------------------------------------------------------


class Jwks:
    """A fake issuer endpoint with a call counter and a switchable outage."""

    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
        self.document = {"keys": [{**jwk, "kid": KID, "use": "sig", "alg": "RS256"}]}
        self.calls = 0
        self.down = False
        self.now = 1_000.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            return httpx.Response(503)
        return httpx.Response(200, json=self.document)

    def source(self) -> JwksKeySource:
        return JwksKeySource(
            "https://synthetic-instance.clerk.example/.well-known/jwks.json",
            transport=httpx.MockTransport(self.handler),
            ttl_seconds=3_600,
            min_refresh_interval_seconds=60,
            clock=lambda: self.now,
        )


async def test_published_keys_are_fetched_once_and_verify_real_tokens(
    signing_key: rsa.RSAPrivateKey, make_token: TokenFactory
) -> None:
    jwks = Jwks(signing_key)
    verifier = ClerkTokenVerifier(
        issuer=ISSUER, authorized_parties=(WEB_ORIGIN,), key_source=jwks.source()
    )

    for _ in range(3):
        assert (await verifier.verify(make_token("user_1"))).subject == "user_1"

    assert jwks.calls == 1


async def test_unknown_key_ids_cannot_cause_a_fetch_storm(signing_key: rsa.RSAPrivateKey) -> None:
    jwks = Jwks(signing_key)
    source = jwks.source()

    assert await source.get(KID) is not None
    for index in range(20):
        assert await source.get(f"unknown-{index}") is None
    assert jwks.calls == 1  # misses inside the refresh interval never reach the issuer

    jwks.now += 61
    for index in range(20):
        assert await source.get(f"unknown-later-{index}") is None
    assert jwks.calls == 2  # one refresh per interval, however many unknown ids arrive


async def test_an_outage_without_cached_keys_is_a_503_not_an_open_door(
    signing_key: rsa.RSAPrivateKey, make_token: TokenFactory
) -> None:
    jwks = Jwks(signing_key)
    jwks.down = True
    verifier = ClerkTokenVerifier(
        issuer=ISSUER, authorized_parties=(WEB_ORIGIN,), key_source=jwks.source()
    )

    with pytest.raises(ApiError) as caught:
        await verifier.verify(make_token())

    assert (caught.value.status_code, caught.value.code) == (503, "AUTH_KEYS_UNAVAILABLE")


async def test_cached_keys_survive_a_short_outage_but_not_their_expiry(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    jwks = Jwks(signing_key)
    source = jwks.source()
    assert await source.get(KID) is not None

    jwks.down = True
    jwks.now += 120
    assert await source.get("unknown") is None  # refresh fails, cache kept
    assert await source.get(KID) is not None

    jwks.now += 3_700
    with pytest.raises(ApiError) as caught:
        await source.get(KID)
    assert caught.value.code == "AUTH_KEYS_UNAVAILABLE"


async def test_non_signing_and_malformed_documents_publish_no_usable_key(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    jwks = Jwks(signing_key)
    jwks.document = {"keys": [{**jwks.document["keys"][0], "use": "enc"}]}
    assert await jwks.source().get(KID) is None

    jwks.document = {"unexpected": True}
    with pytest.raises(ApiError):
        await jwks.source().get(KID)
