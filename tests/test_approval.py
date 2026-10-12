import os
from collections.abc import Callable
from pathlib import Path

import pytest

from specvania.agent.approval import (
    ApprovalBroker,
    ApprovalError,
    ApprovalRequest,
    ApprovalResolution,
    ScriptedResponder,
    TerminalResponder,
)
from specvania.clock import FakeClock
from specvania.run.events import ApprovalAnswered, ApprovalRequested, Event


class RecordingResponder(ScriptedResponder):
    def __init__(self, name: str, answer: bool, polls: int = 0) -> None:
        super().__init__(name, [(answer, polls)])
        self.resolutions: list[ApprovalResolution] = []
        self.requests: list[ApprovalRequest] = []

    def start(self, request: ApprovalRequest, answer: Callable[[bool], None]) -> None:
        self.requests.append(request)
        super().start(request, answer)

    def cancel(self, request_id: str, resolution: ApprovalResolution | None) -> None:
        if resolution is not None:
            self.resolutions.append(resolution)
        super().cancel(request_id, resolution)


@pytest.mark.parametrize("answer", [True, False])
@pytest.mark.parametrize(
    "winner, loser", [("desktop window", "terminal"), ("terminal", "desktop window")]
)
def test_first_answer_wins_and_loser_is_cancelled(
    tmp_path: Path, answer: bool, winner: str, loser: str
) -> None:
    clock = FakeClock()
    events: list[Event] = []
    slow = RecordingResponder(loser, not answer, polls=3)
    fast = RecordingResponder(winner, answer)
    broker = ApprovalBroker([slow, fast], clock=clock, emit=events.append, run_id="run")
    result = broker.request("pytest -q", tmp_path)
    assert result.answer is answer
    assert result.responder == winner
    assert slow.resolutions == [result]
    # Polling a cancelled responder cannot override the recorded answer.
    for _ in range(5):
        slow.poll()
    assert [event.type for event in events] == [
        "approval_requested",
        "approval_answered",
    ]
    assert len(slow.requests) == len(fast.requests) == 1


def test_approval_events_identify_request_and_answering_responder(
    tmp_path: Path,
) -> None:
    events: list[Event] = []
    clock = FakeClock()
    broker = ApprovalBroker(
        [ScriptedResponder("terminal", [(False, 2)])],
        clock=clock,
        emit=events.append,
        run_id="run",
    )
    before = clock.now()
    result = broker.request("pytest -q", tmp_path)
    requested, answered = events
    assert isinstance(requested, ApprovalRequested)
    assert isinstance(answered, ApprovalAnswered)
    assert requested.command == "pytest -q"
    assert requested.cwd == str(tmp_path)
    assert requested.request_id == answered.request_id == result.request_id
    assert answered.answer is False
    assert answered.responder == "terminal"
    assert requested.run_id == answered.run_id == "run"
    assert requested.seq == answered.seq == 0
    assert requested.ts == before
    assert answered.ts > before


def test_event_summaries_do_not_truncate_actual_command(tmp_path: Path) -> None:
    events: list[Event] = []
    responder = RecordingResponder("terminal", True)
    broker = ApprovalBroker(
        [responder], clock=FakeClock(), emit=events.append, run_id="run"
    )
    command = "printf " + "x" * 1000
    broker.request(command, tmp_path)
    assert responder.requests[0].command == command
    assert isinstance(events[0], ApprovalRequested)
    assert len(events[0].command) <= 200


@pytest.mark.parametrize(
    "answer, allowed",
    [
        (b"y\n", True),
        (b"Y\n", True),
        (b"yes\n", True),
        (b"YES\n", True),
        (b"YeS\n", True),
        (b"N\n", False),
        (b"no\n", False),
        (b"maybe\n", False),
        (b"\n", False),
    ],
)
def test_terminal_prompt_uses_nonblocking_pipe(
    tmp_path: Path, answer: bytes, allowed: bool
) -> None:
    reader, writer = os.pipe()
    messages: list[str] = []
    try:
        os.write(writer, answer)
        responder = TerminalResponder(reader, messages.append)
        broker = ApprovalBroker(
            [responder], clock=FakeClock(), emit=lambda _: None, run_id="run"
        )
        result = broker.request("pytest -q", tmp_path)
        assert result.answer is allowed
        assert result.responder == "terminal"
        assert "pytest -q" in messages[0] and str(tmp_path) in messages[0]
        assert "y/N" in messages[0]
        assert os.get_blocking(reader)
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize("answer, word", [(True, "approved"), (False, "denied")])
def test_terminal_is_cancelled_without_input_when_other_responder_answers(
    tmp_path: Path, answer: bool, word: str
) -> None:
    reader, writer = os.pipe()
    messages: list[str] = []
    try:
        terminal = TerminalResponder(reader, messages.append)
        broker = ApprovalBroker(
            [terminal, ScriptedResponder("desktop window", [(answer, 0)])],
            clock=FakeClock(),
            emit=lambda _: None,
            run_id="run",
        )
        assert broker.request("pytest -q", tmp_path).answer is answer
        assert messages[-1] == f"{word} in desktop window"
        assert os.get_blocking(reader)
        terminal.poll()
    finally:
        os.close(reader)
        os.close(writer)


def test_no_responder_fails_closed(tmp_path: Path) -> None:
    broker = ApprovalBroker([], clock=FakeClock(), emit=lambda _: None, run_id="run")
    with pytest.raises(ApprovalError, match="responder"):
        broker.request("pytest -q", tmp_path)


def test_terminal_partial_input_remains_cancellable(tmp_path: Path) -> None:
    reader, writer = os.pipe()
    messages: list[str] = []
    try:
        os.set_blocking(reader, False)
        os.write(writer, b"y")  # No newline: this must not block or approve.
        terminal = TerminalResponder(reader, messages.append)
        broker = ApprovalBroker(
            [terminal, ScriptedResponder("desktop window", [(False, 2)])],
            clock=FakeClock(),
            emit=lambda _: None,
            run_id="run",
        )
        result = broker.request("pytest -q", tmp_path)
        assert result.answer is False and result.responder == "desktop window"
        assert not os.get_blocking(reader)
        assert messages[-1] == "denied in desktop window"
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize("partial", [b"", b"y", b"yes", b"Y", b"other"])
def test_terminal_eof_denies_without_waiting(tmp_path: Path, partial: bytes) -> None:
    reader, writer = os.pipe()
    os.write(writer, partial)
    os.close(writer)
    try:
        terminal = TerminalResponder(reader, lambda _: None)
        broker = ApprovalBroker(
            [terminal], clock=FakeClock(), emit=lambda _: None, run_id="run"
        )
        assert broker.request("pytest -q", tmp_path).answer is False
        assert os.get_blocking(reader)
    finally:
        os.close(reader)


def test_interrupted_approval_restores_stdin_and_cancels_responders(
    tmp_path: Path,
) -> None:
    class InterruptingResponder(RecordingResponder):
        def poll(self) -> None:
            raise KeyboardInterrupt

    reader, writer = os.pipe()
    try:
        terminal = TerminalResponder(reader, lambda _: None)
        interruption = InterruptingResponder("interrupting", True)
        events: list[Event] = []
        broker = ApprovalBroker(
            [terminal, interruption],
            clock=FakeClock(),
            emit=events.append,
            run_id="run",
        )
        with pytest.raises(KeyboardInterrupt):
            broker.request("pytest -q", tmp_path)
        assert os.get_blocking(reader)
        terminal.poll()
        ScriptedResponder.poll(interruption)
        assert [event.type for event in events] == ["approval_requested"]
    finally:
        os.close(reader)
        os.close(writer)


def test_sequential_approvals_have_unique_ids_and_no_stale_answers(
    tmp_path: Path,
) -> None:
    responder = ScriptedResponder("terminal", [(True, 0), (False, 0)])
    events: list[Event] = []
    broker = ApprovalBroker(
        [responder], clock=FakeClock(), emit=events.append, run_id="run"
    )
    first = broker.request("first", tmp_path)
    second = broker.request("second", tmp_path)
    assert first.answer is True and second.answer is False
    assert first.request_id != second.request_id
    assert [event.type for event in events] == [
        "approval_requested",
        "approval_answered",
        "approval_requested",
        "approval_answered",
    ]
