"""Event, clock and thread-safe bus contracts for T03."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Empty
from threading import Barrier, Lock

import pytest
from pydantic import ValidationError

from specvania.clock import Clock, FakeClock, SystemClock
from specvania.run.bus import EventBus, EventOrderError
from specvania.run.events import (
    EVENT_ADAPTER,
    Action,
    Event,
    RedactionGuard,
    SecretLeakError,
)


def event_samples() -> tuple[dict[str, object], ...]:
    """Independent, populated examples of every required event payload."""
    payloads: tuple[tuple[str, dict[str, object]], ...] = (
        ("run_started", {"feature": "invoices", "branch": "specvania/demo"}),
        (
            "world_loaded",
            {"rooms": [{"id": "T01", "area": "A1", "title": "One", "depends_on": []}]},
        ),
        (
            "clarify_question",
            {
                "question_id": "Q1",
                "question": "Format?",
                "options": ["PDF", "HTML"],
                "default": "PDF",
            },
        ),
        ("clarify_answer", {"question_id": "Q1", "answer": "PDF"}),
        ("spec_written", {"feature": "invoices", "spec_version": "abc123"}),
        ("room_entered", {"room_id": "T01"}),
        ("attempt_started", {"room_id": "T01", "attempt": 2}),
        (
            "model_call",
            {
                "role": "warrior",
                "model": "fake/model",
                "tokens_in": 13,
                "tokens_out": 21,
                "latency": 1.25,
                "status": 429,
            },
        ),
        (
            "cooldown",
            {
                "role": "warrior",
                "model": "fake/model",
                "seconds": 5,
                "reason": "rate limited",
            },
        ),
        (
            "fallback",
            {
                "role": "mage",
                "from_model": "fake/one",
                "to_model": "fake/two",
                "reason": "unavailable",
            },
        ),
        (
            "action",
            {
                "room_id": "T01",
                "thought": "Check the file.",
                "action": "read_file",
                "args_summary": "path=hello.py",
            },
        ),
        (
            "tool_refused",
            {"room_id": "T01", "tool": "read_file", "reason": "outside repository"},
        ),
        (
            "approval_requested",
            {"request_id": "req1", "command": "pytest -q", "cwd": "/repo"},
        ),
        (
            "approval_answered",
            {"request_id": "req1", "answer": False, "responder": "terminal"},
        ),
        (
            "gate_result",
            {
                "room_id": "T01",
                "command": "pytest -q",
                "exit_code": 1,
                "output_summary": "one failure",
            },
        ),
        (
            "review",
            {
                "room_id": "T01",
                "verdict": "fail",
                "checklist": [{"item": "Works", "ok": False, "note": "Missing"}],
                "feedback": "Add evidence",
            },
        ),
        ("respawn", {"room_id": "T01", "attempt": 2, "feedback": "Add evidence"}),
        ("room_cleared", {"room_id": "T01", "commit": "abcd"}),
        ("room_failed", {"room_id": "T01", "reason": "review failed"}),
        ("room_skipped", {"room_id": "T01", "reason": "player skipped"}),
        ("boss_started", {"area_id": "A1", "scenarios": ["S1", "S2"]}),
        (
            "boss_scenario",
            {"area_id": "A1", "scenario": "S1", "passed": True, "reason": "Verified"},
        ),
        ("boss_result", {"area_id": "A1", "passed": False}),
        (
            "bug_opened",
            {
                "bug_id": "bug1",
                "title": "Missing evidence",
                "body": "Review failed",
                "feature": "invoices",
                "room_id": "T01",
                "source": "room_skipped",
            },
        ),
        ("bug_closed", {"bug_id": "bug1", "status": "fixed", "fixed_by_run": "demo"}),
        ("run_paused", {"reason": "out of lives", "room_id": "T01"}),
        ("run_finished", {"status": "aborted"}),
    )
    return tuple(
        {
            "type": kind,
            "ts": "2026-10-12T12:00:00Z",
            "run_id": "demo",
            "seq": index,
            **payload,
        }
        for index, (kind, payload) in enumerate(payloads, 1)
    )


@pytest.mark.parametrize("payload", event_samples(), ids=lambda p: str(p["type"]))
def test_every_event_type_round_trips_without_loss(payload: dict[str, object]) -> None:
    event = EVENT_ADAPTER.validate_python(payload)
    serialized = event.model_dump_json()
    assert json.loads(serialized) == (
        {**payload, "from_bug_id": None}
        if payload["type"] == "spec_written"
        else payload
    )
    assert EVENT_ADAPTER.validate_json(serialized) == event
    with pytest.raises(ValidationError, match="frozen"):
        event.seq = 999


def test_nested_event_payloads_are_immutable() -> None:
    event = EVENT_ADAPTER.validate_python(event_samples()[15])
    assert event.type == "review"
    assert isinstance(event.checklist, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        event.checklist[0].ok = True


@pytest.mark.parametrize(
    "mutation",
    [{"type": "unknown"}, {"seq": -1}, {"ts": "not a date"}, {"prompt": "full prompt"}],
)
def test_event_boundary_rejects_invalid_envelopes(mutation: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python({**event_samples()[0], **mutation})


@pytest.mark.parametrize("field,limit", [("thought", 500), ("args_summary", 200)])
def test_event_text_is_bounded(field: str, limit: int) -> None:
    payload = {**event_samples()[10], field: "x" * limit}
    EVENT_ADAPTER.validate_python(payload)
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python({**payload, field: "x" * (limit + 1)})


@pytest.mark.parametrize("boundary", ["model", "adapter_python", "adapter_json"])
def test_event_validation_errors_hide_secret_input_values(boundary: str) -> None:
    secret = "nvapi-SECRET"
    payload = {
        **event_samples()[10],
        "args_summary": secret + "x" * (360 - len(secret)),
    }
    with pytest.raises(ValidationError) as error:
        if boundary == "model":
            Action.model_validate(payload)
        elif boundary == "adapter_python":
            EVENT_ADAPTER.validate_python(payload)
        else:
            EVENT_ADAPTER.validate_json(json.dumps(payload))
    message = str(error.value)
    assert "args_summary" in message
    assert secret not in message


@pytest.mark.parametrize(
    "secret", ["fake-api-key", "fake-view-token", "fake-control-token", 'fake"\\\nclé']
)
@pytest.mark.parametrize("location", ["run_id", "feedback", "item"])
def test_redaction_guard_rejects_registered_secrets(secret: str, location: str) -> None:
    payload = event_samples()[15]
    if location == "item":
        payload["checklist"] = [
            {"item": f"prefix {secret} suffix", "ok": True, "note": "fine"}
        ]
    else:
        payload[location] = f"prefix {secret} suffix"
    guard = RedactionGuard()
    guard.register(secret)
    with pytest.raises(SecretLeakError) as error:
        guard.validate(EVENT_ADAPTER.validate_python(payload))
    assert secret not in str(error.value)
    guard.validate(EVENT_ADAPTER.validate_python(event_samples()[0]))


def test_guards_are_isolated_and_reject_empty_registration() -> None:
    first, second = RedactionGuard(), RedactionGuard()
    first.register("invoices")
    event = EVENT_ADAPTER.validate_python(event_samples()[0])
    with pytest.raises(SecretLeakError):
        first.validate(event)
    second.validate(event)
    with pytest.raises(ValueError):
        second.register("")


def test_fake_clock_advances_without_wall_clock_waits() -> None:
    start = datetime(2026, 10, 12, tzinfo=UTC)
    clock: Clock = FakeClock(start)
    clock.sleep(5.25)
    assert clock.now() == start + timedelta(seconds=5.25)
    clock.sleep(0)
    assert clock.now() == start + timedelta(seconds=5.25)
    assert FakeClock(start).now() == start


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
def test_clocks_reject_invalid_waits(seconds: float) -> None:
    for clock in (FakeClock(), SystemClock()):
        with pytest.raises(ValueError):
            clock.sleep(seconds)


def test_system_clock_returns_aware_utc_time() -> None:
    clock: Clock = SystemClock()
    before = clock.now()
    clock.sleep(0)
    assert clock.now() >= before
    assert before.utcoffset() == timedelta(0)


def test_bus_thread_subscribers_receive_events_in_seq_order() -> None:
    bus = EventBus(RedactionGuard())
    ready = Barrier(4)
    publisher_lock = Lock()
    next_seq = 0

    def receive() -> list[Event]:
        with bus.subscribe("demo") as subscription:
            ready.wait(timeout=5)
            return [subscription.get(timeout=5) for _ in range(200)]

    def publish() -> None:
        nonlocal next_seq
        for _ in range(100):
            # The ledger owns sequence allocation; publish already sequenced events.
            with publisher_lock:
                next_seq += 1
                bus.publish(
                    EVENT_ADAPTER.validate_python(
                        {**event_samples()[0], "seq": next_seq}
                    )
                )

    with ThreadPoolExecutor(max_workers=5) as pool:
        consumers = [pool.submit(receive) for _ in range(3)]
        ready.wait(timeout=5)
        producers = [pool.submit(publish) for _ in range(2)]
        for producer in producers:
            producer.result(timeout=5)
        results = [consumer.result(timeout=5) for consumer in consumers]
    for received in results:
        assert [event.seq for event in received] == list(range(1, 201))
    assert results[0] == results[1] == results[2]


def test_bus_filters_unsubscribes_and_rejects_out_of_order_events() -> None:
    bus = EventBus(RedactionGuard())
    event = EVENT_ADAPTER.validate_python(event_samples()[0])
    with bus.subscribe("other") as filtered, bus.subscribe() as all_runs:
        bus.publish(event)
        assert all_runs.get(timeout=0) == event
        with pytest.raises(Empty):
            filtered.get(timeout=0)
        for seq in (1, 0):
            with pytest.raises(EventOrderError):
                bus.publish(
                    EVENT_ADAPTER.validate_python({**event_samples()[0], "seq": seq})
                )
        bus.publish(
            EVENT_ADAPTER.validate_python({**event_samples()[0], "run_id": "other"})
        )
        assert filtered.get(timeout=0).run_id == "other"
        assert all_runs.get(timeout=0).run_id == "other"
    bus.publish(EVENT_ADAPTER.validate_python({**event_samples()[0], "seq": 2}))
    with pytest.raises(Empty):
        all_runs.get(timeout=0)


def test_bus_rejects_secrets_before_delivery_without_consuming_seq() -> None:
    guard = RedactionGuard()
    guard.register("invoices")
    bus = EventBus(guard)
    with bus.subscribe() as subscription:
        with pytest.raises(SecretLeakError):
            bus.publish(EVENT_ADAPTER.validate_python(event_samples()[0]))
        with pytest.raises(Empty):
            subscription.get(timeout=0)
        clean = EVENT_ADAPTER.validate_python(
            {**event_samples()[0], "feature": "clean"}
        )
        bus.publish(clean)
        assert subscription.get(timeout=0) == clean
