# AliExpress TOP Signing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline-only, deterministic Python implementation of the confirmed AliExpress TOP signer and Java-SDK-compatible request representation without enabling transport.

**Architecture:** A new deep module owns canonical parameter filtering, HMAC-SHA256 signing, and immutable query/form serialization. It produces a prepared relative request with duplicate wire-level `method` pairs while keeping the canonical signed map unique. No runtime client or HTTP transport imports this module.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `hashlib`, `hmac`, `time`, `urllib.parse`), pytest, Ruff, mypy, uv.

**Spec:** `docs/superpowers/specs/2026-09-04-aliexpress-top-signing-design.md`

## Global Constraints

- Support only the six authorized AliExpress Affiliate operations.
- Preserve `UnavailableAliExpressAffiliateClient` and `ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE`.
- Do not connect the new module to `AliExpressHttpTransport` or configure a gateway.
- Keep the provider disabled and preserve every publication, dry-run, search, and live-test gate.
- Do not change database models or migrations.
- Do not execute JARs, make live calls, run live tests, or publish to Telegram.
- Never expose app secrets, signatures, tracking IDs, session/access tokens, or business values through representations, logs, or errors.
- Do not reproduce the Java SDK certificate or hostname verification bypasses.
- Use Conventional Commits and stage only task-specific files after reviewing status and diffs.

---

### Task 1: Core TOP signer and prepared request

**Files:**
- Create: `src/promo_bot/providers/aliexpress/top.py`
- Create: `tests/unit/test_aliexpress_top.py`

**Interfaces:**
- Consumes: operation constants from `promo_bot.providers.aliexpress.contracts` and business payload mappings containing `str | None` values.
- Produces: `AliExpressTopRequestBuilder.prepare(operation, business_parameters, *, timestamp_ms=None, session=None, debug=False) -> PreparedAliExpressTopRequest`.
- Produces: immutable `PreparedAliExpressTopRequest` fields `method`, `path`, `query_pairs`, `form_pairs`, and `content_type`, plus `relative_url()` and `encoded_form()`.

- [ ] **Step 1: Write failing core behavior tests**

Create `tests/unit/test_aliexpress_top.py` with imports, constants, an independent expected-signature helper, and tests equivalent to:

```python
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qsl, urlsplit

import pytest

from promo_bot.providers.aliexpress.contracts import (
    LINK_GENERATE,
    PRODUCT_DETAIL,
    PRODUCT_QUERY,
    PRODUCT_SHIPPING,
    PROMOTION_INFO,
    SKU_DETAIL,
)
from promo_bot.providers.aliexpress.top import AliExpressTopRequestBuilder

APP_KEY = "fixture-app-key"
APP_SECRET = "fixture-secret"
TIMESTAMP_MS = 1_788_498_000_123


def expected_signature(parameters: dict[str, str]) -> str:
    canonical = "".join(
        f"{key}{parameters[key]}"
        for key in sorted(parameters)
        if key.strip() and parameters[key].strip()
    )
    return hmac.new(
        APP_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def test_prepared_request_preserves_java_method_compatibility_quirk() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE,
        {"tracking_id": "fixture-tracking", "source_values": "https://example.test/item/1"},
        timestamp_ms=TIMESTAMP_MS,
    )

    assert request.method == "POST"
    assert request.path == "/sync"
    assert [pair for pair in request.query_pairs if pair[0] == "method"] == [
        ("method", LINK_GENERATE),
        ("method", LINK_GENERATE),
    ]
    assert parse_qsl(urlsplit(request.relative_url()).query).count(
        ("method", LINK_GENERATE)
    ) == 2
    assert request.relative_url().count("method=") == 2


def test_signature_uses_one_method_and_excludes_sign() -> None:
    business = {"tracking_id": "fixture-tracking", "source_values": "item"}
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE, business, timestamp_ms=TIMESTAMP_MS
    )
    signed = {
        "app_key": APP_KEY,
        "format": "json",
        "method": LINK_GENERATE,
        "partner_id": "iop-sdk-java-20181207",
        "sign_method": "sha256",
        "simplify": "true",
        "timestamp": str(TIMESTAMP_MS),
        "v": "2.0",
        **business,
    }
    query_signatures = [value for key, value in request.query_pairs if key == "sign"]

    assert query_signatures == [expected_signature(signed)]
    assert "sign" not in signed


def test_signature_sorting_is_independent_of_business_input_order() -> None:
    first = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_QUERY,
        {"zeta": "último", "alpha": "primeiro"},
        timestamp_ms=TIMESTAMP_MS,
    )
    second = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_QUERY,
        {"alpha": "primeiro", "zeta": "último"},
        timestamp_ms=TIMESTAMP_MS,
    )

    assert dict(first.query_pairs)["sign"] == dict(second.query_pairs)["sign"]


def test_common_query_and_business_form_are_separate_and_deterministic() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_DETAIL,
        {"tracking_id": "fixture", "country": "BR", "empty": "", "null": None},
        timestamp_ms=TIMESTAMP_MS,
    )

    assert request.query_pairs[0] == ("method", PRODUCT_DETAIL)
    assert request.query_pairs[1:] == tuple(sorted(request.query_pairs[1:]))
    assert request.form_pairs == (("country", "BR"), ("tracking_id", "fixture"))
    assert "tracking_id" not in {name for name, _ in request.query_pairs}
    assert "app_key" not in {name for name, _ in request.form_pairs}
    assert request.encoded_form() == "country=BR&tracking_id=fixture"


@pytest.mark.parametrize(
    "operation",
    [PRODUCT_DETAIL, PRODUCT_QUERY, LINK_GENERATE, SKU_DETAIL, PRODUCT_SHIPPING, PROMOTION_INFO],
)
def test_only_authorized_affiliate_operations_are_supported(operation: str) -> None:
    AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        operation, {}, timestamp_ms=TIMESTAMP_MS
    )
```

- [ ] **Step 2: Run the new test file and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_aliexpress_top.py -q
```

Expected: collection fails because `promo_bot.providers.aliexpress.top` does not exist.

- [ ] **Step 3: Implement the minimum core module**

Create `src/promo_bot/providers/aliexpress/top.py` with these concrete elements:

```python
from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias
from urllib.parse import quote_plus

from promo_bot.providers.aliexpress.contracts import (
    LINK_GENERATE,
    PRODUCT_DETAIL,
    PRODUCT_QUERY,
    PRODUCT_SHIPPING,
    PROMOTION_INFO,
    SKU_DETAIL,
)

ParameterPair: TypeAlias = tuple[str, str]
AUTHORIZED_OPERATIONS: Final[frozenset[str]] = frozenset(
    {PRODUCT_DETAIL, PRODUCT_QUERY, LINK_GENERATE, SKU_DETAIL, PRODUCT_SHIPPING, PROMOTION_INFO}
)
RESERVED_PARAMETER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "app_key", "timestamp", "sign", "sign_method", "method", "format",
        "v", "partner_id", "session", "access_token", "simplify", "debug",
    }
)
CONTENT_TYPE: Final[str] = "application/x-www-form-urlencoded;charset=UTF-8"
PARTNER_ID: Final[str] = "iop-sdk-java-20181207"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedAliExpressTopRequest:
    method: Literal["POST"]
    path: Literal["/sync"]
    query_pairs: tuple[ParameterPair, ...]
    form_pairs: tuple[ParameterPair, ...]
    content_type: str

    def relative_url(self) -> str:
        return f"{self.path}?{_encode_pairs(self.query_pairs)}"

    def encoded_form(self) -> str:
        return _encode_pairs(self.form_pairs)

    def __repr__(self) -> str:
        return (
            "PreparedAliExpressTopRequest(method='POST', path='/sync', "
            f"query_pairs=<redacted:{len(self.query_pairs)}>, "
            f"form_pairs=<redacted:{len(self.form_pairs)}>)"
        )

    __str__ = __repr__


class AliExpressTopRequestBuilder:
    __slots__ = ("_app_key", "_app_secret")

    def __init__(self, app_key: str, app_secret: str) -> None:
        self._app_key = _required_credential(app_key)
        self._app_secret = _required_credential(app_secret)

    def prepare(
        self,
        operation: str,
        business_parameters: Mapping[str, str | None],
        *,
        timestamp_ms: int | None = None,
        session: str | None = None,
        debug: bool = False,
    ) -> PreparedAliExpressTopRequest:
        if operation not in AUTHORIZED_OPERATIONS:
            raise ValueError("unsupported AliExpress Affiliate operation")
        _reject_reserved_collisions(business_parameters)
        business = _normalized_parameters(business_parameters)
        timestamp = _timestamp_text(timestamp_ms)
        common = {
            "app_key": self._app_key,
            "format": "json",
            "method": operation,
            "partner_id": PARTNER_ID,
            "sign_method": "sha256",
            "simplify": "true",
            "timestamp": timestamp,
            "v": "2.0",
        }
        if session is not None and session.strip():
            common["session"] = session
        if debug:
            common["debug"] = "true"
        signed_parameters = {**common, **business}
        signature = _sign(signed_parameters, self._app_secret)
        common["sign"] = signature
        query_pairs = (("method", operation), *sorted(common.items()))
        return PreparedAliExpressTopRequest(
            method="POST",
            path="/sync",
            query_pairs=query_pairs,
            form_pairs=tuple(sorted(business.items())),
            content_type=CONTENT_TYPE,
        )

    def __repr__(self) -> str:
        return "AliExpressTopRequestBuilder(app_key=<redacted>, app_secret=<redacted>)"

    __str__ = __repr__
```

Implement private helpers `_required_credential`, `_reject_reserved_collisions`,
`_normalized_parameters`, `_timestamp_text`, `_sign`, and `_encode_pairs`. `_sign` must sort keys,
concatenate key/value pairs, use UTF-8 HMAC-SHA256, and uppercase `hexdigest()`. `_encode_pairs` must
preserve pair order, encode values with `quote_plus(..., encoding="utf-8", errors="strict")`, and
leave the known ASCII parameter names unchanged.

- [ ] **Step 4: Run the new test file and verify GREEN**

Run:

```powershell
uv run pytest tests/unit/test_aliexpress_top.py -q
```

Expected: all core tests pass with socket access disabled by project configuration.

- [ ] **Step 5: Review and commit core implementation**

Review `git status`, unstaged diff, staged diff, and staged secret scan. Stage only the two task files.

```powershell
git add -- src/promo_bot/providers/aliexpress/top.py tests/unit/test_aliexpress_top.py
git commit -m "feat: add offline AliExpress TOP signing"
```

---

### Task 2: Validation, Unicode, and sanitization hardening

**Files:**
- Modify: `src/promo_bot/providers/aliexpress/top.py`
- Modify: `tests/unit/test_aliexpress_top.py`

**Interfaces:**
- Consumes: the Task 1 builder and immutable prepared request.
- Produces: fail-closed validation for reserved names and invalid values, verified Unicode behavior, and sanitized representations/logging/errors.

- [ ] **Step 1: Add failing hardening tests**

Add tests equivalent to:

```python
RESERVED = {
    "app_key", "timestamp", "sign", "sign_method", "method", "format",
    "v", "partner_id", "session", "access_token", "simplify", "debug",
}


@pytest.mark.parametrize("reserved", sorted(RESERVED))
def test_reserved_business_parameter_collisions_are_rejected(reserved: str) -> None:
    with pytest.raises(ValueError, match="reserved TOP parameter"):
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            LINK_GENERATE, {reserved: "attacker-value"}, timestamp_ms=TIMESTAMP_MS
        )


def test_unsupported_dotted_operation_is_rejected_without_echoing_it() -> None:
    operation = "aliexpress.affiliate.order.list"
    with pytest.raises(ValueError) as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            operation, {}, timestamp_ms=TIMESTAMP_MS
        )
    assert operation not in str(captured.value)


def test_unicode_is_signed_and_encoded_as_utf8() -> None:
    business = {"keywords": "café promoção", "tracking_id": "fixture"}
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        PRODUCT_QUERY, business, timestamp_ms=TIMESTAMP_MS
    )
    signed = {
        "app_key": APP_KEY, "format": "json", "method": PRODUCT_QUERY,
        "partner_id": "iop-sdk-java-20181207", "sign_method": "sha256",
        "simplify": "true", "timestamp": str(TIMESTAMP_MS), "v": "2.0", **business,
    }
    assert dict(request.query_pairs)["sign"] == expected_signature(signed)
    assert "keywords=caf%C3%A9+promo%C3%A7%C3%A3o" in request.encoded_form()


def test_timestamp_is_fixed_unix_milliseconds() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE, {}, timestamp_ms=TIMESTAMP_MS
    )
    assert ("timestamp", str(TIMESTAMP_MS)) in request.query_pairs


def test_signature_is_uppercase_hex() -> None:
    request = AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
        LINK_GENERATE, {}, timestamp_ms=TIMESTAMP_MS
    )
    signature = dict(request.query_pairs)["sign"]
    assert len(signature) == 64
    assert signature == signature.upper()
    assert set(signature) <= set("0123456789ABCDEF")


def test_repr_str_logs_and_errors_do_not_expose_sensitive_values(caplog) -> None:
    secret = "never-show-secret"
    tracking = "never-show-tracking"
    session = "never-show-session-token"
    body_value = "never-show-body-value"
    builder = AliExpressTopRequestBuilder(APP_KEY, secret)
    request = builder.prepare(
        LINK_GENERATE,
        {"tracking_id": tracking, "source_values": body_value},
        timestamp_ms=TIMESTAMP_MS,
        session=session,
    )
    signature = dict(request.query_pairs)["sign"]

    import logging
    logging.getLogger("test.aliexpress.top").warning("%r %r", builder, request)
    visible = " ".join((repr(builder), str(builder), repr(request), str(request), caplog.text))
    for sensitive in (secret, tracking, session, body_value, signature):
        assert sensitive not in visible


def test_arbitrary_invalid_value_is_not_echoed_in_error() -> None:
    sensitive = "never-echo-invalid-body-value"
    with pytest.raises((TypeError, ValueError)) as captured:
        AliExpressTopRequestBuilder(APP_KEY, APP_SECRET).prepare(
            LINK_GENERATE,
            {"tracking_id": (sensitive, object())},  # type: ignore[dict-item]
            timestamp_ms=TIMESTAMP_MS,
        )
    assert sensitive not in str(captured.value)
```

Also add tests that whitespace-only keys/values are omitted without trimming nonblank values, blank
sessions are absent, invalid timestamps fail with generic messages, and `repr` contains no literal
parameter values.

- [ ] **Step 2: Run hardening tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_aliexpress_top.py -q
```

Expected: new tests fail on missing or incomplete fail-closed validation.

- [ ] **Step 3: Implement minimum hardening behavior**

Complete the private helpers with these rules:

```python
def _required_credential(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("AliExpress credential must be a non-empty string")
    return value


def _reject_reserved_collisions(parameters: Mapping[str, str | None]) -> None:
    if any(name in RESERVED_PARAMETER_NAMES for name in parameters):
        raise ValueError("business parameters contain a reserved TOP parameter")


def _normalized_parameters(parameters: Mapping[str, str | None]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, value in parameters.items():
        if not isinstance(name, str):
            raise TypeError("business parameter names must be strings")
        if value is not None and not isinstance(value, str):
            raise TypeError("business parameter values must be strings or null")
        if name.strip() and value is not None and value.strip():
            normalized[name] = value
    return normalized


def _timestamp_text(timestamp_ms: int | None) -> str:
    value = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("timestamp_ms must be an integer number of milliseconds")
    return str(value)
```

Keep all error text generic. Do not log inside the module.

- [ ] **Step 4: Run focused quality gates and verify GREEN**

Run:

```powershell
uv run pytest tests/unit/test_aliexpress_top.py -q
uv run ruff check src/promo_bot/providers/aliexpress/top.py tests/unit/test_aliexpress_top.py
uv run ruff format --check src/promo_bot/providers/aliexpress/top.py tests/unit/test_aliexpress_top.py
uv run mypy src
```

Expected: all commands exit zero.

- [ ] **Step 5: Review and commit hardening**

Review status and both diffs, stage only the two task files, run `git diff --cached --check`, and scan
the staged patch for credentials.

```powershell
git add -- src/promo_bot/providers/aliexpress/top.py tests/unit/test_aliexpress_top.py
git commit -m "fix: harden AliExpress TOP request preparation"
```

---

### Task 3: Record SDK evidence and complete offline verification

**Files:**
- Modify: `docs/ALIEXPRESS_AFFILIATE.md`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: confirmed SDK evidence and the implemented offline module.
- Produces: explicit documentation separating confirmed signer behavior, Java compatibility peculiarity, Python determinism decision, and unknown gateway contract.

- [ ] **Step 1: Update technical documentation**

Update `docs/ALIEXPRESS_AFFILIATE.md` with:

- source JAR identity `iop-api-sdk 1.3.5-ae` and inspected class names;
- exact canonical signing algorithm and common parameters;
- TOP `POST`, `/sync`, common query, and business form layout;
- the duplicate `method` observation labeled non-normative compatibility behavior;
- deterministic Python wire sorting labeled an implementation choice;
- external `serverUrl` and absent AliExpress Affiliate gateway;
- unchanged unavailable-client, provider, publication, TLS, and live-call gates.

Update the AliExpress section of `docs/ARCHITECTURE.md` to describe the offline prepared-request
module and state that it is not connected to the HTTP transport.

- [ ] **Step 2: Run the complete validation gate**

Run exactly:

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run promo-bot --dry-run
uv run promo-bot aliexpress status
uv run promo-bot aliexpress preview --url "https://pt.aliexpress.com/item/1005000000000001.html"
```

If `promo-bot --dry-run` is not the repository's controlled smoke syntax, inspect `--help` and use
the documented offline smoke command without enabling network access.

- [ ] **Step 3: Verify gates and absence of prohibited integration**

Run read-only checks proving:

```powershell
rg -n "AliExpressTopRequestBuilder|PreparedAliExpressTopRequest" src tests docs
rg -n "from promo_bot.providers.aliexpress.top|import promo_bot.providers.aliexpress.top" src/promo_bot/providers/aliexpress/transport.py src/promo_bot/providers/aliexpress/client.py
git diff -- migrations src/promo_bot/database
git status --short
```

The transport/client import search and database diff must be empty. Confirm provider defaults,
`SIGNING_CONTRACT_UNAVAILABLE`, and live-test exclusions remain unchanged.

- [ ] **Step 4: Run secret scan and review final diff**

Scan tracked and staged content for private keys, bearer tokens, credential assignments, complete
signatures, and non-placeholder tracking IDs. Review `git diff`, `git diff --cached`, and
`git diff --check`. Do not print any real `.env` values.

- [ ] **Step 5: Commit documentation only after all gates pass**

```powershell
git add -- docs/ALIEXPRESS_AFFILIATE.md docs/ARCHITECTURE.md
git commit -m "docs: record AliExpress TOP SDK evidence"
```

- [ ] **Step 6: Remove extracted attachments and push**

Resolve the exact temporary analysis directory, verify that it is under the current user's system
temporary directory and outside `F:\projetos\promo_bot`, then remove only that directory. Re-run
`git status`, compare `HEAD` with the configured upstream, and push the current branch only if every
validation and security gate passed:

```powershell
git push origin feat/promo-affiliate-bot-mvp
```

Report initial and final Git state, attachment hashes, inspected Java classes, evidence
classifications, changed files, tests, commits, push result, cleanup, and confirmation that no JAR,
live request, live test, gateway, or publication was executed.
