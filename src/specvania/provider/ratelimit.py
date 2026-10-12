"""One thread-safe token bucket shared by all roles using a provider key."""

import math
from collections.abc import Callable
from threading import Lock

from specvania.clock import Clock


class RateLimiter:
    """A capacity-one bucket avoids bursts exceeding RPM in a rolling minute.

    Keep this instance across rooms (and share it across routers using the same
    key). Refill is capped at one token even after a long idle period. Round the
    spacing up to Clock's microsecond precision rather than exceeding the budget.
    """

    def __init__(self, *, rpm: int, clock: Clock) -> None:
        if rpm <= 0:
            raise ValueError("RPM must be positive")
        self._clock = clock
        self._interval = math.ceil(60 / rpm * 1_000_000) / 1_000_000
        self._tokens = 1.0
        self._updated = clock.now()
        self._lock = Lock()

    def _refill(self) -> None:
        now = self._clock.now()
        elapsed = max(0.0, (now - self._updated).total_seconds())
        self._tokens = min(1.0, self._tokens + elapsed / self._interval)
        self._updated = now

    def available(self) -> float:
        """Remaining stamina, from zero to one request token."""
        with self._lock:
            self._refill()
            return self._tokens

    def acquire(self, on_wait: Callable[[float], None] | None = None) -> None:
        with self._lock:
            self._refill()
            if self._tokens < 1:
                seconds = (
                    math.ceil((1 - self._tokens) * self._interval * 1_000_000)
                    / 1_000_000
                )
                if on_wait is not None:
                    on_wait(seconds)
                self._clock.sleep(seconds)
            self._tokens = 0.0
            self._updated = self._clock.now()
