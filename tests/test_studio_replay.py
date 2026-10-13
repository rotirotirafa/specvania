"""T37 offline studio playback through its real control transport."""

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError
from starlette.testclient import TestClient
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.run.events import RedactionGuard
from specvania.ui.server import (
    CloseReplay,
    ControlMessage,
    OpenReplay,
    ReplayPause,
    ReplaySpeed,
    StudioPhase,
    UIServer,
    UIServerError,
    load_recording,
    load_replay,
)
from specvania.ui.studio import Studio, StudioError

FIXTURE = Path(__file__).parent / "fixtures/replay-two-stream"
ORIGIN = "http://127.0.0.1:8765"


class PlaybackClock(FakeClock):
    def __init__(self) -> None:
        super().__init__()
        self.on_sleep: Callable[[], None] = lambda: None

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.on_sleep()


def websocket_url(server: UIServer) -> str:
    origin = server.origin.replace("http:", "ws:")
    return f"{origin}/ws?view_token={server.view_token}"


def instance(clock: FakeClock | None = None) -> Studio:
    return Studio(guard=RedactionGuard(), clock=clock or FakeClock(), port=8765)


def use_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specvania.ui.studio.load_recording",
        lambda directory, *, guard: load_recording(FIXTURE, guard=guard),
    )


def join_replay(studio: Studio) -> None:
    assert studio._replay_worker is not None
    studio._replay_worker.join(3)
    assert not studio._replay_worker.is_alive()


def test_watch_demo_no_key_project_network_provider_engine_ledger_or_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Replay constructed a provider, engine, ledger or GitOps")

    for name in ("OpenAIProvider", "Engine", "GitOps", "plan"):
        monkeypatch.setattr(f"specvania.ui.studio.{name}", forbidden)
    monkeypatch.setattr("specvania.ui.studio.Ledger.from_config", forbidden)
    studio = instance()
    server = studio.server
    expected = load_replay(
        Path(str(files("specvania").joinpath("examples/demo/events.jsonl"))),
        guard=RedactionGuard(),
    )
    with TestClient(server.app, base_url=ORIGIN) as client:
        assert (
            client.get("/", params={"view_token": server.view_token}).status_code == 200
        )
        assert client.get("/screens/replay.js").status_code == 200
        with client.websocket_connect(
            websocket_url(server),
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json()["phase"] == "idle"
            ws.send_json({"kind": "open_replay", "control_token": server.control_token})
            received = []
            while True:
                message = ws.receive_json()
                if message["kind"] == "event":
                    received.append(message["event"])
                if message.get("replay", {}).get("finished"):
                    break
            assert received == [event.model_dump(mode="json") for event in expected]
            assert studio.snapshot().replay is not None
            assert studio.snapshot().state.status == "aborted"
            ws.send_json(
                {"kind": "close_replay", "control_token": server.control_token}
            )
            while ws.receive_json().get("phase") != "idle":
                pass
    assert studio.snapshot().state.run_id is None
    assert studio.code_repository() == (None, None)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("speed", [1, 2, 4])
def test_speeds_change_recorded_pace_with_fake_clock(
    monkeypatch: pytest.MonkeyPatch, speed: Literal[1, 2, 4]
) -> None:
    use_fixture(monkeypatch)
    clock = PlaybackClock()
    studio = instance(clock)
    start = clock.now()
    changed = False

    def change() -> None:
        nonlocal changed
        if not changed:
            changed = True
            studio.control(ReplaySpeed(kind="replay_speed", speed=speed))

    clock.on_sleep = change
    studio.control(OpenReplay(kind="open_replay"))
    join_replay(studio)
    # 18 events at 2 seconds each, plus a 2-second feature introduction.
    # The first 50 ms slice ran at 1x; every remaining slice uses the new speed.
    assert (clock.now() - start).total_seconds() == pytest.approx(
        0.05 + (36.0 - 0.05) / speed, abs=0.001
    )
    replay = studio.snapshot().replay
    assert replay is not None
    assert replay.speed == speed
    studio.control(CloseReplay(kind="close_replay"))


@pytest.mark.parametrize("pause_at", [0, 2, 6])
def test_speed_changes_inside_a_gap_pause_resume_and_back_stop_playback(
    monkeypatch: pytest.MonkeyPatch,
    pause_at: int,
) -> None:
    use_fixture(monkeypatch)
    clock = PlaybackClock()
    studio = instance(clock)
    reached = ThreadEvent()
    start = clock.now()
    changed = False

    def pause() -> None:
        nonlocal changed
        if not changed and studio.snapshot().state.seq == pause_at:
            changed = True
            studio.control(ReplayPause(kind="replay_pause", paused=True))
            reached.set()

    clock.on_sleep = pause
    studio.control(OpenReplay(kind="open_replay"))
    assert reached.wait(2)
    paused_elapsed = (clock.now() - start).total_seconds()
    # Acquire the same lock as the worker; a paused Clock cannot advance.
    with studio._condition:
        assert (clock.now() - start).total_seconds() == paused_elapsed
        assert studio.snapshot().state.seq == pause_at
    studio.control(ReplaySpeed(kind="replay_speed", speed=4))
    assert (clock.now() - start).total_seconds() == paused_elapsed
    studio.control(ReplayPause(kind="replay_pause", paused=False))
    join_replay(studio)
    assert (clock.now() - start).total_seconds() == pytest.approx(
        paused_elapsed + (36.0 - paused_elapsed) / 4, abs=0.001
    )
    studio.control(CloseReplay(kind="close_replay"))

    changed = False
    reached.clear()
    studio.control(OpenReplay(kind="open_replay"))
    assert reached.wait(2)
    studio.control(CloseReplay(kind="close_replay"))
    stopped_at = clock.now()
    assert studio.phase == "idle"
    assert studio._replay_worker is None
    assert studio.snapshot().replay is None
    assert studio.snapshot().state.seq == 0
    assert clock.now() == stopped_at


def test_two_stream_events_and_recorded_answers_arrive_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_fixture(monkeypatch)
    studio = instance()
    server = studio.server
    streams = load_recording(FIXTURE, guard=RedactionGuard())
    expected = [event for _, stream in streams for event in stream]
    with TestClient(server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            websocket_url(server),
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ws.send_json({"kind": "open_replay", "control_token": server.control_token})
            messages = []
            while True:
                message = ws.receive_json()
                messages.append(message)
                if message.get("replay", {}).get("finished"):
                    break
    events = [message["event"] for message in messages if message["kind"] == "event"]
    assert events == [event.model_dump(mode="json") for event in expected]
    assert [event["type"] for event in events][1:5] == [
        "clarify_question",
        "clarify_answer",
        "spec_written",
        "run_started",
    ]
    assert events[2]["answer"] == "Green"
    assert events[9]["type"] == "approval_answered" and events[9]["answer"] is True
    assert events[10]["type"] == "run_paused"
    assert events[11]["type"] == "attempt_started"
    boundary = next(
        index
        for index, message in enumerate(messages)
        if message["kind"] == "state"
        and message.get("replay", {}).get("stream") == "run"
    )
    assert messages[boundary]["state"]["seq"] == 0
    assert studio.snapshot().state.run_id == "fixture-run"
    studio.control(CloseReplay(kind="close_replay"))


@pytest.mark.parametrize("token", [None, "wrong", "view"])
def test_open_replay_rejects_missing_wrong_and_view_control_tokens(
    token: str | None,
) -> None:
    studio = instance()
    server = studio.server
    with TestClient(server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            websocket_url(server),
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            message = {"kind": "open_replay"}
            if token is not None:
                message["control_token"] = (
                    server.view_token if token == "view" else token
                )
            ws.send_json(message)
            assert ws.receive_json()["reason"] == "control unavailable"
    assert studio.phase == "idle"
    assert studio._replay_worker is None


@pytest.mark.parametrize("phase", ["planning", "running"])
def test_open_replay_rejected_during_active_plan_or_run(phase: StudioPhase) -> None:
    studio = instance()
    studio.phase = phase
    with TestClient(studio.server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            websocket_url(studio.server),
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ws.send_json(
                {"kind": "open_replay", "control_token": studio.server.control_token}
            )
            assert ws.receive_json()["kind"] == "approval_rejected"
    assert studio.phase == phase
    assert studio._replay_worker is None


def test_replay_controls_typed_and_live_answers_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_fixture(monkeypatch)
    adapter = TypeAdapter(ControlMessage)
    for bad in (
        {"kind": "open_replay", "name": "../../secret"},
        {"kind": "replay_speed", "speed": 3},
        {"kind": "replay_speed", "speed": True},
        {"kind": "replay_speed", "speed": 1.0},
        {"kind": "replay_speed", "speed": "2"},
        {"kind": "replay_pause", "paused": "true"},
        {"kind": "close_replay", "extra": True},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
    studio = instance()
    for message in (
        ReplaySpeed(kind="replay_speed", speed=2),
        ReplayPause(kind="replay_pause", paused=True),
    ):
        with pytest.raises(StudioError):
            studio.control(message)
    studio.phase = "project_open"
    studio.control(OpenReplay(kind="open_replay"))
    join_replay(studio)
    with TestClient(studio.server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            websocket_url(studio.server),
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ws.receive_json()
            for message in (
                {
                    "kind": "approval_answer",
                    "request_id": "approval-1",
                    "answer": False,
                },
                {"kind": "clarify_answer", "question_id": "Q1", "answer": "New"},
                {"kind": "pause_decision", "choice": "abort"},
            ):
                ws.send_json({**message, "control_token": studio.server.control_token})
                assert ws.receive_json()["kind"] == "approval_rejected"
    studio.control(CloseReplay(kind="close_replay"))


def test_prefers_real_run_when_present_and_explicit_demo_remains_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    examples = package / "examples"
    for name in ("demo", "real-run"):
        target = examples / name
        target.mkdir(parents=True)
        for source in FIXTURE.iterdir():
            (target / source.name).write_bytes(source.read_bytes())
    monkeypatch.setattr("specvania.ui.studio.files", lambda _: package)
    studio = instance()
    choices: tuple[tuple[Literal["demo", "real-run"] | None, str], ...] = (
        (None, "real-run"),
        ("demo", "demo"),
    )
    for name, expected in choices:
        studio.control(OpenReplay(kind="open_replay", name=name))
        join_replay(studio)
        replay = studio.snapshot().replay
        assert replay is not None
        assert replay.name == expected
        studio.control(CloseReplay(kind="close_replay"))


def test_cli_two_stream_directory_without_key_config_or_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    served: list[UIServer] = []
    monkeypatch.setattr(UIServer, "serve", lambda self, **kwargs: served.append(self))
    monkeypatch.setattr(cli, "load_config", lambda *args: pytest.fail("config opened"))
    result = CliRunner().invoke(
        cli.create_app(), ["replay", str(FIXTURE), "--speed", "4"]
    )
    assert result.exit_code == 0, result.output
    server = served[0]
    assert server.replay == load_replay(FIXTURE, guard=RedactionGuard())
    assert server.speed == 4
    server.clock = FakeClock()
    with TestClient(server.app, base_url=server.origin) as client:
        with client.websocket_connect(
            websocket_url(server),
            headers={"Origin": server.origin},
        ) as ws:
            assert ws.receive_json()["replay"] is not None
            assert server.replay is not None
            messages = [ws.receive_json() for _ in range(len(server.replay) + 1)]
    assert sum(message["kind"] == "state" for message in messages) == 1
    assert [message["event"] for message in messages if message["kind"] == "event"] == [
        event.model_dump(mode="json") for event in server.replay
    ]


def test_incomplete_two_stream_recording_has_typed_error(tmp_path: Path) -> None:
    (tmp_path / "plan.jsonl").write_bytes((FIXTURE / "plan.jsonl").read_bytes())
    with pytest.raises(UIServerError):
        load_replay(tmp_path, guard=RedactionGuard())


def test_multiple_speed_changes_during_recorded_events_repace_current_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_fixture(monkeypatch)
    clock = PlaybackClock()
    studio = instance(clock)
    speeds: dict[int, Literal[1, 2, 4]] = {1: 2, 2: 4, 3: 1}
    changed: list[int] = []
    start = clock.now()

    def change() -> None:
        seq = studio.snapshot().state.seq
        if seq in speeds and seq not in changed:
            changed.append(seq)
            studio.control(ReplaySpeed(kind="replay_speed", speed=speeds[seq]))

    clock.on_sleep = change
    studio.control(OpenReplay(kind="open_replay"))
    join_replay(studio)
    assert changed == [1, 2, 3]
    assert (clock.now() - start).total_seconds() == pytest.approx(33.4, abs=0.001)
    studio.control(CloseReplay(kind="close_replay"))


def test_all_playback_controls_require_token_even_after_recording_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_fixture(monkeypatch)
    studio = instance()
    studio.control(OpenReplay(kind="open_replay"))
    join_replay(studio)
    server = studio.server
    with TestClient(server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            websocket_url(server), headers={"Origin": ORIGIN}
        ) as ws:
            ws.receive_json()
            ws.receive_json()
            for message in (
                {"kind": "replay_speed", "speed": 4},
                {"kind": "replay_pause", "paused": True},
                {"kind": "close_replay"},
            ):
                ws.send_json(message)
                assert ws.receive_json()["reason"] == "control unavailable"
            replay = studio.snapshot().replay
            assert replay is not None and replay.speed == 1 and not replay.paused
    studio.control(CloseReplay(kind="close_replay"))
