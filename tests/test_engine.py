"""T11 acceptance tests using real temporary Git, Ledger and scripted models."""

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from specvania import cli
from specvania.agent.approval import ScriptedResponder, TerminalResponder
from specvania.clock import FakeClock
from specvania.config import Config, LimitsConfig, RolesConfig
from specvania.ledger.repo import Ledger
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.run.bus import EventBus
from specvania.run.engine import Engine, PauseDecision, rebuild_run_state
from specvania.run.events import (
    Action,
    RedactionGuard,
    Role,
    RunPaused,
    SecretLeakError,
)
from specvania.run.gitops import GitError
from specvania.run.state import RoomState, fold_events
from specvania.ui.server import UIServer, UIServerError


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


type RoomDefinition = tuple[str, str, tuple[str, ...]]
type Reply = Completion | ProviderError | Callable[[], Completion]


def write_world(root: Path, rooms: Sequence[RoomDefinition]) -> Path:
    directory = root / "world"
    directory.mkdir(exist_ok=True)
    areas = sorted({area for _, area, _ in rooms}, key=lambda a: int(a[1:]))
    specs: list[str] = []
    blocks: list[str] = []
    for index, area in enumerate(areas, 1):
        specs.append(
            f"### S{index} · Acceptance {area}\n```gherkin\n"
            "Given a world\nWhen the room is cleared\nThen work is verified\n```\n"
        )
        blocks.append(f"## {area} · Area {area}\nboss: S{index}\n")
        for room_id, room_area, dependencies in rooms:
            if room_area != area:
                continue
            blocks.append(
                f"### {room_id} · Build {room_id}\n"
                f"depends_on: {', '.join(dependencies) or 'none'}\n"
                f"covers: S{index}\nfiles: {room_id}.txt\n\n"
                "Implement the room.\n\nChecklist:\n- [ ] Work is verified\n"
            )
            (root / f"{room_id}.txt").write_text("original\n")
    (directory / "spec.md").write_text("\n".join(specs))
    (directory / "tasks.md").write_text("\n".join(blocks))
    git(root, "add", ".")
    git(root, "commit", "-m", "world")
    return directory


def action(name: str = "finish", **args: Any) -> Completion:
    if name == "finish":
        args = {}
    return Completion(
        content=json.dumps({"thought": "Working", "action": name, "args": args}),
        tokens_in=2,
        tokens_out=3,
    )


def review(passed: bool = True, feedback: str = "Verified") -> Completion:
    return Completion(
        content=json.dumps(
            {
                "verdict": "pass" if passed else "fail",
                "checklist": [
                    {"item": "Work is verified", "ok": passed, "note": "checked"}
                ],
                "feedback": feedback,
            }
        ),
        tokens_in=5,
        tokens_out=7,
    )


def boss(
    scenario: str = "S1", passed: bool = True, reason: str = "Verified"
) -> Completion:
    return Completion(
        content=json.dumps([{"scenario": scenario, "passed": passed, "reason": reason}])
    )


def success(room_id: str) -> list[Reply]:
    return [
        action("write_file", path=f"{room_id}.txt", content=f"built {room_id}\n"),
        action(),
        review(),
    ]


def config(
    *, lives: int = 3, max_steps: int = 25, gates: list[str] | None = None
) -> Config:
    return Config(
        roles=RolesConfig(mage=["m1"], warrior=["w1", "w2"], paladin=["p1", "p2"]),
        limits=LimitsConfig(
            lives=lives, max_steps=max_steps, failures_before_fallback=1
        ),
        gates=gates or [],
    )


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Postgres requires SPECVANIA_TEST_DATABASE_URL; run G9")
        return url
    return f"sqlite:///{tmp_path / 'ledger.db'}"


@dataclass
class Harness:
    root: Path
    ledger_url: str
    ledger: Ledger
    guard: RedactionGuard
    bus: EventBus
    clock: FakeClock

    def engine(
        self,
        script: Sequence[Reply],
        *,
        settings: Config | None = None,
        decide: Callable[[RunPaused], PauseDecision | None] = lambda pause: None,
        approvals: Sequence[tuple[bool, int]] = (),
    ) -> Engine:
        return Engine(
            root=self.root,
            config=settings or config(),
            provider=ScriptedProvider(script),
            ledger=self.ledger,
            bus=self.bus,
            clock=self.clock,
            decide=decide,
            responders=[ScriptedResponder("terminal", approvals)] if approvals else [],
            gate_timeout=10000,
            shell_timeout=10000,
        )


@pytest.fixture
def harness(tmp_path: Path, ledger_url: str) -> Iterator[Harness]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Specvania test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(
        ".specvania/ledger.db*\n.specvania/runs/\n.specvania/gate-marker\n"
    )
    (root / ".specvania").mkdir()
    (root / ".specvania/config.toml").write_text("")
    guard = RedactionGuard()
    guard.register(os.environ["NVIDIA_API_KEY"])
    clock = FakeClock()
    with Ledger.open(ledger_url, clock=clock, guard=guard) as ledger:
        yield Harness(root, ledger_url, ledger, guard, EventBus(guard), clock)


def test_doors_topological_ties_commits_and_world_payload(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T02", "A1", ("T01",)),
            ("T03", "A1", ()),
            ("T01", "A1", ()),
        ],
    )
    initial = git(harness.root, "rev-parse", "HEAD")
    commands: list[list[str]] = []
    original_run = subprocess.run
    original_publish = harness.bus.publish

    def recorded(command: list[str], **kwargs: Any) -> Any:
        if command[0] == "git":
            commands.append(command)
            assert "push" not in command
        return original_run(command, **kwargs)

    def publish(event: Any) -> None:
        assert harness.ledger.list_events(event.run_id)[-1] == event
        assert event.seq > 0
        original_publish(event)

    monkeypatch.setattr(subprocess, "run", recorded)
    monkeypatch.setattr(harness.bus, "publish", publish)
    with harness.bus.subscribe() as subscription:
        engine = harness.engine(
            success("T01") + success("T02") + success("T03") + [boss()]
        )
        state = engine.run(directory)
        events = harness.ledger.list_events(engine.run_id)
        assert [subscription.get(timeout=0) for _ in events] == events
    assert state.status == "finished"
    assert [e.room_id for e in events if e.type == "room_entered"] == [
        "T01",
        "T02",
        "T03",
    ]
    cleared: set[str] = set()
    for event in events:
        if event.type == "room_entered" and event.room_id == "T02":
            assert "T01" in cleared
        if event.type == "room_cleared":
            cleared.add(event.room_id)
            assert git(harness.root, "show", "-s", "--format=%s", event.commit) == (
                f"specvania({event.room_id}): Build {event.room_id}"
            )
    assert len([e for e in events if e.type == "room_cleared"]) == 3
    assert git(harness.root, "rev-list", "--count", f"{initial}..HEAD") == "3"
    assert git(harness.root, "rev-parse", "HEAD~3") == initial
    assert git(harness.root, "branch", "--show-current") == f"specvania/{engine.run_id}"
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", engine.run_id)
    assert commands and all("push" not in command for command in commands)
    world = next(e for e in events if e.type == "world_loaded")
    assert world.type == "world_loaded"
    assert [area.id for area in world.areas] == ["A1"] and world.boss_ids == ("S1",)
    assert [(d.src, d.dst) for d in world.doors] == [("T01", "T02")]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    run = harness.ledger.get_run(engine.run_id)
    assert run.kind == "run" and run.rooms_cleared == 3
    assert run.status == "finished" and run.finished_at is not None
    assert run.calls == len([e for e in events if e.type == "model_call"])
    assert run.tokens_in == sum(e.tokens_in for e in events if e.type == "model_call")


def test_out_of_lives_retry_resets_budget_and_preserves_feedback(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    pauses: list[RunPaused] = []

    def retry(pause: RunPaused) -> PauseDecision:
        assert harness.ledger.get_run(pause.run_id).status == "paused"
        pauses.append(pause)
        return "retry"

    engine = harness.engine(
        [
            action(),
            review(False, "Fix the assertion"),
            action(),
            review(False),
            action(),
            review(False),
            action(),
            review(),
            boss(),
        ],
        settings=config(lives=2),
        decide=retry,
    )
    assert engine.run(directory).status == "finished"
    events = harness.ledger.list_events(engine.run_id)
    assert len(pauses) == 1 and "Out of lives" in pauses[0].reason
    assert [e.attempt for e in events if e.type == "attempt_started"] == [1, 2, 3, 4]
    assert [e.attempt for e in events if e.type == "respawn"] == [2, 3, 4]
    provider = engine.provider
    assert isinstance(provider, ScriptedProvider)
    assert (
        json.loads(provider.calls[2].messages[-1].content)["feedback"]
        == "Fix the assertion"
    )
    assert len([e for e in events if e.type == "room_cleared"]) == 1


def test_skip_keeps_dependent_doors_closed_and_runs_independent_room(
    harness: Harness,
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T01", "A1", ()),
            ("T02", "A1", ("T01",)),
            ("T03", "A1", ()),
        ],
    )
    engine = harness.engine(
        [action(), review(False, "Broken")] + success("T03"),
        settings=config(lives=1),
        decide=lambda pause: "skip",
    )
    state = engine.run(directory)
    assert state.status == "finished"
    assert {r.id: r.state for r in state.rooms} == {
        "T01": RoomState.SKIPPED,
        "T02": RoomState.LOCKED,
        "T03": RoomState.CLEARED,
    }
    events = harness.ledger.list_events(engine.run_id)
    assert [e.room_id for e in events if e.type == "room_skipped"] == ["T01"]
    assert [e.room_id for e in events if e.type == "room_entered"] == ["T01", "T03"]
    assert not any(e.type == "boss_started" for e in events)


def test_abort_finishes_run_with_aborted_status(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [action(), review(False)],
        settings=config(lives=1),
        decide=lambda pause: "abort",
    )
    state = engine.run(directory)
    run = harness.ledger.get_run(engine.run_id)
    assert state.status == run.status == "aborted" and run.finished_at is not None
    assert run.rooms_cleared == 0
    events = harness.ledger.list_events(engine.run_id)
    assert events[-2].type == "run_paused" and events[-1].type == "run_finished"
    assert not any(e.type == "room_cleared" for e in events)


def test_boss_failure_pause_names_scenario_and_reason(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        success("T01") + [boss(passed=False, reason="Acceptance failed")]
    )
    state = engine.run(directory)
    assert state.status == harness.ledger.get_run(engine.run_id).status == "paused"
    assert (
        state.reason is not None
        and "S1" in state.reason
        and "Acceptance failed" in state.reason
    )
    events = harness.ledger.list_events(engine.run_id)
    assert events[-1].type == "run_paused"
    assert state.bosses[0].passed is False
    assert next(e.seq for e in events if e.type == "room_cleared") < next(
        e.seq for e in events if e.type == "boss_started"
    )
    assert not any(e.type == "run_finished" for e in events)


@pytest.mark.parametrize("decision", ["skip", "abort"])
def test_boss_skip_continues_later_area_and_abort_stops(
    harness: Harness, decision: PauseDecision
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T01", "A1", ()),
            ("T02", "A2", ("T01",)),
        ],
    )
    script = success("T01") + [boss(passed=False, reason="Missing acceptance")]
    if decision == "skip":
        script += success("T02") + [boss("S2")]
    engine = harness.engine(script, decide=lambda pause: decision)
    state = engine.run(directory)
    assert state.bosses[0].passed is False
    assert state.status == ("finished" if decision == "skip" else "aborted")
    events = harness.ledger.list_events(engine.run_id)
    assert [e.room_id for e in events if e.type == "room_entered"] == (
        ["T01", "T02"] if decision == "skip" else ["T01"]
    )
    assert not any(e.type == "room_skipped" for e in events)
    bugs = [e for e in events if e.type == "bug_opened"]
    assert len(bugs) == 1
    assert bugs[0].source == "boss_scenario" and "S1" in bugs[0].title
    assert bugs[0].body == "Missing acceptance"


def test_boss_retry_reuses_area_diff_and_runs_fresh_gates(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "from pathlib import Path; p=Path('.specvania/gate-marker'); "
        "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); "
        "print('fresh evidence', n+1)"
    )
    engine = harness.engine(
        success("T01") + [boss(passed=False, reason="Try again"), boss()],
        settings=config(gates=[command]),
        decide=lambda pause: "retry",
    )
    state = engine.run(directory)
    assert state.status == "finished" and state.bosses[0].passed is True
    provider = engine.provider
    assert isinstance(provider, ScriptedProvider)
    first = json.loads(provider.calls[-2].messages[-1].content)
    second = json.loads(provider.calls[-1].messages[-1].content)
    assert first["diff"] == second["diff"] and "+built T01" in first["diff"]
    assert first["gate_results"][0]["output_tail"] == "fresh evidence 2\n"
    assert second["gate_results"][0]["output_tail"] == "fresh evidence 3\n"
    events = harness.ledger.list_events(engine.run_id)
    assert len([e for e in events if e.type == "gate_result"]) == 3
    assert len([e for e in events if e.type == "boss_started"]) == 2
    assert len([e for e in events if e.type == "room_cleared"]) == 1


def test_spec_versions_snapshot_reuse_and_edit_create_new_version(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    first = harness.engine([action(), review(), boss()]).run(directory)
    second = harness.engine([action(), review(), boss()]).run(directory)
    assert first.run_id is not None and second.run_id is not None
    first_run = harness.ledger.get_run(first.run_id)
    second_run = harness.ledger.get_run(second.run_id)
    assert first_run.spec_version_id == second_run.spec_version_id
    version = harness.ledger.get_spec_version(first_run.spec_version_id)
    assert version.spec_md == (directory / "spec.md").read_text()
    assert version.tasks_md == (directory / "tasks.md").read_text()
    path = directory / "spec.md"
    path.write_text(path.read_text() + "\nAcceptance clarified.\n")
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "edit spec")
    third = harness.engine([action(), review(), boss()]).run(directory)
    assert third.run_id is not None
    third_run = harness.ledger.get_run(third.run_id)
    assert third_run.feature_id == first_run.feature_id
    assert third_run.spec_version_id != first_run.spec_version_id
    assert len(harness.ledger.list_spec_versions(first_run.feature_id)) == 2
    assert harness.ledger.get_feature(first_run.feature_id).slug == directory.name
    assert harness.ledger.get_spec_version(first_run.spec_version_id) == version


def test_killed_run_state_is_rebuilt_from_durable_ledger_events(
    harness: Harness, tmp_path: Path
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T01", "A1", ()),
            ("T02", "A1", ("T01",)),
        ],
    )
    marker = tmp_path / "killed-run-id"
    script = """
import json, os, sys
from pathlib import Path
from specvania.clock import FakeClock
from specvania.config import Config
from specvania.ledger.repo import Ledger
from specvania.provider.client import Completion
from specvania.provider.fake import ScriptedProvider
from specvania.run.bus import EventBus
from specvania.run.engine import Engine
from specvania.run.events import RedactionGuard
root, directory, url, marker = sys.argv[1:]
clock = FakeClock()
guard = RedactionGuard()
reply = Completion(content=json.dumps({
    "thought": "Done", "action": "finish", "args": {}
}))
review = Completion(content=json.dumps({
    "verdict": "pass",
    "checklist": [{"item": "Work is verified", "ok": True, "note": "checked"}],
    "feedback": "Verified"
}))
def killed():
    os._exit(91)
def started(run_id):
    Path(marker).write_text(run_id)
with Ledger.open(url, clock=clock, guard=guard) as ledger:
    Engine(
        root=Path(root), config=Config(),
        provider=ScriptedProvider([reply, review, killed]),
        ledger=ledger, bus=EventBus(guard), clock=clock
    ).run(
        Path(directory), on_started=started
    )
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(harness.root),
            str(directory),
            harness.ledger_url,
            str(marker),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 91, result.stderr
    run_id = marker.read_text()
    with Ledger.open(
        harness.ledger_url, clock=FakeClock(), guard=RedactionGuard()
    ) as reopened:
        state = rebuild_run_state(reopened, run_id)
        events = reopened.list_events(run_id)
        assert state == fold_events(events) == rebuild_run_state(reopened, run_id)
        assert state.status == "running" and state.active_room == "T02"
        assert [r.state for r in state.rooms] == [RoomState.CLEARED, RoomState.ACTIVE]
        assert state.rooms[0].commit == git(harness.root, "rev-parse", "HEAD")
        assert state.rooms[1].attempt == 1
        assert reopened.get_run(run_id).rooms_cleared == 1
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert events[-1].type == "cooldown" and events[-1].role == Role.WARRIOR
        attempts = [e for e in events if e.type == "attempt_started"]
        assert [(e.room_id, e.attempt) for e in attempts] == [("T01", 1), ("T02", 1)]
        assert not any(e.type == "run_finished" for e in events)


def test_router_exhaustion_pauses_without_consuming_life(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    pauses: list[RunPaused] = []

    def retry(pause: RunPaused) -> PauseDecision:
        pauses.append(pause)
        return "retry"

    engine = harness.engine(
        [
            ProviderError(status=500),
            ProviderError(status=500),
            action(),
            review(False),
            action(),
            review(),
            boss(),
        ],
        settings=config(lives=1),
        decide=retry,
    )
    assert engine.run(directory).status == "finished"
    assert len(pauses) == 2
    assert "chain exhausted" in pauses[0].reason and "Out of lives" in pauses[1].reason
    events = harness.ledger.list_events(engine.run_id)
    assert len([e for e in events if e.type == "room_failed"]) == 1
    assert len([e for e in events if e.type == "respawn"]) == 1
    assert len([e for e in events if e.type == "gate_result"]) == 0


def test_paladin_exhaustion_retries_review_without_new_warrior_attempt(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [
            action(),
            ProviderError(status=500),
            ProviderError(status=500),
            review(),
            boss(),
        ],
        settings=config(lives=1),
        decide=lambda pause: "retry",
    )
    assert engine.run(directory).status == "finished"
    events = harness.ledger.list_events(engine.run_id)
    assert len([e for e in events if e.type == "attempt_started"]) == 1
    assert not any(e.type == "room_failed" for e in events)
    assert len([e for e in events if e.type == "run_paused"]) == 1


def test_chain_resets_for_next_room_and_respawn_retains_fallback(
    harness: Harness,
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T01", "A1", ()),
            ("T02", "A1", ("T01",)),
        ],
    )
    engine = harness.engine(
        [
            ProviderError(status=500),
            action(),
            review(False),
            action(),
            review(),
            action(),
            review(),
            boss(),
        ]
    )
    assert engine.run(directory).status == "finished"
    provider = engine.provider
    assert isinstance(provider, ScriptedProvider)
    assert [call.model for call in provider.calls] == [
        "w1",
        "w2",
        "p1",
        "w2",
        "p1",
        "w1",
        "p1",
        "p1",
    ]


def test_failed_gate_tails_reach_paladin_and_next_warrior_context(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "from pathlib import Path; p=Path('.specvania/gate-marker'); "
        "n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); "
        "print('padding'*100); print('DIAGNOSTIC_TAIL'); "
        "raise SystemExit(1 if n==0 else 0)"
    )
    engine = harness.engine(
        [action(), review(), action(), review(), boss()],
        settings=config(gates=[command]),
    )
    assert engine.run(directory).status == "finished"
    provider = engine.provider
    assert isinstance(provider, ScriptedProvider)
    gate_payload = json.loads(provider.calls[1].messages[-1].content)["gate_results"][0]
    assert gate_payload["output_tail"].endswith("DIAGNOSTIC_TAIL\n")
    feedback = json.loads(provider.calls[2].messages[-1].content)["feedback"]
    assert "DIAGNOSTIC_TAIL" in feedback and "Gate failed" in feedback
    events = harness.ledger.list_events(engine.run_id)
    assert [e.verdict for e in events if e.type == "review"] == ["fail", "pass"]
    respawn = next(e for e in events if e.type == "respawn")
    assert respawn.type == "respawn" and "DIAGNOSTIC_TAIL" in respawn.feedback
    assert len(respawn.feedback) <= 200
    assert not any(e.type == "approval_requested" for e in events)


@pytest.mark.parametrize("allow", [True, False])
def test_model_shell_uses_approval_but_gates_do_not(
    harness: Harness, allow: bool
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [
            action("run_shell", command="printf approved > approved-ran"),
            action(),
            review(),
            boss(),
        ],
        approvals=[(allow, 0)],
        settings=config(gates=["printf gate"]),
    )
    assert engine.run(directory).status == "finished"
    assert (harness.root / "approved-ran").exists() == allow
    events = harness.ledger.list_events(engine.run_id)
    assert len([e for e in events if e.type == "approval_requested"]) == 1
    assert [e.answer for e in events if e.type == "approval_answered"] == [allow]
    assert len([e for e in events if e.type == "gate_result"]) == 2


def test_unfinished_attempt_cannot_be_cleared_by_passing_review(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [action("read_file", path="T01.txt"), review()],
        settings=config(max_steps=1, lives=1),
    )
    assert engine.run(directory).status == "paused"
    events = harness.ledger.list_events(engine.run_id)
    assert not any(e.type == "room_cleared" for e in events)
    assert any(e.type == "room_failed" and "max_steps" in e.reason for e in events)


def test_secret_rejected_before_bus_publish_or_sequence_consumption(
    harness: Harness,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    secret = os.environ["NVIDIA_API_KEY"]
    engine = harness.engine(
        [
            Completion(
                content=json.dumps(
                    {
                        "thought": secret,
                        "action": "finish",
                        "args": {},
                    }
                )
            )
        ]
    )
    with harness.bus.subscribe() as subscription:
        with pytest.raises(SecretLeakError):
            engine.run(directory)
        events = harness.ledger.list_events(engine.run_id)
        assert [subscription.get(timeout=0) for _ in events] == events
        assert all(secret not in e.model_dump_json() for e in events)
        last_seq = events[-1].seq
        engine.emit(
            Action(
                ts=harness.clock.now(),
                run_id=engine.run_id,
                seq=0,
                room_id="T01",
                thought="Safe",
                action="finish",
                args_summary="Ready",
            )
        )
        assert subscription.get(timeout=0).seq == last_seq + 1


def test_engine_dirty_tree_fails_before_models_or_run_records(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    (harness.root / "T01.txt").write_text("dirty")
    engine = harness.engine([])
    with pytest.raises(GitError, match="dirty"):
        engine.run(directory)
    assert isinstance(engine.provider, ScriptedProvider) and engine.provider.calls == []
    assert (
        harness.ledger.list_features(harness.ledger.add_project(harness.root).id) == []
    )


def patch_cli(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, provider: ScriptedProvider
) -> None:
    monkeypatch.chdir(harness.root)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    monkeypatch.setattr(
        cli, "TerminalResponder", lambda *args: ScriptedResponder("terminal", [])
    )
    monkeypatch.setattr(
        Ledger,
        "from_config",
        lambda *args, **kwargs: Ledger.open(
            harness.ledger_url, clock=kwargs["clock"], guard=kwargs["guard"]
        ),
    )


def test_cli_dirty_tree_nonzero_before_provider_and_allow_dirty_runs(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    (harness.root / "T01.txt").write_text("dirty")
    provider = ScriptedProvider([action(), review(), boss()])
    patch_cli(harness, monkeypatch, provider)
    constructed: list[bool] = []

    def create_provider(*args: Any, **kwargs: Any) -> ScriptedProvider:
        constructed.append(True)
        return provider

    monkeypatch.setattr(cli, "OpenAIProvider", create_provider)
    result = CliRunner().invoke(cli.create_app(), ["run", str(directory)])
    assert result.exit_code == 1 and "dirty" in result.output
    assert not constructed and not provider.calls and "Traceback" not in result.output
    result = CliRunner().invoke(
        cli.create_app(), ["run", str(directory), "--allow-dirty"]
    )
    assert result.exit_code == 0, result.output
    assert constructed == [True] and "finished" in result.output


def test_cli_pause_renders_rich_choices_and_aborts(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    provider = ScriptedProvider([action(), review(False)] * 3)
    patch_cli(harness, monkeypatch, provider)
    result = CliRunner().invoke(
        cli.create_app(), ["run", str(directory)], input="abort\n"
    )
    assert result.exit_code == 1, result.output
    assert "Run paused" in result.output
    assert all(
        choice in result.output for choice in ["retry", "skip", "abort", "aborted"]
    )
    assert "Traceback" not in result.output
    assert os.environ["NVIDIA_API_KEY"] not in result.output


@pytest.mark.parametrize(
    "ui_args,mode",
    [
        (["--ui"], "auto"),
        (["--ui=auto"], "auto"),
        (["--ui=browser"], "browser"),
        (["--ui=desktop"], "desktop"),
    ],
)
def test_cli_ui_uses_same_live_ledger_and_bus_without_sockets(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, ui_args: list[str], mode: str
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    provider = ScriptedProvider([action(), review(), boss()])
    patch_cli(harness, monkeypatch, provider)
    seen: list[UIServer] = []

    def start(server: UIServer, **kwargs: Any) -> None:
        assert kwargs["mode"] == mode
        assert (
            server.replay is None
            and server.ledger is not None
            and server.bus is not None
        )
        assert server.ledger.get_run(server.run_id).kind == "run"
        seen.append(server)

    monkeypatch.setattr(cli, "start_run_ui", start)
    result = CliRunner().invoke(cli.create_app(), ["run", str(directory), *ui_args])
    assert result.exit_code == 0, result.output
    assert len(seen) == 1 and seen[0].ledger is not None
    events = seen[0].ledger.list_events(seen[0].run_id)
    assert len(events) > 2 and events[-1].type == "run_finished"
    assert all(seen[0].control_token not in e.model_dump_json() for e in events)


@pytest.mark.parametrize("mode", ["browser", "desktop"])
def test_cli_bare_ui_uses_configured_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    import subprocess
    from types import SimpleNamespace

    from specvania.run.state import RunState

    directory = Path(__file__).parent / "fixtures/worlds/valid"
    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text(f'[ui]\nmode = "{mode}"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No UI test processes")
    )
    monkeypatch.setattr(
        cli,
        "GitOps",
        lambda root: SimpleNamespace(root=root, check=lambda **kwargs: None),
    )
    monkeypatch.setattr(
        cli, "OpenAIProvider", lambda *args, **kwargs: ScriptedProvider([])
    )

    def execute(directory: Path, **kwargs: Any) -> RunState:
        kwargs["on_started"]("run")
        return RunState(run_id="run", status="finished")

    monkeypatch.setattr(cli, "Engine", lambda **kwargs: SimpleNamespace(run=execute))
    modes: list[str] = []
    monkeypatch.setattr(
        cli, "start_run_ui", lambda server, **kwargs: modes.append(kwargs["mode"])
    )
    result = CliRunner().invoke(cli.create_app(), ["run", "--ui", str(directory)])
    assert result.exit_code == 0, result.output
    assert modes == [mode]


def test_live_ui_daemon_thread_uses_launch_ui_after_port_assignment(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine([action(), review(), boss()])
    assert engine.run(directory).run_id is not None
    server = UIServer(
        guard=harness.guard,
        clock=harness.clock,
        run_id=engine.run_id,
        ledger=harness.ledger,
        bus=harness.bus,
    )
    launched: list[tuple[int, str, str, str]] = []

    class Worker:
        def __init__(
            self, *, target: Callable[[], None], name: str, daemon: bool
        ) -> None:
            assert name == "specvania-ui" and daemon is True
            self.target = target

        def start(self) -> None:
            self.target()

    def serve(self: UIServer, *, on_started: Callable[[], None]) -> None:
        self.port = 12345
        on_started()

    def launch(port: int, view: str, control: str, **kwargs: Any) -> str:
        launched.append((port, view, control, kwargs["mode"]))
        return "browser"

    monkeypatch.setattr(cli, "Thread", Worker)
    monkeypatch.setattr(UIServer, "serve", serve)
    monkeypatch.setattr(cli, "launch_ui", launch)
    cli.start_run_ui(server, config=config(), mode="auto")
    assert launched == [(12345, server.view_token, server.control_token, "auto")]


def test_cli_invalid_world_and_missing_key_are_clear_nonzero_errors(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    provider = ScriptedProvider([])
    patch_cli(harness, monkeypatch, provider)
    monkeypatch.delenv("NVIDIA_API_KEY")
    result = CliRunner().invoke(cli.create_app(), ["run", str(directory)])
    assert result.exit_code == 1 and "NVIDIA_API_KEY missing" in result.output
    (directory / "tasks.md").write_text("### T01 · No area\n")
    result = CliRunner().invoke(cli.create_app(), ["run", str(directory)])
    assert (
        result.exit_code == 1 and "tasks.md:" in result.output and "L" in result.output
    )
    assert "Traceback" not in result.output and not provider.calls


def test_boss_receives_only_its_area_commits_in_execution_order(
    harness: Harness,
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T03", "A1", ("T01",)),
            ("T01", "A1", ()),
            ("T02", "A2", ("T01",)),
        ],
    )
    engine = harness.engine(
        success("T01") + success("T02") + [boss("S2")] + success("T03") + [boss("S1")]
    )
    assert engine.run(directory).status == "finished"
    provider = engine.provider
    assert isinstance(provider, ScriptedProvider)
    area2 = json.loads(provider.calls[6].messages[-1].content)
    area1 = json.loads(provider.calls[-1].messages[-1].content)
    assert "+built T02" in area2["diff"] and "+built T01" not in area2["diff"]
    assert "+built T02" not in area1["diff"]
    assert area1["diff"].index("+built T01") < area1["diff"].index("+built T03")


def test_live_ui_start_failure_is_reported_before_launching(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine([action(), review(), boss()])
    engine.run(directory)
    server = UIServer(
        guard=harness.guard,
        clock=harness.clock,
        run_id=engine.run_id,
        ledger=harness.ledger,
        bus=harness.bus,
        port=12345,
    )

    class Worker:
        def __init__(self, *, target: Callable[[], None], **kwargs: Any) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    def serve(self: UIServer, *, on_started: Callable[[], None]) -> None:
        raise UIServerError("Cannot bind local port")

    monkeypatch.setattr(cli, "Thread", Worker)
    monkeypatch.setattr(UIServer, "serve", serve)
    monkeypatch.setattr(
        cli, "launch_ui", lambda *args, **kwargs: pytest.fail("Must not launch")
    )
    with pytest.raises(UIServerError, match="Cannot bind"):
        cli.start_run_ui(server, config=config(), mode="browser")


def test_ui_launch_waits_for_startup_callback_without_socket_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    server = UIServer(guard=RedactionGuard(), clock=clock, run_id="ready", replay=())
    calls: list[str] = []
    pending: list[Callable[[], None]] = []

    class Worker:
        def __init__(self, *, target: Callable[[], None], **kwargs: Any) -> None:
            pending.append(target)

        def start(self) -> None:
            calls.append("thread started")

    def serve(self: UIServer, *, on_started: Callable[[], None]) -> None:
        self.port = 12345
        assert calls == ["thread started", "waiting"]
        calls.append("server started")
        on_started()

    def wait(seconds: float) -> None:
        calls.append("waiting")
        pending.pop()()

    def launch(*args: Any, **kwargs: Any) -> str:
        assert server.port == 12345 and calls[-1] == "server started"
        calls.append("launched")
        return "browser"

    monkeypatch.setattr(cli, "Thread", Worker)
    monkeypatch.setattr(clock, "sleep", wait)
    monkeypatch.setattr(UIServer, "serve", serve)
    monkeypatch.setattr(cli, "launch_ui", launch)
    cli.start_run_ui(server, config=config(), mode="browser")
    assert calls == ["thread started", "waiting", "server started", "launched"]


@pytest.mark.parametrize("ui_args", [["--ui=invalid"], ["--ui", "browser"]])
def test_cli_ui_validation_and_separate_mode_argument(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, ui_args: list[str]
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    provider = ScriptedProvider([action(), review(), boss()])
    patch_cli(harness, monkeypatch, provider)
    modes: list[str] = []
    monkeypatch.setattr(
        cli, "start_run_ui", lambda server, **kwargs: modes.append(kwargs["mode"])
    )
    result = CliRunner().invoke(cli.create_app(), ["run", *ui_args, str(directory)])
    if ui_args == ["--ui=invalid"]:
        assert result.exit_code == 2 and "Invalid value for '--ui'" in result.output
        assert not provider.calls
    else:
        assert result.exit_code == 0, result.output
        assert modes == ["browser"]


@pytest.mark.parametrize("allow", [True, False])
def test_live_desktop_answer_resolves_engine_shell_and_cancels_terminal(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    allow: bool,
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    provider = ScriptedProvider(
        [
            action("run_shell", command="printf desktop > desktop-approved"),
            action(),
            review(),
            boss(),
        ]
    )
    patch_cli(harness, monkeypatch, provider)
    # Match the other subprocess integration tests' virtual deadline: FakeClock
    # advances immediately while the real approved process still needs CPU time.
    monkeypatch.setattr(
        cli, "Engine", lambda **kwargs: Engine(**kwargs, shell_timeout=10000)
    )
    reader, writer = os.pipe()
    messages: list[str] = []
    terminal = TerminalResponder(reader, messages.append)
    monkeypatch.setattr(cli, "TerminalResponder", lambda *args: terminal)
    servers: list[UIServer] = []

    def start(server: UIServer, **kwargs: Any) -> None:
        assert server.bus is not None
        servers.append(server)
        publish = server.bus.publish

        def answer(event: Any) -> None:
            publish(event)
            if event.type == "approval_requested":
                pending = server.desktop_responder.pending
                assert (
                    pending is not None
                    and pending.command == "printf desktop > desktop-approved"
                )
                assert server.desktop_responder.answer(event.request_id, allow)

        monkeypatch.setattr(server.bus, "publish", answer)

    monkeypatch.setattr(cli, "start_run_ui", start)
    try:
        result = CliRunner().invoke(
            cli.create_app(), ["run", str(directory), "--ui=desktop"]
        )
        assert result.exit_code == 0, result.exception
        assert (harness.root / "desktop-approved").exists() == allow, (
            provider.calls[1].messages[-1].content
        )
        assert len(servers) == 1 and servers[0].ledger is not None
        events = servers[0].ledger.list_events(servers[0].run_id)
        assert [e.responder for e in events if e.type == "approval_answered"] == [
            "desktop window"
        ]
        assert [e.answer for e in events if e.type == "approval_answered"] == [allow]
        assert servers[0].desktop_responder.pending is None
        assert ("approved" if allow else "denied") + " in desktop window" in messages
        assert os.get_blocking(reader) is True
        observation = json.loads(provider.calls[1].messages[-1].content)
        assert observation["exit_code"] == (0 if allow else None)
        assert observation["timed_out"] is False
        assert all(servers[0].control_token not in e.model_dump_json() for e in events)
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize("failure", ["review", "warrior_router", "paladin_router"])
def test_skipped_partial_work_is_stashed_and_excluded_from_next_review_and_commit(
    harness: Harness,
    failure: str,
) -> None:
    directory = write_world(
        harness.root,
        [
            ("T01", "A1", ()),
            ("T02", "A1", ()),
        ],
    )

    def remove_file() -> Completion:
        (harness.root / "T01.txt").unlink()
        return action("write_file", path="partial.txt", content="skipped new file\n")

    script: list[Reply] = [
        action(
            "write_file",
            path=".gitignore",
            content=(harness.root / ".gitignore").read_text() + "extra-ignore/\n",
        ),
        remove_file,
    ]
    if failure == "warrior_router":
        script += [ProviderError(status=500), ProviderError(status=500)]
    elif failure == "paladin_router":
        script += [action(), ProviderError(status=500), ProviderError(status=500)]
    else:
        script += [action(), review(False, "Fix this " + "feedback " * 40)]
    script += success("T02")
    engine = harness.engine(
        script, settings=config(lives=1), decide=lambda pause: "skip"
    )
    assert engine.run(directory).status == "finished"
    events = harness.ledger.list_events(engine.run_id)
    skipped = next(e for e in events if e.type == "room_skipped")
    assert skipped.type == "room_skipped" and len(skipped.reason) <= 200
    stash = git(harness.root, "rev-parse", "refs/stash")
    assert stash in skipped.reason
    assert f"specvania {engine.run_id} T01 skipped" in git(
        harness.root, "stash", "list"
    )
    saved = git(harness.root, "stash", "show", "--include-untracked", "-p", stash)
    assert "-original" in saved and "+extra-ignore/" in saved
    assert "+skipped new file" in saved
    provider = engine.provider
    assert isinstance(provider, ScriptedProvider)
    next_review = json.loads(provider.calls[-1].messages[-1].content)
    assert next_review["room"]["id"] == "T02"
    assert "+built T02" in next_review["diff"]
    assert all(
        path not in next_review["diff"]
        for path in ["T01.txt", "partial.txt", ".gitignore"]
    )
    commit = next(e.commit for e in events if e.type == "room_cleared")
    assert git(harness.root, "show", "--format=", "--name-only", commit) == "T02.txt"
    assert git(harness.root, "show", f"{commit}:T01.txt") == "original"
    assert "partial.txt" not in git(harness.root, "ls-tree", "--name-only", commit)
    assert git(harness.root, "status", "--porcelain") == ""
    # Recovery is explicit and happens only inside this disposable repository.
    git(harness.root, "stash", "apply", stash)
    assert not (harness.root / "T01.txt").exists()
    assert (harness.root / "partial.txt").read_text() == "skipped new file\n"


def test_abort_preserves_partial_work_without_stashing(harness: Harness) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine(
        [
            action("write_file", path="T01.txt", content="inspect this\n"),
            action("write_file", path="partial.txt", content="keep this\n"),
            action(),
            review(False),
        ],
        settings=config(lives=1),
        decide=lambda pause: "abort",
    )
    assert engine.run(directory).status == "aborted"
    assert (harness.root / "T01.txt").read_text() == "inspect this\n"
    assert (harness.root / "partial.txt").read_text() == "keep this\n"
    assert git(harness.root, "stash", "list") == ""


def test_engine_project_records_local_origin_without_network(
    harness: Harness, tmp_path: Path
) -> None:
    directory = write_world(harness.root, [("T01", "A1", ())])
    origin = str(tmp_path / "local-origin.git")
    git(harness.root, "remote", "add", "origin", origin)
    engine = harness.engine([action(), review(), boss()])
    engine.run(directory)
    run = harness.ledger.get_run(engine.run_id)
    feature = harness.ledger.get_feature(run.feature_id)
    project = harness.ledger.get_project(feature.project_id)
    assert project.repo_root == str(harness.root.resolve())
    assert project.origin_url == origin


@pytest.mark.parametrize("priced", [False, True])
def test_run_started_supplies_configured_lives_and_optional_prices(
    harness: Harness, priced: bool
) -> None:
    if priced:
        (harness.root / ".specvania/pricing.toml").write_text(
            '[[prices]]\nmodel = "w1"\ninput_per_mtok = 2.5\n'
            'output_per_mtok = 5\ncurrency = "USD"\n'
            'source_url = "https://example.invalid/pricing"\n'
            'checked_on = "2026-10-12"\n'
        )
    directory = write_world(harness.root, [("T01", "A1", ())])
    engine = harness.engine([*success("T01"), boss()], settings=config(lives=5))
    engine.run(directory)
    started = next(
        event
        for event in harness.ledger.list_events(engine.run_id)
        if event.type == "run_started"
    )
    assert started.lives == 5
    if priced:
        assert len(started.prices) == 1
        price = started.prices[0]
        assert price.model == "w1"
        assert str(price.input_per_mtok) == "2.5"
        assert price.output_per_mtok == 5
        assert price.currency == "USD"
        assert price.source_url == "https://example.invalid/pricing"
        assert price.checked_on is not None
        assert price.checked_on.isoformat() == "2026-10-12"
    else:
        assert started.prices == ()
