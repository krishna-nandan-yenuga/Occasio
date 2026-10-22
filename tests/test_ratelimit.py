"""Token-bucket rate limiter with a fake clock."""

from app.api.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_allows_a_burst_then_blocks_with_retry_hint() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=3, clock=clock)
    assert [limiter.check("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.check("a") == 20.0  # one token every 20 seconds


def test_tokens_refill_over_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=3, clock=clock)
    for _ in range(3):
        limiter.check("a")
    clock.now = 20.0
    assert limiter.check("a") == 0.0
    assert limiter.check("a") > 0


def test_clients_are_limited_separately() -> None:
    limiter = RateLimiter(per_minute=1, clock=FakeClock())
    assert limiter.check("a") == 0.0
    assert limiter.check("b") == 0.0
    assert limiter.check("a") > 0


def test_idle_clients_are_pruned() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=1, clock=clock, max_clients=2)
    limiter.check("a")
    limiter.check("b")
    clock.now = 120.0
    limiter.check("c")
    assert set(limiter._buckets) == {"c"}
