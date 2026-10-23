"""Per-key token buckets: requests per minute and text tokens per minute (tier limits).

Tokens are charged after a response (usage is only known then), so a key may overshoot by one request;
the next one waits. Over the limit -> 429 with Retry-After.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Bucket:
    capacity: float
    rate: float        # per second
    level: float
    t: float

    def refill(self, now):
        self.level = min(self.capacity, self.level + (now - self.t) * self.rate)
        self.t = now


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.buckets: dict[tuple[str, str], Bucket] = {}

    def _bucket(self, key_id, kind, per_min):
        b = self.buckets.get((key_id, kind))
        if b is None or b.capacity != per_min:
            b = Bucket(per_min, per_min / 60.0, per_min, self.clock())
            self.buckets[(key_id, kind)] = b
        return b

    def check_request(self, principal) -> float:
        """0 if allowed (and charged), else seconds to wait."""
        now = self.clock()
        t = principal.tier
        if t.tpm:
            tb = self._bucket(principal.key_id, "tpm", t.tpm)
            tb.refill(now)
            if tb.level <= 0:
                return -tb.level / tb.rate + 1
        if t.rpm:
            b = self._bucket(principal.key_id, "rpm", t.rpm)
            b.refill(now)
            if b.level < 1:
                return (1 - b.level) / b.rate
            b.level -= 1
        return 0.0

    def charge_tokens(self, principal, tokens: int):
        if principal.tier.tpm and tokens:
            b = self._bucket(principal.key_id, "tpm", principal.tier.tpm)
            b.refill(self.clock())
            b.level -= tokens
