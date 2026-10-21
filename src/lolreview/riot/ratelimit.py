"""Sliding-window rate limiter.

Riot development keys enforce two limits simultaneously:

    20 requests / 1 second
    100 requests / 120 seconds   <- the binding one for bulk collection

A token bucket handles one limit well but composes awkwardly for several. A sliding
window per limit is simpler to reason about and trivially testable: keep the timestamps
of recent requests, and before each new one, sleep until every window has room.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

# (max_requests, window_seconds) — Riot's documented development-key limits.
DEV_KEY_LIMITS: tuple[tuple[int, float], ...] = ((20, 1.0), (100, 120.0))


@dataclass
class _Window:
    max_requests: int
    window: float
    times: deque[float]

    def wait_time(self, now: float) -> float:
        """Seconds to sleep before another request fits in this window. 0 if it fits."""
        while self.times and now - self.times[0] >= self.window:
            self.times.popleft()
        if len(self.times) < self.max_requests:
            return 0.0
        # Oldest request must age out of the window before we get a slot.
        return self.window - (now - self.times[0])


class RateLimiter:
    """Blocks until a request is permitted under every configured window.

    Thread-safe, though the collection scripts are single-threaded by default —
    concurrency buys nothing against a 100/2min ceiling.
    """

    def __init__(
        self,
        limits: tuple[tuple[int, float], ...] = DEV_KEY_LIMITS,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self._windows = [_Window(n, w, deque()) for n, w in limits]
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a request may be made. Returns how long it slept."""
        slept = 0.0
        while True:
            with self._lock:
                now = self._clock()
                wait = max((w.wait_time(now) for w in self._windows), default=0.0)
                if wait <= 0:
                    for w in self._windows:
                        w.times.append(now)
                    return slept
            # Pad slightly so we land past the boundary rather than on it.
            self._sleep(wait + 0.01)
            slept += wait + 0.01

    def penalize(self, seconds: float) -> None:
        """Record an externally imposed wait (a 429's Retry-After).

        Fills every window so nothing is attempted until the penalty has elapsed.
        """
        with self._lock:
            now = self._clock()
            for w in self._windows:
                # Push synthetic timestamps far enough forward that wait_time()
                # returns at least `seconds`.
                target = now + seconds - w.window
                w.times.clear()
                w.times.extend([target] * w.max_requests)
