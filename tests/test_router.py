from collections.abc import Callable

import pytest

from specvania.clock import FakeClock
from specvania.config import Config, LimitsConfig, RolesConfig
from specvania.provider.client import ChatMessage, Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.provider.router import ChainExhaustedError, Router
from specvania.run.events import (
    Cooldown,
    Event,
    Fallback,
    ModelCall,
    RedactionGuard,
    Role,
)
from specvania.run.jsonl import decode_events, encode_events
from specvania.run.state import fold_events


def setup_router(
    script: list[Completion | ProviderError | Callable[[], Completion]],
    *,
    failures: int = 3,
    jitter: float = 0,
) -> tuple[Router, ScriptedProvider, FakeClock, list[Event]]:
    provider = ScriptedProvider(script)
    clock = FakeClock()
    events: list[Event] = []

    def emit(event: Event) -> None:
        events.append(event.model_copy(update={"seq": len(events) + 1}))

    router = Router(
        config=Config(
            roles=RolesConfig(
                mage=["first", "second"], warrior=["first"], paladin=["third"]
            ),
            limits=LimitsConfig(rpm=40, failures_before_fallback=failures),
        ),
        provider=provider,
        clock=clock,
        run_id="test-run",
        emit=emit,
        jitter=lambda: jitter,
    )
    return router, provider, clock, events


def messages() -> tuple[ChatMessage, ...]:
    return (ChatMessage(role="user", content="private prompt"),)


def test_retry_after_waits_and_emits_cooldown() -> None:
    router, provider, clock, events = setup_router(
        [ProviderError(status=429, retry_after="5"), Completion(content="done")]
    )
    start = clock.now()
    assert router.complete(Role.MAGE, messages()).content == "done"
    assert (clock.now() - start).total_seconds() >= 5
    cooldown = next(event for event in events if isinstance(event, Cooldown))
    assert cooldown.seconds >= 5
    assert cooldown.role == Role.MAGE and cooldown.model == "first"
    assert [call.model for call in provider.calls] == ["first", "first"]
    assert [event.status for event in events if isinstance(event, ModelCall)] == [
        429,
        200,
    ]


def test_three_consecutive_failures_fallback_for_rest_of_room_then_reset() -> None:
    router, provider, _, events = setup_router(
        [
            *[ProviderError(status=503) for _ in range(3)],
            Completion(content="fallback"),
            Completion(content="same room"),
            Completion(content="new room"),
        ]
    )
    router.start_room("T01")
    router.complete(Role.MAGE, messages())
    router.complete(Role.MAGE, messages())
    fallback = next(event for event in events if isinstance(event, Fallback))
    assert (fallback.role, fallback.from_model, fallback.to_model) == (
        Role.MAGE,
        "first",
        "second",
    )
    assert [call.model for call in provider.calls] == [
        "first",
        "first",
        "first",
        "second",
        "second",
    ]
    router.start_room("T02")
    router.complete(Role.MAGE, messages())
    assert provider.calls[-1].model == "first"
    assert fold_events(events).party[0].model == "first"


def test_whole_chain_raises_typed_error_and_stays_exhausted_until_new_room() -> None:
    router, provider, _, events = setup_router(
        [*[ProviderError(status=429) for _ in range(6)], Completion(content="new room")]
    )
    with pytest.raises(ChainExhaustedError) as failure:
        router.complete(Role.MAGE, messages())
    assert failure.value.role == Role.MAGE
    assert len(provider.calls) == 6
    assert len([event for event in events if isinstance(event, Fallback)]) == 1
    with pytest.raises(ChainExhaustedError):
        router.complete(Role.MAGE, messages())
    assert len(provider.calls) == 6
    router.start_room("T02")
    assert router.complete(Role.MAGE, messages()).content == "new room"


def test_model_call_metrics_roundtrip_and_fold_without_content() -> None:
    router, _, clock, events = setup_router([])

    def response() -> Completion:
        clock.sleep(0.25)
        return Completion(content="private answer", tokens_in=12, tokens_out=7)

    router.provider = ScriptedProvider([response])
    router.complete(Role.PALADIN, messages())
    event = events[1]
    assert events[0].type == "model_requested"
    assert isinstance(event, ModelCall)
    assert (event.role, event.model, event.tokens_in, event.tokens_out) == (
        Role.PALADIN,
        "third",
        12,
        7,
    )
    assert event.latency == 0.25 and event.status == 200
    encoded = encode_events(events, RedactionGuard())
    assert "private" not in encoded
    decoded = decode_events(encoded, RedactionGuard())
    assert decoded == tuple(events)
    metrics = fold_events(decoded).models[0]
    assert (metrics.calls, metrics.tokens_in, metrics.tokens_out, metrics.latency) == (
        1,
        12,
        7,
        0.25,
    )


def test_success_resets_consecutive_failure_counter() -> None:
    router, provider, _, events = setup_router(
        [
            ProviderError(status=500),
            ProviderError(status=500),
            Completion(content="ok"),
            ProviderError(status=500),
            ProviderError(status=500),
            Completion(content="ok"),
        ]
    )
    router.complete(Role.MAGE, messages())
    router.complete(Role.MAGE, messages())
    assert all(call.model == "first" for call in provider.calls)
    assert not any(isinstance(event, Fallback) for event in events)


def test_roles_share_limiter_even_across_room_reset() -> None:
    router, _, clock, events = setup_router(
        [Completion(content="ok") for _ in range(100)]
    )
    for index in range(100):
        if index == 50:
            router.start_room("T02")
        router.complete(list(Role)[index % 3], messages())
    times = [event.ts for event in events if isinstance(event, ModelCall)]
    for begin in times:
        assert sum(0 <= (value - begin).total_seconds() < 60 for value in times) <= 40
    assert (clock.now() - times[0]).total_seconds() >= 148.5
    assert any(
        isinstance(event, Cooldown) and event.reason == "rate limit" for event in events
    )


def test_backoff_is_exponential_with_injected_jitter() -> None:
    router, _, _, events = setup_router(
        [
            ProviderError(status=502),
            ProviderError(status=502),
            Completion(content="ok"),
        ],
        jitter=0.5,
    )
    router.complete(Role.MAGE, messages())
    assert [event.seconds for event in events if isinstance(event, Cooldown)] == [
        1.5,
        2.5,
    ]


@pytest.mark.parametrize("retry_after", ["invalid", "-2", "NaN", "Infinity"])
def test_invalid_retry_after_uses_backoff(retry_after: str) -> None:
    router, _, _, events = setup_router(
        [ProviderError(status=429, retry_after=retry_after), Completion(content="ok")],
        jitter=0.5,
    )
    router.complete(Role.MAGE, messages())
    assert events[0].type == "model_requested"
    assert events[2].type == "cooldown"
    assert isinstance(events[2], Cooldown) and events[2].seconds == 1.5


def test_http_date_retry_after_uses_clock() -> None:
    router, _, _, events = setup_router(
        [
            ProviderError(status=429, retry_after="Sat, 01 Jan 2000 00:00:05 GMT"),
            Completion(content="ok"),
        ]
    )
    router.complete(Role.MAGE, messages())
    assert events[0].type == "model_requested"
    assert isinstance(events[2], Cooldown) and events[2].seconds == 5


def test_transport_failure_uses_synthetic_599_without_changing_schema() -> None:
    router, _, _, events = setup_router([ProviderError(), Completion(content="ok")])
    router.complete(Role.MAGE, messages())
    assert events[0].type == "model_requested"
    call = events[1]
    assert isinstance(call, ModelCall) and call.status == 599
    assert call.tokens_in == call.tokens_out == 0
    assert decode_events(
        encode_events(events, RedactionGuard()), RedactionGuard()
    ) == tuple(events)


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_non_retryable_http_error_is_typed_and_not_retried(status: int) -> None:
    error = ProviderError(status=status)
    router, provider, clock, events = setup_router([error])
    start = clock.now()
    with pytest.raises(ProviderError) as caught:
        router.complete(Role.MAGE, messages())
    assert caught.value is error
    assert len(provider.calls) == 1
    assert [event.type for event in events] == ["model_requested", "model_call"]
    assert isinstance(events[1], ModelCall) and events[1].status == status
    assert clock.now() == start


def test_unavailable_model_falls_back_without_cooldown_or_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router, provider, clock, events = setup_router(
        [
            ProviderError(status=404, retry_after="100"),
            Completion(content="fallback"),
            Completion(content="same room"),
            Completion(content="new room"),
        ]
    )
    # Isolate provider fallback from the independently tested shared rate limiter.
    monkeypatch.setattr(router.limiter, "acquire", lambda on_wait=None: None)

    def unexpected_sleep(seconds: float) -> None:
        pytest.fail(f"404 fallback must not sleep ({seconds} seconds)")

    monkeypatch.setattr(clock, "sleep", unexpected_sleep)
    router.start_room("T01")
    assert router.complete(Role.MAGE, messages()).content == "fallback"
    assert [call.model for call in provider.calls] == ["first", "second"]
    assert [event.type for event in events] == [
        "model_requested",
        "model_call",
        "fallback",
        "model_requested",
        "model_call",
    ]
    assert isinstance(events[1], ModelCall) and events[1].status == 404
    fallback = events[2]
    assert isinstance(fallback, Fallback)
    assert (fallback.role, fallback.from_model, fallback.to_model, fallback.reason) == (
        Role.MAGE,
        "first",
        "second",
        "model unavailable (HTTP 404)",
    )
    assert fold_events(events).party[0].model == "second"
    router.start_room("T01")
    assert router.complete(Role.MAGE, messages()).content == "same room"
    router.start_room("T02")
    assert router.complete(Role.MAGE, messages()).content == "new room"
    assert [call.model for call in provider.calls] == [
        "first",
        "second",
        "second",
        "first",
    ]
    assert not any(isinstance(event, Cooldown) for event in events)


def test_unavailable_last_model_exhausts_chain_without_cooldown() -> None:
    router, provider, clock, events = setup_router([ProviderError(status=404)])
    start = clock.now()
    with pytest.raises(ChainExhaustedError) as caught:
        router.complete(Role.WARRIOR, messages())
    assert caught.value.role == Role.WARRIOR
    assert [call.model for call in provider.calls] == ["first"]
    assert [event.type for event in events] == ["model_requested", "model_call"]
    assert isinstance(events[1], ModelCall) and events[1].status == 404
    assert clock.now() == start
    with pytest.raises(ChainExhaustedError):
        router.complete(Role.WARRIOR, messages())
    assert len(provider.calls) == 1


def test_unavailable_model_resets_failure_count_before_next_model_backoff() -> None:
    router, provider, _, events = setup_router(
        [
            ProviderError(status=503),
            ProviderError(status=503),
            ProviderError(status=404),
            ProviderError(status=503),
            ProviderError(status=503),
            ProviderError(status=503),
        ]
    )
    with pytest.raises(ChainExhaustedError):
        router.complete(Role.MAGE, messages())
    assert [call.model for call in provider.calls] == [
        "first",
        "first",
        "first",
        "second",
        "second",
        "second",
    ]
    assert [event.status for event in events if isinstance(event, ModelCall)] == [
        503,
        503,
        404,
        503,
        503,
        503,
    ]
    assert [
        event.seconds
        for event in events
        if isinstance(event, Cooldown) and event.reason == "provider backoff"
    ] == [1.0, 2.0, 1.0, 2.0]
    fallbacks = [event for event in events if isinstance(event, Fallback)]
    assert len(fallbacks) == 1
    assert fallbacks[0].reason == "model unavailable (HTTP 404)"


def test_configured_failure_threshold_and_independent_role_chains() -> None:
    router, provider, _, events = setup_router(
        [
            ProviderError(status=429),
            ProviderError(status=500),
            Completion(content="mage fallback"),
            Completion(content="warrior original"),
            Completion(content="mage still fallback"),
        ],
        failures=2,
    )
    router.start_room("T01")
    router.complete(Role.MAGE, messages())
    router.complete(Role.WARRIOR, messages())
    router.start_room("T01")
    router.complete(Role.MAGE, messages())
    assert [call.model for call in provider.calls] == [
        "first",
        "first",
        "second",
        "first",
        "second",
    ]
    assert len([event for event in events if isinstance(event, Fallback)]) == 1


def test_retry_after_is_honored_when_switching_models() -> None:
    router, provider, clock, events = setup_router(
        [ProviderError(status=429, retry_after="5"), Completion(content="fallback")],
        failures=1,
    )
    start = clock.now()
    router.complete(Role.MAGE, messages())
    assert (clock.now() - start).total_seconds() >= 5
    assert [call.model for call in provider.calls] == ["first", "second"]
    assert any(isinstance(event, Cooldown) and event.seconds >= 5 for event in events)


def test_cooldown_after_fallback_keeps_new_model_in_run_state() -> None:
    router, _, _, events = setup_router(
        [ProviderError(status=503), Completion(content="ok")], failures=1
    )
    router.complete(Role.MAGE, messages())
    fallback_index = next(
        index for index, event in enumerate(events) if isinstance(event, Fallback)
    )
    for end in range(fallback_index + 1, len(events) + 1):
        assert fold_events(events[:end]).party[0].model == "second"


def test_backoff_stays_exponential_with_larger_configured_threshold() -> None:
    router, _, _, events = setup_router(
        [*[ProviderError(status=503) for _ in range(8)], Completion(content="ok")],
        failures=9,
        jitter=0.5,
    )
    router.complete(Role.MAGE, messages())
    assert [
        event.seconds
        for event in events
        if isinstance(event, Cooldown) and event.reason == "provider backoff"
    ] == [1.5, 2.5, 4.5, 8.5, 16.5, 32.5, 64.5, 128.5]


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("room_id", [None, "T01"])
def test_model_requested_is_visible_before_provider_attempt(
    role: Role, room_id: str | None
) -> None:
    router, _, clock, events = setup_router([])
    if room_id is not None:
        router.start_room(room_id)

    def response() -> Completion:
        assert [event.type for event in events] == ["model_requested"]
        request = events[0].model_dump(mode="json")
        assert request["role"] == role.value
        assert request["model"] == ("third" if role == Role.PALADIN else "first")
        assert request.get("room_id") == room_id
        assert ("room_id" in request) == (room_id is not None)
        clock.sleep(284)
        return Completion(content="private answer")

    router.provider = ScriptedProvider([response])
    router.complete(role, messages())
    assert [event.type for event in events] == ["model_requested", "model_call"]
    assert isinstance(events[-1], ModelCall) and events[-1].latency == 284


@pytest.mark.parametrize("status", [429, 503, 404])
def test_model_requested_pairs_every_retry_and_fallback_attempt(status: int) -> None:
    router, provider, _, events = setup_router(
        [
            *[ProviderError(status=status) for _ in range(3 if status != 404 else 1)],
            Completion(content="ok"),
        ]
    )
    router.start_room("T01")
    router.complete(Role.MAGE, messages())
    attempts = [
        event for event in events if event.type in ("model_requested", "model_call")
    ]
    assert len(attempts) == 2 * len(provider.calls)
    for request, call, provider_call in zip(
        attempts[::2], attempts[1::2], provider.calls, strict=True
    ):
        assert request.type == "model_requested" and call.type == "model_call"
        assert request.role == call.role == Role.MAGE
        assert request.model == call.model == provider_call.model
        assert request.model_dump()["room_id"] == "T01"
    for index, event in enumerate(events):
        if event.type == "model_call":
            assert events[index - 1].type == "model_requested"
        if event.type == "cooldown":
            # Provider backoff and limiter waits can be consecutive cooldowns.
            prefix = events[:index]
            assert sum(e.type == "model_requested" for e in prefix) == sum(
                e.type == "model_call" for e in prefix
            )
            assert any(e.type == "model_requested" for e in events[index + 1 :])


def test_no_model_requested_when_chain_exhausted_or_messages_refused() -> None:
    router, provider, _, events = setup_router([ProviderError(status=404)])
    with pytest.raises(ChainExhaustedError):
        router.complete(Role.WARRIOR, messages())
    before = tuple(events)
    with pytest.raises(ChainExhaustedError):
        router.complete(Role.WARRIOR, messages())
    assert tuple(events) == before
    router.start_room("T02")

    def refused() -> tuple[ChatMessage, ...]:
        raise RuntimeError("request refused")

    with pytest.raises(RuntimeError, match="request refused"):
        router.complete(Role.WARRIOR, refused)
    assert not any(event.type == "model_requested" for event in events[len(before) :])
    assert len(provider.calls) == 1
