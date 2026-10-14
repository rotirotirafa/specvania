"""T25: real git/ledger/engine, fake models, driven entirely through WebSocket."""

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from starlette.testclient import TestClient, WebSocketTestSession
from test_desktop_approval import CooperativeClock
from test_engine import action, boss, git, review

from specvania.agent.approval import TerminalResponder
from specvania.config import initialize_config, load_config
from specvania.ledger import Ledger
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.run.bus import EventBus
from specvania.run.events import ApprovalRequested, RedactionGuard, RunFinished
from specvania.run.gitops import GitOps
from specvania.run.state import fold_events
from specvania.sdd.planner import PlanError
from specvania.ui import studio as studio_module
from specvania.ui.server import ControlMessage, UIServer
from specvania.ui.studio import Studio

ORIGIN = "http://127.0.0.1:8765"
SPEC = """### S1 · Verified work
```gherkin
Given a project
When the rooms clear
Then work is verified
```
"""
TASKS = """## A1 · Work
boss: S1

### T01 · First room
depends_on: none
covers: S1
files: approved.txt

Checklist:
- [ ] Work is verified

### T02 · Second room
depends_on: T01
covers: S1
files: built.txt

Checklist:
- [ ] Work is verified
"""


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def studio_ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Postgres requires SPECVANIA_TEST_DATABASE_URL; run G9")
        return url
    return f"sqlite:///{tmp_path / 'studio-ledger.db'}"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Studio Test")
    git(root, "config", "user.email", "studio@example.invalid")
    (root / "modified.txt").write_text("original modified\n")
    (root / "staged.txt").write_text("original staged\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    return root


def prepare(root: Path) -> None:
    initialize_config(root)
    path = root / ".specvania/config.toml"
    path.write_text(path.read_text().replace("lives = 3", "lives = 1"))
    git(root, "add", ".")
    git(root, "commit", "-m", "initialize")


def draft() -> Completion:
    return Completion(content=json.dumps({"spec_md": SPEC, "tasks_md": TASKS}))


def clarification() -> Completion:
    return Completion(
        content=json.dumps(
            {
                "needs_clarification": True,
                "questions": [
                    {
                        "id": "q1",
                        "question": "Which behavior?",
                        "options": ["yes", "no"],
                        "default": "yes",
                    }
                ],
            }
        )
    )


def make_studio(monkeypatch: pytest.MonkeyPatch, replies: list[Any]) -> Studio:
    provider = ScriptedProvider(replies)
    monkeypatch.setattr(
        studio_module, "OpenAIProvider", lambda *args, **kwargs: provider
    )
    return Studio(guard=RedactionGuard(), clock=CooperativeClock(), port=8765)


@contextmanager
def socket(instance: Studio) -> Iterator[WebSocketTestSession]:
    with TestClient(instance.server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={instance.server.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json()["kind"] == "state"
            yield ws


def send(ws: WebSocketTestSession, instance: Studio, kind: str, **fields: Any) -> None:
    ws.send_json(
        {"kind": kind, "control_token": instance.server.control_token, **fields}
    )


def receive(ws: WebSocketTestSession, kind: str, **fields: Any) -> dict[str, Any]:
    for _ in range(1000):
        message = ws.receive_json()
        if message.get("error"):
            pytest.fail(message["error"])
        if message["kind"] == kind and all(
            message.get(key) == value for key, value in fields.items()
        ):
            return message
    pytest.fail("Expected studio message missing")


def ready(
    ws: WebSocketTestSession, instance: Studio, root: Path, *, clarify: bool = False
) -> None:
    send(ws, instance, "open_project", path=str(root))
    assert receive(ws, "studio_state", phase="project_open")["project"] == {
        "path": str(root),
        "initialized": True,
    }
    send(ws, instance, "start_plan", idea="Build work", no_clarify=not clarify)
    if clarify:
        while not (
            prompt := receive(ws, "studio_state", phase="planning")["clarify_request"]
        ):
            pass
        send(
            ws,
            instance,
            "clarify_answer",
            question_id=prompt["question_id"],
            answer="yes",
        )
    assert receive(ws, "studio_state", phase="world_ready")["worlds"] == ["build-work"]


def test_s18_websocket_only_plan_save_run_approval_pause_finished(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    instance = make_studio(
        monkeypatch,
        [
            clarification(),
            draft(),
            action("run_shell", command="printf approved > approved.txt"),
            action(),
            review(False),
            action(),
            review(),
            action("write_file", path="built.txt", content="built\n"),
            action(),
            review(),
            boss(),
        ],
    )

    class NoTerminal:
        def read(self, *args: Any) -> str:
            pytest.fail("Studio read stdin")

        readline = read
        fileno = read

    monkeypatch.setattr(sys, "stdin", NoTerminal())
    monkeypatch.setattr(
        TerminalResponder,
        "__init__",
        lambda *args, **kwargs: pytest.fail("Studio constructed terminal responder"),
    )
    with socket(instance) as ws:
        ready(ws, instance, project, clarify=True)
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        map_commit = git(project, "rev-parse", "HEAD")
        assert (
            git(project, "show", "-s", "--format=%s", map_commit)
            == "specvania: map build-work"
        )
        assert set(
            git(project, "show", "--format=", "--name-only", map_commit).splitlines()
        ) == {
            ".specvania/specs/build-work/spec.md",
            ".specvania/specs/build-work/tasks.md",
        }
        send(ws, instance, "start_run", slug="build-work", allow_dirty=False)
        while True:
            message = ws.receive_json()
            assert instance.server.control_token not in json.dumps(message)
            assert instance.server.view_token not in json.dumps(message)
            assert not message.get("error")
            if (
                message["kind"] == "event"
                and message["event"]["type"] == "approval_requested"
            ):
                prompt = message["approval_request"]
                assert prompt["command"] == "printf approved > approved.txt"
                send(
                    ws,
                    instance,
                    "approval_answer",
                    request_id=prompt["request_id"],
                    answer=True,
                )
            elif message["kind"] == "studio_state" and message["pause_request"]:
                assert "Out of lives" in message["pause_request"]
                send(ws, instance, "pause_decision", choice="retry")
            elif message["kind"] == "studio_state" and message["phase"] == "finished":
                break
        state = instance.snapshot().state
        assert state.status == "finished"
        assert git(project, "branch", "--show-current") == f"specvania/{state.run_id}"
        subjects = git(
            project, "log", "--format=%s", f"{map_commit}..HEAD"
        ).splitlines()
        assert subjects == ["specvania(T02): Second room", "specvania(T01): First room"]
        assert (project / "approved.txt").read_text() == "approved"
        assert (project / "built.txt").read_text() == "built\n"
        config = load_config(project / ".specvania/config.toml")
        with Ledger.from_config(
            project, config.ledger, clock=instance.clock, guard=instance.guard
        ) as ledger:
            assert state.run_id is not None
            events = ledger.list_events(state.run_id)
            assert sum(event.type == "room_cleared" for event in events) == 2
            assert sum(event.type == "run_paused" for event in events) == 1
            assert sum(event.type == "approval_answered" for event in events) == 1
            assert ledger.get_run(state.run_id).status == "finished"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_save_preserves_modified_file_and_unrelated_index_exactly_and_dirty_refusal(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    instance = make_studio(
        monkeypatch, [draft(), action(), review(), action(), review(), boss()]
    )
    with socket(instance) as ws:
        ready(ws, instance, project)
        (project / "modified.txt").write_text("uncommitted\n")
        (project / "staged.txt").write_text("staged\n")
        git(project, "add", "staged.txt")
        (project / "staged.txt").write_text("unstaged on top\n")
        staged = git(project, "diff", "--cached", "--binary", "--", "staged.txt")
        unstaged = git(project, "diff", "--binary", "--", "modified.txt", "staged.txt")
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        assert (
            git(project, "diff", "--cached", "--binary", "--", "staged.txt") == staged
        )
        assert (
            git(project, "diff", "--binary", "--", "modified.txt", "staged.txt")
            == unstaged
        )
        assert git(project, "show", "HEAD:modified.txt") == "original modified"
        assert git(project, "show", "HEAD:staged.txt") == "original staged"
        assert all(
            path.startswith(".specvania/specs/build-work/")
            for path in git(
                project, "show", "--format=", "--name-only", "HEAD"
            ).splitlines()
        )
        branch = git(project, "branch", "--show-current")
        send(ws, instance, "start_run", slug="build-work", allow_dirty=False)
        rejection = receive(ws, "approval_rejected")
        assert set(rejection["dirty_paths"]) == {"modified.txt", "staged.txt"}
        assert instance.phase == "world_ready"
        assert git(project, "branch", "--show-current") == branch
        send(ws, instance, "start_run", slug="build-work", allow_dirty=True)
        receive(ws, "studio_state", phase="finished")
        assert git(project, "branch", "--show-current").startswith("specvania/")


def test_clarify_reconnect_stale_answers_and_wrong_phase_leave_state_unchanged(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare(project)
    instance = make_studio(monkeypatch, [clarification(), draft()])
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        send(ws, instance, "start_plan", idea="Build work", no_clarify=False)
        while not (
            prompt := receive(ws, "studio_state", phase="planning")["clarify_request"]
        ):
            pass
        assert prompt == {
            "question_id": "q1",
            "text": "Which behavior?",
            "options": ["yes", "no"],
            "default": "yes",
        }
        for kind, fields in [
            ("start_run", {"slug": "build-work"}),
            ("open_project", {"path": str(project)}),
            ("save_world", {"slug": "build-work"}),
            ("clarify_answer", {"question_id": "unknown", "answer": "bad"}),
        ]:
            send(ws, instance, kind, **fields)
            receive(ws, "approval_rejected")
            info = instance.info()
            assert instance.phase == "planning"
            assert info is not None and info.clarify_request is not None
    with socket(instance) as ws:
        assert receive(ws, "studio_state")["clarify_request"] == prompt
        send(ws, instance, "clarify_answer", question_id="q1", answer="")
        assert receive(ws, "studio_state", clarify_request=None)["phase"] == "planning"
        receive(ws, "studio_state", phase="world_ready")
        send(ws, instance, "clarify_answer", question_id="q1", answer="stale")
        receive(ws, "approval_rejected")
        assert instance.phase == "world_ready"
        assert (
            "| q1 | Which behavior? | yes |"
            in (project / ".specvania/specs/build-work/spec.md").read_text()
        )


CONTROLS = [
    {"kind": "open_project", "path": "/"},
    {"kind": "init_project"},
    {"kind": "start_plan", "idea": "idea"},
    {"kind": "clarify_answer", "question_id": "q1", "answer": "yes"},
    {"kind": "save_world", "slug": "world"},
    {"kind": "start_run", "slug": "world"},
    {"kind": "pause_decision", "choice": "abort"},
    {"kind": "approval_answer", "request_id": "id", "answer": True},
    {"kind": "shutdown"},
]


@pytest.mark.parametrize("message", CONTROLS)
@pytest.mark.parametrize("token", [None, "wrong", "view"])
def test_every_control_requires_token_and_connection_stays_open(
    monkeypatch: pytest.MonkeyPatch,
    message: dict[str, Any],
    token: str | None,
) -> None:
    instance = make_studio(monkeypatch, [])
    with socket(instance) as ws:
        payload = dict(message)
        if token is not None:
            payload["control_token"] = (
                instance.server.view_token if token == "view" else token
            )
        ws.send_json(payload)
        assert receive(ws, "approval_rejected")["reason"] == "control unavailable"
        assert instance.phase == "idle" and not instance.stopped.is_set()
        ws.send_json(
            {"kind": "unknown", "control_token": instance.server.control_token}
        )
        assert receive(ws, "approval_rejected")["reason"] == "invalid message"
        send(ws, instance, "shutdown")
        assert instance.stopped.wait(2)


@pytest.mark.parametrize("message", CONTROLS)
def test_controls_are_typed_secret_excluded_and_token_not_passed_to_responder(
    monkeypatch: pytest.MonkeyPatch,
    message: dict[str, Any],
) -> None:
    adapter = TypeAdapter(ControlMessage)
    model = adapter.validate_python({**message, "control_token": "secret-token"})
    assert (
        "secret-token" not in repr(model) and "control_token" not in model.model_dump()
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({**message, "unexpected": True})
    instance = make_studio(monkeypatch, [])
    received: list[Any] = []
    instance.server.control_responder = lambda msg: received.append(msg) or False
    instance.server.approval_responder = lambda msg: received.append(msg) or False
    with socket(instance) as ws:
        ws.send_json({**message, "control_token": instance.server.control_token})
        receive(ws, "approval_rejected")
    assert len(received) == 1 and received[0].control_token == ""


def test_open_git_subfolder_init_world_lint_and_project_bug_count(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    instance = make_studio(monkeypatch, [])
    subfolder = project / "nested"
    subfolder.mkdir()
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project.parent))
        assert "git work tree" in receive(ws, "approval_rejected")["reason"]
        assert instance.phase == "idle"
        before = git(project, "status", "--porcelain")
        send(ws, instance, "open_project", path=str(subfolder))
        opened = receive(ws, "studio_state", phase="project_open")
        assert opened["project"] == {"path": str(project), "initialized": False}
        assert opened["worlds"] == [] and opened["open_bugs"] == 0
        assert not (project / ".specvania").exists()
        assert git(project, "status", "--porcelain") == before
        send(ws, instance, "init_project")
        initialized = receive(ws, "studio_state", phase="project_open")
        assert initialized["project"]["initialized"]
        assert (
            load_config(project / ".specvania/config.toml").provider.api_key_env
            == "NVIDIA_API_KEY"
        )
        assert ".specvania/ledger.db*" in (project / ".gitignore").read_text()
        from specvania.bugs import open_bug_ledger

        ledger, bugs = open_bug_ledger(
            project, clock=instance.clock, guard=instance.guard
        )
        with ledger:
            bugs.add("Open bug")
            closed = bugs.add("Closed bug")
            bugs.close_wontfix(closed.id)
            other = ledger.add_project(project.parent / "other")
            ledger.add_bug(other.id, "Other project bug")
        specs = project / ".specvania/specs"
        for slug, valid in [("valid", True), ("invalid", False)]:
            directory = specs / slug
            directory.mkdir(parents=True)
            (directory / "spec.md").write_text(SPEC)
            (directory / "tasks.md").write_text(TASKS if valid else "invalid")
        send(ws, instance, "open_project", path=str(project))
        reopened = receive(ws, "studio_state", phase="project_open")
        assert reopened["worlds"] == ["valid"]
        assert reopened["open_bugs"] == 1


def test_dirty_paths_preserve_spaces_and_rename_sources(project: Path) -> None:
    (project / "space name.txt").write_text("new")
    git(project, "mv", "staged.txt", "renamed.txt")
    assert set(GitOps(project).dirty_paths()) == {
        "space name.txt",
        "staged.txt",
        "renamed.txt",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "start_run", "slug": "../outside"},
        {"kind": "save_world", "slug": ".."},
        {"kind": "start_plan", "idea": "idea", "no_clarify": "true"},
        {"kind": "pause_decision", "choice": "rest"},
        {"kind": "clarify_answer", "question_id": "q1"},
        [],
        "bad JSON object",
    ],
)
def test_malformed_messages_rejected_without_closing_connection(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    instance = make_studio(monkeypatch, [])
    with socket(instance) as ws:
        if isinstance(payload, dict):
            payload = {**payload, "control_token": instance.server.control_token}
        ws.send_json(payload)
        assert receive(ws, "approval_rejected")["reason"] == "invalid message"
        assert instance.phase == "idle"
        send(ws, instance, "shutdown")
        assert instance.stopped.wait(2)


@pytest.mark.parametrize("waiting", ["approval", "pause"])
def test_shutdown_real_studio_worker_aborts_pending_run_and_reconnect_restores_approval(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
    waiting: str,
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    replies = [
        draft(),
        action("run_shell", command="printf NEVER > should-not-exist.txt"),
    ]
    if waiting == "pause":
        replies = [draft(), action(), review(False)]
    instance = make_studio(monkeypatch, replies)
    request: dict[str, Any] | None = None
    with socket(instance) as ws:
        ready(ws, instance, project)
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        send(ws, instance, "start_run", slug="build-work")
        if waiting == "approval":
            for _ in range(100):
                message = ws.receive_json()
                if (
                    message["kind"] == "event"
                    and message["event"]["type"] == "approval_requested"
                ):
                    request = message["approval_request"]
                    break
            else:
                pytest.fail("Approval missing")
        else:
            while not receive(ws, "studio_state", phase="running")["pause_request"]:
                pass
        for kind, fields in [
            ("start_plan", {"idea": "wrong phase"}),
            ("init_project", {}),
        ]:
            send(ws, instance, kind, **fields)
            receive(ws, "approval_rejected")
            assert instance.phase == "running"
    with TestClient(instance.server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={instance.server.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            snapshot = ws.receive_json()
            if waiting == "approval":
                assert request is not None
                assert snapshot["approval_request"] == request
            else:
                assert receive(ws, "studio_state")["pause_request"]
            send(ws, instance, "shutdown")
            assert instance.stopped.wait(5)
    state = instance.snapshot().state
    assert state.status == "aborted" and instance.phase == "finished"
    assert instance.snapshot().approval_request is None
    assert instance.server.desktop_responder.pending is None
    assert not (project / "should-not-exist.txt").exists()
    config = load_config(project / ".specvania/config.toml")
    with Ledger.from_config(
        project, config.ledger, clock=instance.clock, guard=instance.guard
    ) as ledger:
        assert state.run_id is not None
        events = ledger.list_events(state.run_id)
        assert events[-1].type == "run_finished" and events[-1].status == "aborted"
        assert ledger.get_run(state.run_id).status == "aborted"
        assert sum(event.type == "run_finished" for event in events) == 1
        assert sum(event.type == "run_paused" for event in events) == (
            1 if waiting == "pause" else 0
        )
        assert not any(event.type == "approval_answered" for event in events)
        assert not any(
            event.type == "model_call" and event.status == 400 for event in events
        )
        if waiting == "approval":
            assert state.dialogue is None


@pytest.mark.parametrize("timing", ["before_call", "during_call"])
def test_shutdown_during_provider_call_aborts_without_provider_failure(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
    timing: str,
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    entered = ThreadEvent()

    def provider_call() -> Completion:
        entered.set()
        assert instance._cancelled.wait(5)
        return action()

    instance = make_studio(monkeypatch, [draft(), provider_call])
    if timing == "before_call":
        assert instance.server.bus is not None
        publish = instance.server.bus.publish

        def wait_for_cancel(event: Any) -> None:
            publish(event)
            if event.type == "attempt_started":
                entered.set()
                assert instance._cancelled.wait(5)

        monkeypatch.setattr(instance.server.bus, "publish", wait_for_cancel)

    with socket(instance) as ws:
        ready(ws, instance, project)
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        send(ws, instance, "start_run", slug="build-work")
        assert entered.wait(5)
        send(ws, instance, "shutdown")
        assert instance.stopped.wait(5)
    state = instance.snapshot().state
    assert state.status == "aborted" and instance.phase == "finished"
    info = instance.info()
    assert info is not None and info.error is None and info.pause_request is None
    assert state.run_id is not None
    config = load_config(project / ".specvania/config.toml")
    with Ledger.from_config(
        project, config.ledger, clock=instance.clock, guard=instance.guard
    ) as ledger:
        events = ledger.list_events(state.run_id)
        assert events[-1].type == "run_finished" and events[-1].status == "aborted"
        assert sum(event.type == "run_finished" for event in events) == 1
        assert not any(event.type in ("run_paused", "model_call") for event in events)
        assert ledger.get_run(state.run_id).status == "aborted"


@pytest.mark.parametrize("projection", ["state", "server"])
def test_aborted_unanswered_approval_is_not_pending_in_projection(
    projection: str,
) -> None:
    clock = CooperativeClock()
    events = (
        ApprovalRequested(
            ts=clock.now(),
            run_id="cancelled",
            seq=1,
            request_id="pending",
            command="printf NEVER",
            cwd="/repo",
        ),
        RunFinished(ts=clock.now(), run_id="cancelled", seq=2, status="aborted"),
    )
    if projection == "state":
        state = fold_events(events)
        assert state.status == "aborted" and state.dialogue is None
    else:
        guard = RedactionGuard()
        server = UIServer(
            guard=guard,
            clock=clock,
            run_id="cancelled",
            port=8765,
            bus=EventBus(guard),
            live_snapshot=lambda: events,
        )
        with TestClient(server.app, base_url=ORIGIN) as client:
            with client.websocket_connect(
                f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
                headers={"Origin": ORIGIN},
            ) as ws:
                snapshot = ws.receive_json()
                assert snapshot["state"]["status"] == "aborted"
                assert snapshot["approval_request"] is None


def test_transient_clarify_and_events_keep_summary_limits_and_no_model_output(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare(project)
    marker = "PRIVATE-MODEL-OUTPUT"
    clarification_reply = Completion(
        content=json.dumps(
            {
                "needs_clarification": True,
                "questions": [
                    {
                        "id": "q1",
                        "question": "Q" * 450,
                        "options": ["A" * 450],
                        "default": "A" * 450,
                    }
                ],
            }
        )
    )
    instance = make_studio(monkeypatch, [clarification_reply, draft()])
    seen: list[dict[str, Any]] = []
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        send(ws, instance, "start_plan", idea=marker, no_clarify=False)
        while True:
            message = ws.receive_json()
            seen.append(message)
            if message["kind"] == "studio_state" and (
                prompt := message["clarify_request"]
            ):
                break
        assert (
            len(prompt["text"])
            == len(prompt["default"])
            == len(prompt["options"][0])
            == 200
        )
        send(
            ws,
            instance,
            "clarify_answer",
            question_id=prompt["question_id"],
            answer="reply" * 200,
        )
        while True:
            message = ws.receive_json()
            seen.append(message)
            if message["kind"] == "studio_state" and message["phase"] == "world_ready":
                break
    serialized = json.dumps(seen)
    assert (
        marker not in serialized and TASKS not in serialized and SPEC not in serialized
    )
    assert "reply" * 200 not in serialized
    assert instance.server.control_token not in serialized
    assert instance.server.view_token not in serialized
    assert (
        "reply" * 200
        in (project / ".specvania/specs/private-model-output/spec.md").read_text()
    )


def test_uninitialized_open_never_opens_ledger_or_registers_project(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = make_studio(monkeypatch, [])
    monkeypatch.setattr(
        studio_module,
        "open_bug_ledger",
        lambda *args, **kwargs: pytest.fail("Uninitialized project opened ledger"),
    )
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        assert receive(ws, "studio_state", phase="project_open")["open_bugs"] == 0
        assert not (project / ".specvania").exists()


@pytest.mark.parametrize("choice", ["skip", "abort"])
def test_pause_decision_skip_and_abort_use_normal_engine(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
    choice: str,
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    instance = make_studio(monkeypatch, [draft(), action(), review(False)])
    with socket(instance) as ws:
        ready(ws, instance, project)
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        send(ws, instance, "start_run", slug="build-work")
        while not receive(ws, "studio_state", phase="running")["pause_request"]:
            pass
        send(ws, instance, "pause_decision", choice=choice)
        finished = receive(ws, "studio_state", phase="finished")
        assert finished["pause_request"] is None
        assert finished["open_bugs"] == (1 if choice == "skip" else 0)
        state = instance.snapshot().state
        assert state.status == ("finished" if choice == "skip" else "aborted")
        assert git(project, "log", "--format=%s", "-1") == "specvania: map build-work"


def test_failed_boss_pause_refreshes_open_bugs_from_ledger(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    studio_ledger_url: str,
) -> None:
    monkeypatch.setenv("SPECVANIA_DATABASE_URL", studio_ledger_url)
    prepare(project)
    instance = make_studio(
        monkeypatch,
        [draft(), action(), review(), action(), review(), boss(passed=False)],
    )
    with socket(instance) as ws:
        ready(ws, instance, project)
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        send(ws, instance, "start_run", slug="build-work")
        while not (paused := receive(ws, "studio_state", phase="running"))[
            "pause_request"
        ]:
            pass
        assert paused["open_bugs"] == 1
        send(ws, instance, "pause_decision", choice="abort")
        assert receive(ws, "studio_state", phase="finished")["open_bugs"] == 1


@pytest.mark.parametrize("secret", ["control", "view", "key"])
def test_project_world_names_never_echo_registered_secrets(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
) -> None:
    prepare(project)
    instance = make_studio(monkeypatch, [])
    value = {
        "control": instance.server.control_token,
        "view": instance.server.view_token,
        "key": os.environ["NVIDIA_API_KEY"],
    }[secret]
    directory = project / ".specvania/specs" / value
    directory.mkdir(parents=True)
    (directory / "spec.md").write_text(SPEC)
    (directory / "tasks.md").write_text(TASKS)
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        rejection = receive(ws, "approval_rejected")
        assert value not in json.dumps(rejection)
        assert instance.phase == "idle"
        send(ws, instance, "shutdown")
        assert instance.stopped.wait(2)


@pytest.mark.parametrize("already_committed", [False, True])
def test_existing_world_folder_can_be_saved_on_current_branch_and_run(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    already_committed: bool,
) -> None:
    prepare(project)
    slug = "Existing world_1"
    directory = project / ".specvania/specs" / slug
    directory.mkdir(parents=True)
    (directory / "spec.md").write_text(SPEC)
    (directory / "tasks.md").write_text(TASKS)
    if already_committed:
        git(project, "add", ".specvania/specs")
        git(project, "commit", "-m", "existing world")
    original_head = git(project, "rev-parse", "HEAD")
    instance = make_studio(
        monkeypatch, [action(), review(), action(), review(), boss()]
    )
    original_branch = git(project, "branch", "--show-current")
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        assert receive(ws, "studio_state", phase="project_open")["worlds"] == [slug]
        send(ws, instance, "save_world", slug=slug)
        receive(ws, "studio_state", phase="world_ready")
        assert git(project, "branch", "--show-current") == original_branch
        if already_committed:
            assert git(project, "rev-parse", "HEAD") == original_head
        else:
            assert (
                git(project, "show", "-s", "--format=%s", "HEAD")
                == f"specvania: map {slug}"
            )
        send(ws, instance, "start_run", slug=slug)
        receive(ws, "studio_state", phase="finished")
        assert instance.snapshot().state.status == "finished"


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_save_changed_world_creates_one_commit_only_in_world_folder(
    project: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    prepare(project)
    directory = project / ".specvania/specs/build-work"
    directory.mkdir(parents=True)
    (directory / "spec.md").write_text(SPEC)
    (directory / "tasks.md").write_text(TASKS)
    (directory / "notes.md").write_text("notes")
    git(project, "add", ".specvania/specs")
    git(project, "commit", "-m", "existing world")
    before = git(project, "rev-parse", "HEAD")
    if change == "edit":
        (directory / "tasks.md").write_text(TASKS + "\nUpdated room context.\n")
        changed_file = "tasks.md"
    else:
        (directory / "notes.md").unlink()
        changed_file = "notes.md"
    (project / "modified.txt").write_text("uncommitted\n")
    (project / "staged.txt").write_text("staged\n")
    git(project, "add", "staged.txt")
    (project / "staged.txt").write_text("unstaged on top\n")
    staged = git(project, "diff", "--cached", "--binary")
    unstaged = git(project, "diff", "--binary", "--", "modified.txt", "staged.txt")
    instance = make_studio(monkeypatch, [])
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        saved = git(project, "rev-parse", "HEAD")
        assert git(project, "rev-list", "--count", f"{before}..HEAD") == "1"
        assert git(project, "show", "--format=", "--name-only", "HEAD") == (
            f".specvania/specs/build-work/{changed_file}"
        )
        # A second save must also preserve unrelated staged and unstaged changes.
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        assert git(project, "rev-parse", "HEAD") == saved
        assert git(project, "diff", "--cached", "--binary") == staged
        assert (
            git(project, "diff", "--binary", "--", "modified.txt", "staged.txt")
            == unstaged
        )


@pytest.mark.parametrize("operation", ["start_run", "save_world"])
def test_world_lint_message_reaches_control_rejection(
    project: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    prepare(project)
    instance = make_studio(monkeypatch, [draft()])
    with socket(instance) as ws:
        ready(ws, instance, project)
        (project / ".specvania/specs/build-work/tasks.md").write_text(
            TASKS.replace("depends_on: T01", "depends_on: T99")
        )
        before = git(project, "rev-parse", "HEAD")
        send(ws, instance, operation, slug="build-work")
        assert receive(ws, "approval_rejected")["reason"] == "World has 1 lint errors"
        assert instance.phase == "world_ready"
        assert git(project, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("boundary", ["control", "worker"])
@pytest.mark.parametrize(
    "failure",
    ["provider_401", "long_plan", "secret", "secret_after_limit", "unexpected"],
)
def test_planning_errors_surface_safe_typed_messages_only(
    project: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, failure: str
) -> None:
    prepare(project)
    secret = "registered-studio-secret"
    errors = {
        "provider_401": ProviderError(status=401),
        "long_plan": PlanError("Mage could not produce a plan. " + "x" * 250),
        "secret": PlanError(f"Cannot plan with {secret}"),
        "secret_after_limit": PlanError("x" * 250 + secret),
        "unexpected": RuntimeError("PRIVATE-MODEL-OUTPUT"),
    }

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise errors[failure]

    instance = make_studio(monkeypatch, [fail])
    instance.guard.register(secret)
    if boundary == "control":
        monkeypatch.setattr(instance, "_settings", fail)
    expected = {
        "provider_401": (
            "Provider call failed (HTTP 401); check provider configuration."
        ),
        "long_plan": ("Mage could not produce a plan. " + "x" * 250)[:200],
    }.get(
        failure,
        "Cannot perform this operation; "
        "check the git project, configuration and world files."
        if boundary == "control"
        else "Cannot complete the plan or run; check project configuration and files.",
    )
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        send(ws, instance, "start_plan", idea="Build work", no_clarify=True)
        if boundary == "control":
            message = receive(ws, "approval_rejected")
            assert message["reason"] == expected
        else:
            while True:
                message = ws.receive_json()
                assert secret not in json.dumps(message)
                assert "PRIVATE-MODEL-OUTPUT" not in json.dumps(message)
                if message["kind"] == "studio_state" and message["error"]:
                    break
            assert message["phase"] == "project_open"
            assert message["error"] == expected
        assert instance.phase == "project_open"
        assert secret not in json.dumps(message)
        assert "PRIVATE-MODEL-OUTPUT" not in json.dumps(message)


@pytest.mark.parametrize("token_budget", [0, -1, True, 1.5, "123", None])
def test_start_run_token_budget_rejects_non_positive_or_non_integer(
    token_budget: Any,
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ControlMessage).validate_python(
            {"kind": "start_run", "slug": "world", "token_budget": token_budget}
        )


@pytest.mark.parametrize("budget", [12345, None])
def test_start_run_token_budget_override_is_run_only(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget: int | None,
) -> None:
    prepare(project)
    config_path = project / ".specvania/config.toml"
    original = config_path.read_bytes()
    instance = make_studio(
        monkeypatch, [draft(), action(), review(), action(), review(), boss()]
    )
    with socket(instance) as ws:
        ready(ws, instance, project)
        send(ws, instance, "save_world", slug="build-work")
        receive(ws, "studio_state", phase="world_ready")
        fields = {"token_budget": budget} if budget is not None else {}
        send(ws, instance, "start_run", slug="build-work", **fields)
        while True:
            message = ws.receive_json()
            assert message["kind"] != "approval_rejected", message
            if message["kind"] == "studio_state" and message["phase"] == "finished":
                break
        assert instance.snapshot().state.token_budget == (
            budget if budget is not None else 1_000_000
        )
    assert config_path.read_bytes() == original
    assert load_config(config_path).limits.token_budget == 1_000_000


def test_planned_world_payload_reaches_ready_and_reconnect_without_spec_prose(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from specvania.run.events import build_world_payload
    from specvania.sdd.world import parse_world

    prepare(project)
    instance = make_studio(monkeypatch, [draft()])
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        opened = receive(ws, "studio_state", phase="project_open")
        assert opened["world"] is None and opened["world_slug"] is None
        send(ws, instance, "start_plan", idea="Build work", no_clarify=True)
        planned = receive(ws, "studio_state", phase="world_ready")
        expected = build_world_payload(
            parse_world(project / ".specvania/specs/build-work")
        ).model_dump(mode="json")
        assert planned["world"] == expected
        assert planned["world_slug"] == "build-work"
        assert SPEC not in json.dumps(planned) and TASKS not in json.dumps(planned)
        assert "checklist" not in json.dumps(planned["world"])
        assert "files" not in json.dumps(planned["world"])
    with socket(instance) as ws:
        restored = receive(ws, "studio_state", phase="world_ready")
        assert restored["world"] == expected
        assert restored["world_slug"] == "build-work"
        send(ws, instance, "open_project", path=str(project))
        cleared = receive(ws, "studio_state", phase="project_open")
        assert cleared["world"] is None and cleared["world_slug"] is None


@pytest.mark.parametrize("already_committed", [False, True])
def test_saved_existing_world_payload_and_clearing_on_project_open(
    project: Path, monkeypatch: pytest.MonkeyPatch, already_committed: bool
) -> None:
    from specvania.run.events import build_world_payload
    from specvania.sdd.world import parse_world

    prepare(project)
    directory = project / ".specvania/specs/existing"
    directory.mkdir(parents=True)
    (directory / "spec.md").write_text(SPEC)
    (directory / "tasks.md").write_text(TASKS)
    if already_committed:
        git(project, "add", ".specvania/specs")
        git(project, "commit", "-m", "existing map")
    instance = make_studio(monkeypatch, [])
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        send(ws, instance, "save_world", slug="existing")
        saved = receive(ws, "studio_state", phase="world_ready")
        assert saved["world"] == build_world_payload(parse_world(directory)).model_dump(
            mode="json"
        )
        assert saved["world_slug"] == "existing"
        assert saved["world"]["doors"] == [{"src": "T01", "dst": "T02"}]
        assert saved["world"]["boss_ids"] == ["S1"]
        instance.transition("project_open")
        cleared = receive(ws, "studio_state", phase="project_open")
        assert cleared["world"] is None and cleared["world_slug"] is None
        send(ws, instance, "open_project", path=str(project))
        cleared = receive(ws, "studio_state", phase="project_open")
        assert cleared["world"] is None and cleared["world_slug"] is None


def test_ready_payload_requires_lint_and_sandbox_before_parsing(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prepare(project)
    directory = project / ".specvania/specs/existing"
    directory.mkdir(parents=True)
    (directory / "spec.md").write_text(SPEC)
    (directory / "tasks.md").write_text(TASKS)
    instance = make_studio(monkeypatch, [])
    with socket(instance) as ws:
        send(ws, instance, "open_project", path=str(project))
        receive(ws, "studio_state", phase="project_open")
        monkeypatch.setattr(
            studio_module,
            "parse_world",
            lambda *args: pytest.fail("Parsed before validating world directory"),
        )
        (directory / "tasks.md").write_text("invalid")
        send(ws, instance, "save_world", slug="existing")
        receive(ws, "approval_rejected")
        info = instance.info()
        assert info is not None and info.world is None
        outside = tmp_path / "outside.md"
        outside.write_text(TASKS)
        (directory / "tasks.md").unlink()
        (directory / "tasks.md").symlink_to(outside)
        send(ws, instance, "save_world", slug="existing")
        receive(ws, "approval_rejected")
