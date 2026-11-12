"""Which ``AppConfig`` fields the current flow really applies (T4 / F-03).

Accepting a value in ``config.yaml`` is not the same as enforcing it. This module is the single
source of truth for that difference; ``tests/unit/test_config_policy_contract.py`` checks it against
the real code, so a field cannot be added, started or stopped being read without reclassifying it.
It never enables behavior: it only describes it and names the fields that must be rejected.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

POLICY_NOT_ENFORCED_CODE: Final = "CONFIG_POLICY_NOT_ENFORCED"


class PolicyStatus(StrEnum):
    #: Read by production code and changes behavior (gates, relay limits, destination checks).
    APPLIED = "applied"
    #: Read, but only for a narrow effect; the full documented rule is not enforced.
    PARTIAL = "partial"
    #: Validated and stored only. Reserved for a future phase; no current flow reads it.
    RESERVED = "reserved"
    #: Looks like a safety/publication filter but is not enforced, so a non-empty value is refused.
    REJECTED_WHEN_SET = "rejected_when_set"


CONFIG_POLICY_STATUS: Final[dict[str, PolicyStatus]] = {
    # Applied: relay ingestion, shadow-delivery authorization and provider gates.
    "source_channels": PolicyStatus.APPLIED,
    "telegram_shadow_delivery": PolicyStatus.APPLIED,
    "telegram_relay": PolicyStatus.APPLIED,
    "providers": PolicyStatus.APPLIED,
    # Partial: only the `run` command logs how many categories are configured.
    "categories": PolicyStatus.PARTIAL,
    # Reserved: there is no catalog pipeline that filters, scores, throttles or renders by these.
    "keywords": PolicyStatus.RESERVED,
    "minimum_discount_percent": PolicyStatus.RESERVED,
    "minimum_score": PolicyStatus.RESERVED,
    "cooldown_hours": PolicyStatus.RESERVED,
    "maximum_promotions_per_hour": PolicyStatus.RESERVED,
    "search_intervals_minutes": PolicyStatus.RESERVED,
    "provider_rate_limits_per_minute": PolicyStatus.RESERVED,
    "templates": PolicyStatus.RESERVED,
    "affiliate_disclosure": PolicyStatus.RESERVED,
    "presentation_timezone": PolicyStatus.RESERVED,
    # Rejected when set: a user who lists a blocked seller expects it to be blocked.
    "blacklist": PolicyStatus.REJECTED_WHEN_SET,
    "allowed_sellers": PolicyStatus.REJECTED_WHEN_SET,
    "blocked_sellers": PolicyStatus.REJECTED_WHEN_SET,
    "maximum_price": PolicyStatus.REJECTED_WHEN_SET,
}

UNENFORCED_BLOCKING_FIELDS: Final[tuple[str, ...]] = tuple(
    name
    for name, status in CONFIG_POLICY_STATUS.items()
    if status is PolicyStatus.REJECTED_WHEN_SET
)
