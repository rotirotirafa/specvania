from concurrent.futures import ThreadPoolExecutor

import pytest

from specvania.clock import FakeClock
from specvania.provider.ratelimit import RateLimiter


def test_100_queued_calls_obey_every_60_second_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rpm=40, clock=clock)
    times: list[float] = []
    start = clock.now()
    for _ in range(100):
        limiter.acquire()
        times.append((clock.now() - start).total_seconds())
    assert times[-1] >= 148.5
    for begin in times:
        assert sum(begin <= value < begin + 60 for value in times) <= 40


def test_idle_bucket_cannot_burst_and_exposes_stamina() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rpm=40, clock=clock)
    assert limiter.available() == 1
    limiter.acquire()
    assert limiter.available() == 0
    clock.sleep(0.75)
    assert limiter.available() == pytest.approx(0.5)
    clock.sleep(300)
    assert limiter.available() == 1
    limiter.acquire()
    start = clock.now()
    limiter.acquire()
    assert (clock.now() - start).total_seconds() >= 1.5


def test_concurrent_acquisitions_share_the_bucket() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rpm=40, clock=clock)
    start = clock.now()
    with ThreadPoolExecutor(max_workers=3) as workers:
        list(workers.map(lambda _: limiter.acquire(), range(100)))
    assert (clock.now() - start).total_seconds() >= 148.5


@pytest.mark.parametrize("rpm", [0, -1])
def test_invalid_rpm_is_rejected(rpm: int) -> None:
    with pytest.raises(ValueError):
        RateLimiter(rpm=rpm, clock=FakeClock())
