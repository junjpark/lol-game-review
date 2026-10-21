"""Rate limiter tests — fully offline, using a fake clock.

Wall-clock tests of a 120-second window would take two minutes to run. A virtual clock
makes the same assertions instant and deterministic.
"""

from __future__ import annotations

from lolreview.riot.ratelimit import RateLimiter


class FakeClock:
    """Virtual time: sleeping advances the clock instead of blocking."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make(limits):
    clock = FakeClock()
    return RateLimiter(limits, clock=clock.time, sleep=clock.sleep), clock


def test_under_limit_never_sleeps():
    rl, clock = make(((10, 1.0),))
    for _ in range(10):
        rl.acquire()
    assert clock.slept == []


def test_sleeps_when_window_is_full():
    rl, clock = make(((5, 1.0),))
    for _ in range(5):
        rl.acquire()
    assert clock.slept == []

    rl.acquire()  # sixth request must wait for the window to roll
    assert len(clock.slept) == 1
    assert 0.99 <= clock.slept[0] <= 1.05


def test_window_slides_rather_than_resetting():
    rl, clock = make(((3, 10.0),))
    for _ in range(3):
        rl.acquire()

    clock.now += 10.1  # all three age out
    rl.acquire()
    assert clock.slept == []


def test_binding_limit_is_the_tighter_one():
    """20/s and 100/120s together: the 100/120s ceiling should dominate."""
    rl, clock = make(((20, 1.0), (100, 120.0)))
    start = clock.now
    for _ in range(101):
        rl.acquire()
    elapsed = clock.now - start
    # 101 requests cannot fit in under two minutes under a 100/120s limit.
    assert elapsed >= 100, f"expected the 2-minute window to bind, got {elapsed:.1f}s"


def test_penalize_blocks_for_retry_after():
    rl, clock = make(((100, 120.0),))
    rl.penalize(30.0)
    start = clock.now
    rl.acquire()
    assert clock.now - start >= 29.9


def test_throughput_converges_on_riot_ceiling():
    """Steady-state throughput should approach 100 requests / 120s = 3,000/hr.

    Measured over a short run the rate looks higher, because the first 100 requests
    fill an empty window and cost nothing. That burst-then-stall shape is correct —
    it's how a rolling window behaves — but it means throughput only converges on the
    ceiling over a long run. 3,000 req/hr is ~1,500 matches/hr at 2 requests each,
    which is the figure the collection plan budgets against.
    """
    rl, clock = make(((20, 1.0), (100, 120.0)))
    n = 5000
    start = clock.now
    for _ in range(n):
        rl.acquire()
    per_hour = n / ((clock.now - start) / 3600)
    assert 2900 <= per_hour <= 3150, per_hour


def test_first_window_is_free():
    """The initial burst should not sleep — documents the behaviour above."""
    rl, clock = make(((20, 1.0), (100, 120.0)))
    for _ in range(100):
        rl.acquire()
    # Only the 20/s limit should have caused any waiting, never the 2-minute one.
    assert sum(clock.slept) < 10, clock.slept
