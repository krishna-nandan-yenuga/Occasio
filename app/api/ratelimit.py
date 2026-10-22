"""In-memory token-bucket rate limiter, one bucket per client key.

Single-process only: each worker keeps its own buckets, which is enough to stop one client
from running up LLM costs on a small deployment.
"""

import time
from collections.abc import Callable

SECONDS_PER_MINUTE = 60.0


class RateLimiter:
    """Allows `per_minute` requests per key, refilled continuously; bursts up to `per_minute`."""

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 10_000,
    ) -> None:
        self._capacity = float(per_minute)
        self._rate = per_minute / SECONDS_PER_MINUTE
        self._clock = clock
        self._max_clients = max_clients
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last update)

    def check(self, key: str) -> float:
        """Take a token for `key`: 0 if allowed, else seconds until the next one is available."""
        now = self._clock()
        tokens, last = self._buckets.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._rate)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            self._prune(now)
            return 0.0
        self._buckets[key] = (tokens, now)
        return (1 - tokens) / self._rate

    def _prune(self, now: float) -> None:
        """Forget clients whose buckets have refilled completely, once there are too many."""
        if len(self._buckets) <= self._max_clients:
            return
        full_after = self._capacity / self._rate
        self._buckets = {
            key: (tokens, last)
            for key, (tokens, last) in self._buckets.items()
            if now - last < full_after
        }
