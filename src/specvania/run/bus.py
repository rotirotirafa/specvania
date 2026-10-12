"""Thread-safe queue subscriptions to already sequenced, guarded events."""

from __future__ import annotations

from queue import Queue
from threading import RLock
from types import TracebackType

from specvania.run.events import Event, RedactionGuard


class EventOrderError(ValueError):
    """A publisher must deliver events in increasing sequence order per run."""


class Subscription:
    def __init__(self, bus: EventBus, run_id: str | None) -> None:
        self._bus = bus
        self.run_id = run_id
        self._queue: Queue[Event] = Queue()

    def get(self, timeout: float | None = None) -> Event:
        """Wait for the next event; queue.Empty signals a timed-out wait."""
        return self._queue.get(timeout=timeout)

    def close(self) -> None:
        self._bus.unsubscribe(self)

    def __enter__(self) -> Subscription:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class EventBus:
    """No callbacks run under the bus lock; consumers own their worker threads.

    Subscribe/publish/unsubscribe are linearized by one lock. Queues are unbounded
    so a slow subscriber cannot drop events or block another subscriber. The
    caller must serialize ledger append and publish for each run; publishing an
    older sequence is an error, never a silently reordered delivery.
    """

    def __init__(self, guard: RedactionGuard) -> None:
        self._guard = guard
        self._lock = RLock()
        self._subscribers: set[Subscription] = set()
        self._last_seq: dict[str, int] = {}

    def subscribe(self, run_id: str | None = None) -> Subscription:
        with self._lock:
            subscription = Subscription(self, run_id)
            self._subscribers.add(subscription)
            return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def publish(self, event: Event) -> None:
        with self._lock:
            self._guard.validate(event)
            if event.seq <= self._last_seq.get(event.run_id, 0):
                raise EventOrderError("Bus events must have increasing assigned seq")
            self._last_seq[event.run_id] = event.seq
            for subscription in self._subscribers:
                if subscription.run_id is None or subscription.run_id == event.run_id:
                    subscription._queue.put_nowait(event)
