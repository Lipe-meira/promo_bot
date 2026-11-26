"""Rate limit of the two routes the local agent calls, the only ones reachable without a Clerk
session. It is checked before any database access.

Kept in memory on purpose: no migration and no write per request. It therefore counts per API
process (the service runs one) and starts empty after a restart.

- A token the database has accepted is limited on its own, far above one call every 3 seconds, so
  nothing strangers send can slow a working agent down.
- Everything else (pairing attempts, unknown tokens) is limited per client address, and by one
  shared ceiling that bounds how often strangers reach the database.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable

Clock = Callable[[], float]

_MAX_KEYS = 10_000


class TokenBuckets:
    """`capacity` calls at once, then one more every `refill_seconds`, per key.

    Refused calls cost nothing. The least recently seen keys are forgotten beyond `max_keys`, so
    the memory used does not grow with the number of callers.
    """

    def __init__(
        self,
        capacity: int,
        refill_seconds: float,
        clock: Clock = time.monotonic,
        max_keys: int = _MAX_KEYS,
    ) -> None:
        self._interval = refill_seconds
        self._burst = (capacity - 1) * refill_seconds
        self._clock = clock
        self._max_keys = max_keys
        # Per key, the moment at which the bucket would be full again.
        self._full_at: OrderedDict[str, float] = OrderedDict()

    def __len__(self) -> int:
        return len(self._full_at)

    def take(self, key: str) -> int | None:
        """None if the call is allowed; otherwise the whole seconds to wait."""

        now = self._clock()
        full_at = max(self._full_at.pop(key, now), now)
        excess = full_at - now - self._burst
        wait = None
        if excess > 0:
            wait = max(1, math.ceil(excess))
        else:
            full_at += self._interval
        self._full_at[key] = full_at
        if len(self._full_at) > self._max_keys:
            self._full_at.popitem(last=False)
        return wait


class AgentRateLimiter:
    def __init__(self, clock: Clock = time.monotonic) -> None:
        # One agent calls 20 times a minute, plus once after each command it runs.
        self._agent = TokenBuckets(capacity=30, refill_seconds=1, clock=clock)
        self._pairing = TokenBuckets(capacity=5, refill_seconds=12, clock=clock)
        self._stranger = TokenBuckets(capacity=10, refill_seconds=6, clock=clock)
        # Asked only after the address was allowed: one noisy address cannot use it up.
        self._everyone = TokenBuckets(capacity=60, refill_seconds=1, clock=clock)
        self._trusted: OrderedDict[str, None] = OrderedDict()

    def check_pair(self, address: str) -> int | None:
        return self._pairing.take(address) or self._everyone.take("")

    def check_sync(self, token_digest: str | None, address: str) -> int | None:
        if token_digest is not None and token_digest in self._trusted:
            return self._agent.take(token_digest)
        return self._stranger.take(address) or self._everyone.take("")

    def trust(self, token_digest: str) -> None:
        """The database accepted this token."""

        self._trusted[token_digest] = None
        self._trusted.move_to_end(token_digest)
        if len(self._trusted) > _MAX_KEYS:
            self._trusted.popitem(last=False)

    def distrust(self, token_digest: str) -> None:
        self._trusted.pop(token_digest, None)


def client_address(peer: str | None, forwarded_for: list[str], trusted_hops: int) -> str:
    """The address to count against.

    `X-Forwarded-For` is read here only when proxies are declared: each one appends the address it
    saw, so the entry `trusted_hops` from the end is the last one a client cannot have invented.
    `peer` is trustworthy only if the server did not already replace it from that same header
    (Uvicorn does unless started with `--no-proxy-headers`).
    """

    if trusted_hops > 0:
        chain = [part.strip() for value in forwarded_for for part in value.split(",")]
        chain = [part for part in chain if part]
        if len(chain) >= trusted_hops:
            return chain[-trusted_hops][:64]
    return peer or "unknown"
