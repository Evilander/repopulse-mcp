"""Rate limiting / throttling primitives."""

import time
from dataclasses import dataclass

RATE_LIMITS = {
    "global": 1000,
    "per_user": 60,
    "burst": 10,
}


@dataclass
class TokenBucket:
    """Simple token bucket used by throttle()."""

    capacity: int
    refill_per_second: float
    tokens: float
    last_refill: float

    def allow(self, cost: int = 1) -> bool:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.refill_per_second,
        )
        self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


def throttle(user_id: str, cost: int = 1) -> bool:
    """Return True if this request is allowed for `user_id`, False to rate-limit."""
    bucket = _buckets.setdefault(
        user_id,
        TokenBucket(
            capacity=RATE_LIMITS["burst"],
            refill_per_second=RATE_LIMITS["per_user"] / 60.0,
            tokens=float(RATE_LIMITS["burst"]),
            last_refill=time.time(),
        ),
    )
    return bucket.allow(cost)


_buckets: dict[str, TokenBucket] = {}
