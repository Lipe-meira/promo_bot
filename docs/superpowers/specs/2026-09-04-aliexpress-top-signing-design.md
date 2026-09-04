# AliExpress TOP signing and offline request preparation

## Scope

Implement an offline-only Python model of the AliExpress Affiliate TOP signing and request
serialization behavior evidenced by the official Java SDK attachments. The module must not perform
HTTP requests, select a gateway, enable the provider, or weaken any publication or TLS gate.

The supported operations are limited to:

- `aliexpress.affiliate.productdetail.get`;
- `aliexpress.affiliate.product.query`;
- `aliexpress.affiliate.link.generate`;
- `aliexpress.affiliate.product.sku.detail.get`;
- `aliexpress.affiliate.product.shipping.get`;
- `aliexpress.affiliate.promotion.info.get`.

## Evidence and classifications

### Confirmed signer behavior

For TOP requests, the Java SDK builds one canonical parameter map from common and business
parameters. It removes parameters whose key or value is null, empty, or whitespace-only, then sorts
the remaining keys lexicographically. The signed text is the concatenation of each key immediately
followed by its value, without separators. TOP passes an empty API-name prefix and no body suffix to
the shared signing routine.

The app secret and signed text are encoded as UTF-8. The digest uses HMAC-SHA256 and is rendered as
uppercase hexadecimal. The common `sign_method` value is `sha256`, although the Java implementation
uses the HmacSHA256 primitive for both accepted SHA-256 labels. The timestamp originates from
`System.currentTimeMillis()` and is serialized as Unix milliseconds. The `sign` parameter is absent
while the digest is calculated and is added only afterward.

The common TOP parameters evidenced by the SDK are `app_key`, `v=2.0`, `timestamp`, `method`,
`format=json`, optional `session`, `partner_id`, `sign_method=sha256`, `simplify`, optional `debug`,
and the post-calculation `sign`. Business parameters participate in the same canonical signed map.

### Java SDK compatibility peculiarity

`TopExecutor` first constructs `/sync?method=<operation>`. `BaseExecutor` then appends the common
parameters as another query string, and those common parameters contain the same `method` again.
The resulting Java wire representation therefore contains two identical `method` pairs.

This duplication is an observed compatibility peculiarity of the official Java SDK. It is not a
normative API contract. The canonical map passed to the signer contains `method` exactly once; the
duplication happens only while serializing the wire query.

### Python determinism decision

The Java SDK stores common and business parameters in `HashMap` and does not establish physical
wire ordering as part of the contract. The Python implementation will sort the common query pairs
and business form pairs for reproducible offline tests. The leading routing pair
`("method", operation)` remains first, followed by sorted common pairs, including their second
`method` occurrence.

Sorting for wire output is a Python implementation decision. Only lexical sorting for the signed
text is confirmed signer behavior.

### Unknown gateway contract

The SDK receives `serverUrl` from its caller. It contains no unequivocal AliExpress Affiliate base
URL. The only hard-coded gateway constants found in the source target Taobao Taiwan and are not
applicable evidence. The production gateway, host, region, and operational request acceptance
remain unknown.

No URL, transport adapter, live request, API Testing Tool action, or Telegram publication is in
scope. `ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE` remains the active runtime gate.

## Module design

Add a focused module at `src/promo_bot/providers/aliexpress/top.py` with a small interface that
hides signing and serialization details.

### Prepared request

An immutable prepared-request value exposes:

- HTTP method `POST`;
- path `/sync`;
- query as an immutable ordered sequence of `(name, value)` pairs;
- form body as a separate immutable ordered sequence of `(name, value)` pairs;
- content type `application/x-www-form-urlencoded` with UTF-8;
- a relative URL serializer that preserves the two `method` pairs without adding a third.

The prepared value must use a custom sanitized representation. Its `repr`, `str`, validation
errors, and related logging must never expose app secrets, signatures, tracking IDs, access/session
tokens, or business values.

### Builder and signer

The builder accepts only a supported operation, app key, app secret, business parameters, and a
fixed or generated millisecond timestamp. Optional TOP common parameters are modeled only where
confirmed by the SDK. It performs these steps:

1. reject unsupported operations;
2. reject business keys colliding with reserved common names;
3. normalize away null, empty, and whitespace-only keys or values;
4. create the canonical common map with one `method`;
5. merge the normalized business parameters for signing;
6. remove `sign` defensively before canonical composition;
7. calculate the uppercase HMAC-SHA256 signature over UTF-8 bytes;
8. add `sign` to the common wire parameters;
9. create a query sequence with the routing `method` pair first and sorted common pairs afterward;
10. create a separately sorted business form sequence.

Reserved business names include at least `app_key`, `timestamp`, `sign`, `sign_method`, `method`,
`format`, `v`, `partner_id`, `session`, `access_token`, `simplify`, and `debug`.

The module does not implement the existing `AliExpressRequestSigner` transport protocol and is not
connected to `AliExpressHttpTransport`.

## Security and error behavior

- The app secret is kept only in the builder's private state and is excluded from representations.
- Prepared-request representations contain only structural counts and fixed metadata.
- Validation errors identify invalid parameter names only when the name is a fixed reserved name;
  they never echo arbitrary keys or values.
- No helper returns or stores the canonical signed text.
- No logger receives the request object, query, form, signature, secret, or tracking ID.
- TLS behavior is unchanged; the Java SDK certificate and hostname bypasses are not reproduced.
- Existing provider, publication, dry-run, search, and live-test gates remain unchanged.

## Testing

Use strict red-green-refactor cycles. Deterministic tests use a fixed timestamp and dummy
credentials. Tests must prove:

- the six-operation allowlist;
- rejection of every reserved-name collision;
- two identical `method` pairs in the prepared query;
- one `method` occurrence in the canonical signed content;
- no third `method` after relative URL composition;
- `sign` absent during digest calculation and present afterward;
- common parameters in query and business parameters in the form;
- lexical sorting applies to signing regardless of input order;
- deterministic physical ordering is the Python policy, not inferred API behavior;
- UTF-8 and Unicode handling;
- fixed Unix-millisecond timestamp serialization;
- uppercase hexadecimal digest;
- null, empty, and whitespace-only filtering;
- sanitized `repr`, `str`, logs, and errors;
- no network access and no integration with `AliExpressHttpTransport`.

Run the complete offline gate: locked dependency sync, Ruff lint, Ruff format check, mypy, offline
pytest, dry-run smoke test, offline `aliexpress status`, offline `aliexpress preview`, secret scan,
and final Git diff/status review.

## Repository impact

Expected production changes are limited to the new TOP signing/preparation module and technical
documentation. Expected test changes are limited to new offline unit tests. No database model,
migration, transport wiring, provider enablement, configuration default, live test, or publication
code changes are permitted.
