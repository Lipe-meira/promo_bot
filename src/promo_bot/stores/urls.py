"""Strict URL identification and canonicalization for the five Phase 2 stores."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from promo_bot.domain.enums import RelayLinkState, Store

STORE_HOSTS: dict[Store, frozenset[str]] = {
    Store.AMAZON: frozenset({"amazon.com.br", "www.amazon.com.br"}),
    Store.MERCADOLIVRE: frozenset(
        {
            "mercadolivre.com.br",
            "www.mercadolivre.com.br",
            "produto.mercadolivre.com.br",
        }
    ),
    Store.SHOPEE: frozenset({"shopee.com.br", "www.shopee.com.br"}),
    Store.ALIEXPRESS: frozenset({"aliexpress.com", "www.aliexpress.com", "pt.aliexpress.com"}),
    Store.KABUM: frozenset({"kabum.com.br", "www.kabum.com.br"}),
}
SHORTENER_HOSTS = frozenset(
    {"amzn.to", "meli.la", "s.shopee.com.br", "a.aliexpress.com", "s.click.aliexpress.com"}
)
ALLOWED_NETWORK_HOSTS = frozenset().union(*STORE_HOSTS.values(), SHORTENER_HOSTS)

AMAZON_PRODUCT = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE)
MERCADOLIVRE_PRODUCT = re.compile(r"MLB[-_]?([0-9]{5,})", re.IGNORECASE)
SHOPEE_PRODUCT = re.compile(r"/product/([0-9]+)/([0-9]+)(?:[/?]|$)", re.IGNORECASE)
SHOPEE_I_PRODUCT = re.compile(r"-i\.([0-9]+)\.([0-9]+)(?:[/?]|$)", re.IGNORECASE)
ALIEXPRESS_PRODUCT = re.compile(r"/item/([0-9]+)\.html(?:[/?]|$)", re.IGNORECASE)
KABUM_PRODUCT = re.compile(r"/produto/([0-9]+)(?:[/?]|$)", re.IGNORECASE)

AMBIGUOUS_VARIATION_KEYS = frozenset(
    {"color", "colour", "model", "size", "sku", "variant", "variation", "variacao"}
)
REMOVABLE_QUERY_KEYS = frozenset(
    {
        "ascsubtag",
        "awc",
        "creativeasin",
        "gatewayadapt",
        "linkcode",
        "psc",
        "ref",
        "share_channel_code",
        "sk",
        "smid",
        "sp_atk",
        "spm",
        "tag",
        "th",
        "tracking_id",
        "uls_trackid",
        "xptdk",
    }
)
REMOVABLE_QUERY_PREFIXES = ("aff_", "matt_", "ref_", "utm_")


@dataclass(frozen=True, slots=True)
class CanonicalUrlResult:
    state: RelayLinkState
    reason_code: str
    store: Store | None = None
    external_product_id: str | None = None
    variation_key: str | None = None
    canonical_url: str | None = None


def normalize_hostname(hostname: str) -> str:
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return ""


def hostname_from_url(url: str) -> str | None:
    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        return None
    return normalize_hostname(hostname) if hostname else None


def store_for_host(hostname: str) -> Store | None:
    normalized = normalize_hostname(hostname)
    return next((store for store, hosts in STORE_HOSTS.items() if normalized in hosts), None)


def is_shortener_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = normalize_hostname(parts.hostname) if parts.hostname else None
    return bool(
        host in SHORTENER_HOSTS
        or (host in STORE_HOSTS[Store.MERCADOLIVRE] and parts.path.casefold().startswith("/sec/"))
    )


def is_allowed_network_url(url: str) -> bool:
    host = hostname_from_url(url)
    return host in ALLOWED_NETWORK_HOSTS if host else False


def canonicalize_store_url(url: str) -> CanonicalUrlResult:
    try:
        parts = urlsplit(url)
    except ValueError:
        return CanonicalUrlResult(RelayLinkState.REJECTED, "INVALID_URL")
    host = normalize_hostname(parts.hostname) if parts.hostname else ""
    store = store_for_host(host) if host else None
    if store is None:
        if host in SHORTENER_HOSTS:
            return CanonicalUrlResult(RelayLinkState.MANUAL_REVIEW, "UNEXPANDED_SHORT_URL")
        return CanonicalUrlResult(RelayLinkState.IGNORED, "UNSUPPORTED_DOMAIN")

    match: re.Match[str] | None
    canonical_url: str
    external_id: str
    if store is Store.AMAZON:
        match = AMAZON_PRODUCT.search(parts.path)
        if not match:
            return _unrecognized(store)
        external_id = match.group(1).upper()
        canonical_url = f"https://www.amazon.com.br/dp/{external_id}"
    elif store is Store.MERCADOLIVRE:
        match = MERCADOLIVRE_PRODUCT.search(parts.path)
        if not match:
            return _unrecognized(store)
        external_id = f"MLB{match.group(1)}"
        canonical_url = f"https://produto.mercadolivre.com.br/MLB-{match.group(1)}"
    elif store is Store.SHOPEE:
        match = SHOPEE_PRODUCT.search(parts.path) or SHOPEE_I_PRODUCT.search(parts.path)
        if not match:
            return _unrecognized(store)
        shop_id, item_id = match.groups()
        external_id = f"{shop_id}:{item_id}"
        canonical_url = f"https://shopee.com.br/product/{shop_id}/{item_id}"
    elif store is Store.ALIEXPRESS:
        match = ALIEXPRESS_PRODUCT.search(parts.path)
        if not match:
            return _unrecognized(store)
        external_id = match.group(1)
        canonical_url = f"https://www.aliexpress.com/item/{external_id}.html"
    else:
        match = KABUM_PRODUCT.search(parts.path)
        if not match:
            return _unrecognized(store)
        external_id = match.group(1)
        canonical_url = f"https://www.kabum.com.br/produto/{external_id}"

    query: dict[str, list[str]] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.setdefault(key.casefold(), []).append(value)
    if any(not _is_known_query_key(store, key) for key in query):
        return CanonicalUrlResult(
            RelayLinkState.MANUAL_REVIEW, "QUERY_SEMANTICS_UNKNOWN", store=store
        )
    variation_key, ambiguous = _variation_key(store, query)
    if ambiguous:
        return CanonicalUrlResult(RelayLinkState.MANUAL_REVIEW, "VARIATION_AMBIGUOUS", store=store)

    if variation_key:
        key, value = variation_key.split(":", 1)
        canonical_url = urlunsplit((*urlsplit(canonical_url)[:3], urlencode({key: value}), ""))
    return CanonicalUrlResult(
        state=RelayLinkState.PENDING_AFFILIATE,
        reason_code="AFFILIATE_PROVIDER_UNAVAILABLE",
        store=store,
        external_product_id=external_id,
        variation_key=variation_key or "",
        canonical_url=canonical_url,
    )


def _variation_key(store: Store, query: dict[str, list[str]]) -> tuple[str, bool]:
    explicit_keys: tuple[str, ...]
    if store is Store.SHOPEE:
        explicit_keys = ("variationid",)
    elif store is Store.ALIEXPRESS:
        explicit_keys = ("sku_id", "skuid")
    else:
        explicit_keys = ()
    if any(key in query for key in AMBIGUOUS_VARIATION_KEYS):
        return "", True
    found = [(key, query[key]) for key in explicit_keys if query.get(key)]
    if len(found) == 1 and len(found[0][1]) == 1 and found[0][1][0].isdigit():
        return f"{found[0][0]}:{found[0][1][0]}", False
    if found:
        return "", True
    return "", False


def _is_known_query_key(store: Store, key: str) -> bool:
    explicit_for_store = (store is Store.SHOPEE and key == "variationid") or (
        store is Store.ALIEXPRESS and key in {"sku_id", "skuid"}
    )
    return bool(
        key in AMBIGUOUS_VARIATION_KEYS
        or explicit_for_store
        or key in REMOVABLE_QUERY_KEYS
        or key.startswith(REMOVABLE_QUERY_PREFIXES)
    )


def _unrecognized(store: Store) -> CanonicalUrlResult:
    return CanonicalUrlResult(RelayLinkState.MANUAL_REVIEW, "PRODUCT_ID_NOT_FOUND", store=store)
