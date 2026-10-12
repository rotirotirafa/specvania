"""Guarded JSONL export and replay with recoverable final-line truncation."""

import json
import warnings
from collections.abc import Iterable

from pydantic import ValidationError

from specvania.run.events import EVENT_ADAPTER, Event, RedactionGuard


class EventStreamError(ValueError):
    """Malformed event stream; messages report line numbers, never payloads."""


class TruncatedLineWarning(UserWarning):
    """An incomplete final record was ignored during replay recovery."""


def encode_events(events: Iterable[Event], guard: RedactionGuard) -> str:
    lines: list[str] = []
    for event in events:
        guard.validate(event)
        lines.append(event.model_dump_json() + "\n")
    return "".join(lines)


def decode_events(stream: str, guard: RedactionGuard) -> tuple[Event, ...]:
    """Recover an unfinished final record, keeping complete records strict.

    An unterminated final line whose JSON cannot parse is treated as interrupted
    writing. Valid JSON without a trailing newline is accepted and still must
    validate as an event. Invalid newline-terminated records always raise.
    """
    # JSONL delimiters are LF only; Unicode line separators can occur in strings.
    lines = stream.split("\n")
    if not lines[-1]:
        lines.pop()
    events: list[Event] = []
    for number, line in enumerate(lines, 1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if number == len(lines) and not stream.endswith("\n"):
                warnings.warn(
                    f"Ignoring truncated final JSONL line {number}",
                    TruncatedLineWarning,
                    stacklevel=2,
                )
                break
            raise EventStreamError(f"Invalid JSON on line {number}") from None
        try:
            event = EVENT_ADAPTER.validate_python(payload)
        except ValidationError:
            raise EventStreamError(f"Invalid event on line {number}") from None
        guard.validate(event)
        events.append(event)
    return tuple(events)
