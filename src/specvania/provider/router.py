"""Role model chains, bounded retries and summary-only provider events."""

import math
from collections.abc import Callable, Sequence
from email.utils import parsedate_to_datetime
from functools import partial
from random import Random
from threading import RLock

from specvania.clock import Clock
from specvania.config import Config
from specvania.provider.client import ChatMessage, Completion, Provider, ProviderError
from specvania.provider.ratelimit import RateLimiter
from specvania.run.events import Cooldown, Event, Fallback, ModelCall, Role


class ChainExhaustedError(ProviderError):
    """The engine can turn this failure into run_paused."""

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__()
        self.args = (f"{role.value} model chain exhausted; retry or change the chain.",)


class Router:
    """Own one router per key/run; every role uses its single shared limiter.

    emit accepts seq=0 events: the caller appends them to the ledger, then
    publishes the assigned events to the bus. Room resets leave stamina intact.
    Multiple routers for the same key must receive the same limiter instance.
    Calls are serialized because domain orchestration is synchronous.
    """

    def __init__(
        self,
        *,
        config: Config,
        provider: Provider,
        clock: Clock,
        run_id: str,
        emit: Callable[[Event], None],
        jitter: Callable[[], float] | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.provider = provider
        self._clock = clock
        self._run_id = run_id
        self._emit = emit
        self._chains = {role: tuple(getattr(config.roles, role.value)) for role in Role}
        self._limit = config.limits.failures_before_fallback
        self.limiter = limiter or RateLimiter(rpm=config.limits.rpm, clock=clock)
        self._jitter = jitter if jitter is not None else Random().random
        self._index = dict.fromkeys(Role, 0)
        self._failures = dict.fromkeys(Role, 0)
        self._room_id: str | None = None
        self._lock = RLock()

    def start_room(self, room_id: str) -> None:
        """A different room restores each role's full model chain."""
        with self._lock:
            if room_id != self._room_id:
                self._index = dict.fromkeys(Role, 0)
                self._failures = dict.fromkeys(Role, 0)
                self._room_id = room_id

    def _cooldown(self, role: Role, model: str, seconds: float, reason: str) -> None:
        self._emit(
            Cooldown(
                ts=self._clock.now(),
                run_id=self._run_id,
                seq=0,
                role=role,
                model=model,
                seconds=seconds,
                reason=reason,
            )
        )

    def _limiter_wait(self, role: Role, model: str, seconds: float) -> None:
        self._cooldown(role, model, seconds, "rate limit")

    def _retry_after(self, value: str | None) -> float:
        if value is None:
            return 0
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                seconds = (date - self._clock.now()).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 0
        return max(0, seconds) if math.isfinite(seconds) else 0

    def complete(self, role: Role, messages: Sequence[ChatMessage]) -> Completion:
        with self._lock:
            chain = self._chains[role]
            while self._index[role] < len(chain):
                model = chain[self._index[role]]
                self.limiter.acquire(partial(self._limiter_wait, role, model))
                started = self._clock.now()
                failure: ProviderError | None = None
                reply: Completion | None = None
                try:
                    reply = self.provider.complete(model, messages)
                except ProviderError as error:
                    failure = error
                self._emit(
                    ModelCall(
                        ts=self._clock.now(),
                        run_id=self._run_id,
                        seq=0,
                        role=role,
                        model=model,
                        tokens_in=reply.tokens_in if reply else 0,
                        tokens_out=reply.tokens_out if reply else 0,
                        latency=max(0, (self._clock.now() - started).total_seconds()),
                        status=reply.status
                        if reply
                        else (
                            failure.status
                            if failure and failure.status is not None
                            else 599
                        ),
                    )
                )
                if reply is not None:
                    self._failures[role] = 0
                    return reply
                assert failure is not None
                if not failure.retryable:
                    raise failure
                self._failures[role] += 1
                count = self._failures[role]
                if count >= self._limit:
                    self._index[role] += 1
                    self._failures[role] = 0
                    if self._index[role] >= len(chain):
                        raise ChainExhaustedError(role) from None
                    self._emit(
                        Fallback(
                            ts=self._clock.now(),
                            run_id=self._run_id,
                            seq=0,
                            role=role,
                            from_model=model,
                            to_model=chain[self._index[role]],
                            reason=f"{count} consecutive provider failures",
                        )
                    )
                # Retry-After applies to the key even when moving to another model.
                seconds = max(
                    2.0 ** (count - 1) + self._jitter(),
                    self._retry_after(failure.retry_after),
                )
                self._cooldown(
                    role, chain[self._index[role]], seconds, "provider backoff"
                )
                self._clock.sleep(seconds)
            raise ChainExhaustedError(role)
