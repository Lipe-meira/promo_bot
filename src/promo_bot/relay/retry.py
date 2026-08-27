"""Bounded retry timing for transient relay failures."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    initial_seconds: int
    maximum_seconds: int
    jitter_ratio: float = 0.2

    def delay_seconds(self, attempt_count: int, *, rng: Random | None = None) -> float:
        if attempt_count < 1:
            raise ValueError("attempt_count must be positive")
        base = min(self.maximum_seconds, self.initial_seconds * (2 ** (attempt_count - 1)))
        generator = rng or random.Random()
        jitter = generator.uniform(0, base * self.jitter_ratio)
        return float(min(float(self.maximum_seconds), base + jitter))

    def next_attempt_at(
        self, now: datetime, attempt_count: int, *, rng: Random | None = None
    ) -> datetime:
        return now + timedelta(seconds=self.delay_seconds(attempt_count, rng=rng))
