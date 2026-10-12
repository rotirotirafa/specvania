"""Portable event streams, corruption handling and redaction."""

import json

import pytest

from specvania.run.events import EVENT_ADAPTER, Event, RedactionGuard, SecretLeakError
from specvania.run.jsonl import (
    EventStreamError,
    TruncatedLineWarning,
    decode_events,
    encode_events,
)


def sample_events() -> tuple[Event, ...]:
    return tuple(
        EVENT_ADAPTER.validate_python(
            {
                "type": "run_paused",
                "ts": "2026-10-12T12:00:00Z",
                "run_id": "demo",
                "seq": seq,
                "reason": f"Pause {seq}",
                "room_id": "T01",
            }
        )
        for seq in (1, 2, 3)
    )


def test_jsonl_export_replay_preserves_events() -> None:
    guard = RedactionGuard()
    events = sample_events()
    encoded = encode_events(iter(events), guard)
    assert encoded.endswith("\n")
    assert len(encoded.splitlines()) == 3
    assert [json.loads(line)["seq"] for line in encoded.splitlines()] == [1, 2, 3]
    assert decode_events(encoded, guard) == events
    assert decode_events(encoded.rstrip("\n"), guard) == events
    assert encode_events((), guard) == ""
    assert decode_events("", guard) == ()


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\r", "\x1c"])
def test_jsonl_round_trip_preserves_text_line_separators(separator: str) -> None:
    guard = RedactionGuard()
    event = EVENT_ADAPTER.validate_python(
        {
            "type": "action",
            "ts": "2026-10-12T12:00:00Z",
            "run_id": "demo",
            "seq": 4,
            "room_id": "T01",
            "thought": f"a{separator}b",
            "action": "read_file",
            "args_summary": "path=hello.py",
        }
    )
    events = (*sample_events(), event)
    encoded = encode_events(events, guard)
    assert decode_events(encoded, guard) == events
    assert decode_events(encoded.rstrip("\n"), guard) == events


@pytest.mark.parametrize("cut", [1, 10, 40])
def test_truncated_last_jsonl_line_is_ignored_with_warning(cut: int) -> None:
    guard = RedactionGuard()
    encoded = encode_events(sample_events(), guard).rstrip("\n")
    with pytest.warns(TruncatedLineWarning, match="line 3"):
        recovered = decode_events(encoded[:-cut], guard)
    assert recovered == sample_events()[:2]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "run_paused", "reason": "Pause \x01", "room_id": None},
        {
            "type": "approval_answered",
            "request_id": "req",
            "answer": False,
            "responder": "terminal",
        },
        {
            "type": "model_call",
            "role": "mage",
            "model": "fake",
            "tokens_in": 1,
            "tokens_out": 2,
            "latency": 1.25,
            "status": 200,
        },
    ],
)
def test_last_record_recovers_at_every_truncation_boundary(
    payload: dict[str, object],
) -> None:
    guard = RedactionGuard()
    event = EVENT_ADAPTER.validate_python(
        {"ts": "2026-10-12T12:00:00Z", "run_id": "demo", "seq": 4, **payload}
    )
    prefix = encode_events(sample_events(), guard)
    record = event.model_dump_json()
    for length in range(1, len(record)):
        with pytest.warns(TruncatedLineWarning, match="line 4"):
            assert decode_events(prefix + record[:length], guard) == sample_events()


@pytest.mark.parametrize(
    "stream,line", [("{broken}\n", 1), ("{broken}\n{}", 1), ("{}", 1), ("\n", 1)]
)
def test_malformed_complete_lines_raise_typed_errors(stream: str, line: int) -> None:
    with pytest.raises(EventStreamError, match=f"line {line}"):
        decode_events(stream, RedactionGuard())


def test_jsonl_errors_never_echo_payloads() -> None:
    secret = "fake-secret"
    with pytest.raises(EventStreamError) as error:
        decode_events(f'{{"type":"unknown", "secret":"{secret}"}}\n', RedactionGuard())
    assert secret not in str(error.value)


def test_jsonl_redaction_runs_on_export_and_decode() -> None:
    events = sample_events()
    encoded = encode_events(events, RedactionGuard())
    guard = RedactionGuard()
    guard.register("Pause 2")
    with pytest.raises(SecretLeakError):
        encode_events(events, guard)
    with pytest.raises(SecretLeakError):
        decode_events(encoded, guard)
