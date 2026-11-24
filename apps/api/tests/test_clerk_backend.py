"""Confirming a *primary* e-mail address with Clerk's Backend API.

Why this exists: the session-token shortcode `{{user.email_verified}}` documents itself as
"whether the user has a verified email address", not "whether the primary address is verified",
and no shortcode exposes the verification status of the primary address. A token can therefore say
`email = <primary>` and `email_verified = true` while only a *different* address is verified.
Admission to a private preview must not rest on that pair alone, so the API asks Clerk whether the
claimed address is the user's verified primary one. These tests use a mock transport: no network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from promo_api.auth.clerk_backend import ClerkBackendClient
from promo_api.errors import ApiError

SECRET = "sk_test_synthetic_secret_value"
SUBJECT = "user_synthetic_a"
PRIMARY = "primary@example.test"
OTHER = "other@example.test"


def address(identifier: str, email: str, status: str | None) -> dict[str, Any]:
    verification = None if status is None else {"status": status, "strategy": "email_code"}
    return {"id": identifier, "email_address": email, "verification": verification}


def user(
    *, primary_id: str | None = "idn_primary", addresses: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "id": SUBJECT,
        "primary_email_address_id": primary_id,
        "email_addresses": addresses
        if addresses is not None
        else [address("idn_primary", PRIMARY, "verified")],
    }


Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> ClerkBackendClient:
    return ClerkBackendClient(SECRET, transport=httpx.MockTransport(handler))


def answering(body: Any, status: int = 200) -> Handler:
    return lambda _: httpx.Response(status, json=body)


async def test_a_verified_primary_address_matching_the_claim_is_confirmed() -> None:
    assert await client_for(answering(user())).is_verified_primary(SUBJECT, PRIMARY) is True


async def test_the_comparison_ignores_case_and_surrounding_spaces() -> None:
    body = user(addresses=[address("idn_primary", "  Primary@Example.TEST ", "verified")])

    assert await client_for(answering(body)).is_verified_primary(SUBJECT, PRIMARY) is True


async def test_an_unverified_primary_is_refused_even_when_another_address_is_verified() -> None:
    # The documented gap: `email_verified` is true because OTHER is verified, yet PRIMARY is not.
    body = user(
        addresses=[
            address("idn_primary", PRIMARY, "unverified"),
            address("idn_other", OTHER, "verified"),
        ]
    )

    assert await client_for(answering(body)).is_verified_primary(SUBJECT, PRIMARY) is False


async def test_a_verified_address_that_is_not_the_primary_does_not_count() -> None:
    body = user(
        addresses=[
            address("idn_primary", PRIMARY, "verified"),
            address("idn_other", OTHER, "verified"),
        ]
    )

    assert await client_for(answering(body)).is_verified_primary(SUBJECT, OTHER) is False


@pytest.mark.parametrize("status", ["unverified", "expired", "failed", "transferable", "", None])
async def test_only_the_verified_status_counts(status: str | None) -> None:
    body = user(addresses=[address("idn_primary", PRIMARY, status)])

    assert await client_for(answering(body)).is_verified_primary(SUBJECT, PRIMARY) is False


@pytest.mark.parametrize(
    "body",
    [
        user(primary_id=None),
        user(primary_id="idn_missing"),
        user(addresses=[]),
        {**user(), "email_addresses": None},
        {"id": SUBJECT},
    ],
)
async def test_a_user_without_a_resolvable_primary_address_is_refused(body: dict[str, Any]) -> None:
    assert await client_for(answering(body)).is_verified_primary(SUBJECT, PRIMARY) is False


async def test_an_unknown_user_is_refused_not_an_error() -> None:
    handler = answering({"errors": [{"code": "resource_not_found"}]}, status=404)

    assert await client_for(handler).is_verified_primary(SUBJECT, PRIMARY) is False


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
async def test_a_provider_failure_is_a_retryable_503_never_an_admission(status: int) -> None:
    with pytest.raises(ApiError) as error:
        await client_for(answering({"errors": []}, status=status)).is_verified_primary(
            SUBJECT, PRIMARY
        )

    assert error.value.status_code == 503
    assert error.value.code == "IDENTITY_PROVIDER_UNAVAILABLE"
    assert (error.value.headers or {}).get("Retry-After") == "5"


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")],
)
async def test_a_network_failure_is_a_retryable_503(failure: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(ApiError) as error:
        await client_for(handler).is_verified_primary(SUBJECT, PRIMARY)

    assert error.value.code == "IDENTITY_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("content", [b"not json", b"[]", b'"text"', b"null"])
async def test_an_unexpected_response_shape_is_a_retryable_503(content: bytes) -> None:
    with pytest.raises(ApiError) as error:
        await client_for(lambda _: httpx.Response(200, content=content)).is_verified_primary(
            SUBJECT, PRIMARY
        )

    assert error.value.code == "IDENTITY_PROVIDER_UNAVAILABLE"


async def test_the_request_is_a_bearer_get_of_the_user_and_follows_no_redirect() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=user())

    await client_for(handler).is_verified_primary(SUBJECT, PRIMARY)

    (request,) = seen
    assert request.method == "GET"
    assert request.url.host == "api.clerk.com"
    assert request.url.path == f"/v1/users/{SUBJECT}"
    assert request.headers["Authorization"] == f"Bearer {SECRET}"


async def test_a_redirect_is_not_followed_and_is_a_provider_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/steal"})

    with pytest.raises(ApiError) as error:
        await client_for(handler).is_verified_primary(SUBJECT, PRIMARY)

    assert error.value.code == "IDENTITY_PROVIDER_UNAVAILABLE"


async def test_a_subject_cannot_alter_the_request_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404, json={})

    await client_for(handler).is_verified_primary("user_a/../../v1/sessions?x=1#y", PRIMARY)

    assert seen == ["/v1/users/user_a%2F..%2F..%2Fv1%2Fsessions%3Fx%3D1%23y"]


async def test_neither_the_secret_nor_the_address_reaches_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    for status in (401, 500):
        with pytest.raises(ApiError) as error:
            await client_for(
                answering({"errors": [SECRET, PRIMARY]}, status=status)
            ).is_verified_primary(SUBJECT, PRIMARY)
        assert SECRET not in repr(error.value) + str(error.value.message)
        assert PRIMARY not in repr(error.value) + str(error.value.message)

    assert SECRET not in caplog.text
    assert PRIMARY not in caplog.text


def test_the_secret_is_not_shown_by_repr() -> None:
    assert SECRET not in repr(ClerkBackendClient(SECRET))
