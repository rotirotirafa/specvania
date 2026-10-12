"""All wall-clock access and waits live behind this synchronous port."""

import math
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


def _validate_wait(seconds: float) -> None:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Clock waits must be finite and non-negative")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        _validate_wait(seconds)
        time.sleep(seconds)


class FakeClock:
    """A deterministic clock; sleeping advances virtual time immediately."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start if start is not None else datetime(2000, 1, 1, tzinfo=UTC)
        if self._now.utcoffset() is None:
            raise ValueError("Clock timestamps must include a timezone")
        self._lock = Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        _validate_wait(seconds)
        with self._lock:
            self._now += timedelta(seconds=seconds)
