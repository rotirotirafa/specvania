"""T13 evidence over real temporary Git and both Ledger adapters, offline."""

import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specvania import cli
from specvania.bugs import BugError, Bugs, source_bug
from specvania.clock import FakeClock
from specvania.config import Config, LimitsConfig
from specvania.ledger import repo as ledger_repo
from specvania.ledger.repo import (
    Bug,
    Ledger,
    LedgerError,
    LedgerNotFoundError,
    RunIdTakenError,
)
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.run import engine as run_engine
from specvania.run.bus import EventBus
from specvania.run.engine import Engine, EngineError, PauseDecision
from specvania.run.events import RedactionGuard, RunPaused, SpecWritten
from specvania.run.jsonl import decode_events, encode_events
from specvania.sdd.planner import PlanError, plan


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def reply(payload: object) -> Completion:
    return Completion(content=json.dumps(payload), tokens_in=3, tokens_out=4)


def action(name: str = "finish", **args: str) -> Completion:
    return reply({"thought": "Working", "action": name, "args": args})


def review(passed: bool = True, feedback: str = "Verified") -> Completion:
    return reply(
        {
            "verdict": "pass" if passed else "fail",
            "checklist": [
                {"item": "Work is verified", "ok": passed, "note": "checked"}
            ],
            "feedback": feedback,
        }
    )


def boss(passed: bool = True) -> Completion:
    return reply([{"scenario": "S1", "passed": passed, "reason": "Acceptance"}])


def world_reply() -> Completion:
    return reply(
        {
            "spec_md": (
                "### S1 · Acceptance\n```gherkin\nGiven x\nWhen y\nThen z\n```\n"
            ),
            "tasks_md": (
                "## A1 · Area\nboss: S1\n\n### T01 · Repair\n"
                "depends_on: none\ncovers: S1\nfiles: work.txt\n\n"
                "Checklist:\n- [ ] Work is verified\n"
            ),
        }
    )


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.pg)])
def ledger_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgres":
        url = os.environ.get("SPECVANIA_TEST_DATABASE_URL")
        if not url:
            pytest.skip(
                "Postgres requires SPECVANIA_TEST_DATABASE_URL; reviewer runs G9"
            )
        return url
    return f"sqlite:///{tmp_path / 'ledger.db'}"


@dataclass
class Harness:
    root: Path
    url: str
    ledger: Ledger
    clock: FakeClock
    guard: RedactionGuard
    project_id: str

    def engine(
        self,
        script: Sequence[Completion | ProviderError],
        decision: PauseDecision | None = None,
    ) -> Engine:
        return Engine(
            root=self.root,
            ledger=self.ledger,
            clock=self.clock,
            bus=EventBus(self.guard),
            config=Config(limits=LimitsConfig(lives=1)),
            provider=ScriptedProvider(script),
            decide=lambda pause: decision,
        )

    def bug(self, title: str = "Repair bug", body: str = "Broken assertion") -> Bug:
        return self.ledger.add_bug(self.project_id, title, body=body)

    def world(self) -> Path:
        payload = json.loads(world_reply().content)
        directory = self.root / "world"
        directory.mkdir()
        for name in ("spec", "tasks"):
            (directory / f"{name}.md").write_text(payload[f"{name}_md"])
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "world")
        return directory


@pytest.fixture
def harness(tmp_path: Path, ledger_url: str) -> Iterator[Harness]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Specvania test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".specvania").mkdir()
    (root / ".specvania/config.toml").write_text("[limits]\nlives = 1\n")
    (root / ".gitignore").write_text(".specvania/ledger.db*\n.specvania/runs/\n")
    (root / "work.txt").write_text("original\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    clock = FakeClock()
    guard = RedactionGuard()
    guard.register(os.environ["NVIDIA_API_KEY"])
    with Ledger.open(ledger_url, clock=clock, guard=guard) as ledger:
        yield Harness(
            root, ledger_url, ledger, clock, guard, ledger.add_project(root).id
        )


def patch_cli(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(harness.root)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    monkeypatch.setattr(
        Ledger,
        "from_config",
        lambda *args, **kwargs: Ledger.open(
            harness.url, clock=kwargs["clock"], guard=kwargs["guard"]
        ),
    )


def test_skipped_room_opens_one_linked_bug_with_full_feedback_and_stash(
    harness: Harness,
) -> None:
    directory = harness.world()
    feedback = "Fix the assertion " + "details " * 50
    engine = harness.engine(
        [
            action("write_file", path="work.txt", content="partial"),
            action(),
            review(False, feedback),
        ],
        "skip",
    )
    state = engine.run(directory)
    run = harness.ledger.get_run(engine.run_id)
    bugs = harness.ledger.list_bugs(harness.project_id)
    assert state.status == "finished" and len(bugs) == 1
    bug = bugs[0]
    assert (bug.feature_id, bug.run_id, bug.room_id) == (run.feature_id, run.id, "T01")
    assert bug.source == "room_skipped" and bug.body == feedback
    assert "T01" in bug.title and "Repair" in bug.title
    events = harness.ledger.list_events(run.id)
    skipped = next(e for e in events if e.type == "room_skipped")
    opened = [e for e in events if e.type == "bug_opened"]
    assert len(opened) == 1 and opened[0].bug_id == bug.id
    assert opened[0].feature == "world" and opened[0].room_id == "T01"
    assert opened[0].body == feedback[:200] and skipped.seq < opened[0].seq
    assert "saved to stash" in skipped.reason
    assert (harness.root / "work.txt").read_text() == "original\n"


def test_failed_boss_opens_one_bug_per_failed_scenario_before_pause(
    harness: Harness,
) -> None:
    directory = harness.world()
    spec = directory / "spec.md"
    spec.write_text(
        spec.read_text()
        + "\n### S2 · Second\n```gherkin\nGiven x\nWhen y\nThen z\n```\n"
        + "\n### S3 · Third\n```gherkin\nGiven x\nWhen y\nThen z\n```\n"
    )
    rooms = directory / "tasks.md"
    rooms.write_text(
        rooms.read_text()
        .replace("boss: S1", "boss: S1, S2, S3")
        .replace("covers: S1", "covers: S1, S2, S3")
    )
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "boss scenarios")
    engine = harness.engine(
        [
            action(),
            review(),
            reply(
                [
                    {"scenario": "S1", "passed": False, "reason": "Missing assertion"},
                    {"scenario": "S2", "passed": True, "reason": "Verified"},
                    {"scenario": "S3", "passed": False, "reason": "Missing acceptance"},
                ]
            ),
        ]
    )
    observed: list[RunPaused] = []

    def pause(event: RunPaused) -> None:
        assert len(harness.ledger.list_bugs(harness.project_id)) == 2
        observed.append(event)

    engine.decide = pause
    assert engine.run(directory).status == "paused"
    assert len(observed) == 1
    bugs = harness.ledger.list_bugs(harness.project_id)
    assert {b.title for b in bugs} == {"Boss A1: S1", "Boss A1: S3"}
    assert {b.body for b in bugs} == {"Missing assertion", "Missing acceptance"}
    run = harness.ledger.get_run(engine.run_id)
    assert all(b.feature_id == run.feature_id and b.run_id == run.id for b in bugs)
    assert all(b.source == "boss_scenario" for b in bugs)
    events = harness.ledger.list_events(run.id)
    opened = [e for e in events if e.type == "bug_opened"]
    result = next(e for e in events if e.type == "boss_result")
    pause_event = next(e for e in events if e.type == "run_paused")
    assert len(opened) == 2 and all(
        result.seq < e.seq < pause_event.seq for e in opened
    )


@pytest.mark.parametrize("decision", ["skip", "abort", None])
def test_cli_plan_from_bug_and_run_lifecycle(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    decision: PauseDecision | None,
) -> None:
    # None here is the success case; skip/abort exercise non-cleared outcomes.
    bug = harness.bug(body="Detailed failure context " + "x" * 250)
    patch_cli(harness, monkeypatch)
    provider = ScriptedProvider(
        [reply({"needs_clarification": False, "questions": []}), world_reply()]
    )
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    result = CliRunner().invoke(cli.create_app(), ["plan", "--from-bug", bug.id])
    assert result.exit_code == 0, result.output
    assert len(provider.calls) == 2
    for call in provider.calls:
        context = json.loads(call.messages[1].content)
        assert context["idea"] == bug.title
        assert (
            context["bug"]["body"] == bug.body and context["bug"]["title"] == bug.title
        )
    feature = next(
        f
        for f in harness.ledger.list_features(harness.project_id)
        if f.slug == "repair-bug"
    )
    plan_run = harness.ledger.list_runs(feature.id)[0]
    assert plan_run.kind == "plan"
    written = next(
        e for e in harness.ledger.list_events(plan_run.id) if e.type == "spec_written"
    )
    assert written.from_bug_id == bug.id
    assert harness.ledger.get_bug(bug.id).status == "open"
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "plan")
    script = [action(), review(decision is None)]
    if decision is None:
        script.append(boss())
    provider = ScriptedProvider(script)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    result = CliRunner().invoke(
        cli.create_app(),
        ["run", str(harness.root / ".specvania/specs/repair-bug")],
        input=f"{decision}\n" if decision is not None else "",
    )
    assert result.exit_code == (1 if decision == "abort" else 0), result.output
    run = next(r for r in harness.ledger.list_runs(feature.id) if r.kind == "run")
    persisted = harness.ledger.get_bug(bug.id)
    events = harness.ledger.list_events(run.id)
    closed = [e for e in events if e.type == "bug_closed"]
    if decision is None:
        assert persisted.status == "fixed" and persisted.fixed_by_run == run.id
        assert len(closed) == 1 and closed[0].fixed_by_run == run.id
        assert closed[0].bug_id == bug.id and closed[0].status == "fixed"
        assert run.status == "finished" and run.rooms_cleared == 1
        assert (
            "Repair bug"
            not in CliRunner().invoke(cli.create_app(), ["bug", "list"]).output
        )
        bestiary = CliRunner().invoke(cli.create_app(), ["bug", "list", "--all"])
        assert "Repair bug" in bestiary.output and "fixed" in bestiary.output
        # Reopening storage proves the relation and closure are durable.
        with Ledger.open(
            harness.url, clock=harness.clock, guard=harness.guard
        ) as ledger:
            assert ledger.get_bug(bug.id) == persisted
            assert ledger.list_events(run.id) == events
    else:
        assert persisted.status == "open" and persisted.fixed_by_run is None
        assert not closed
        assert run.rooms_cleared == 0
        if decision == "skip":
            assert run.status == "finished" and any(
                e.type == "room_skipped" for e in events
            )


def test_run_start_emits_only_its_features_open_bugs_after_world(
    harness: Harness,
) -> None:
    directory = harness.world()
    feature = harness.ledger.add_feature(harness.project_id, "world", "World")
    other = harness.ledger.add_feature(harness.project_id, "other", "Other")
    open_bug = harness.ledger.add_bug(
        harness.project_id,
        "Monster",
        feature_id=feature.id,
        room_id="T01",
        body="context",
    )
    closed = harness.ledger.add_bug(harness.project_id, "Closed", feature_id=feature.id)
    harness.ledger.close_bug(closed.id, status="wontfix")
    harness.ledger.add_bug(harness.project_id, "Other", feature_id=other.id)
    harness.bug("Unassigned")
    engine = harness.engine([action(), review(), boss()])
    assert engine.run(directory).status == "finished"
    events = harness.ledger.list_events(engine.run_id)
    assert [e.type for e in events[:4]] == [
        "run_started",
        "world_loaded",
        "bug_opened",
        "room_entered",
    ]
    opened = [e for e in events if e.type == "bug_opened"]
    assert len(opened) == 1 and opened[0].bug_id == open_bug.id
    assert opened[0].feature == "world" and opened[0].room_id == "T01"
    assert opened[0].source == "user" and opened[0].body == "context"


def test_cli_bug_add_close_and_bestiary_write_no_runs_or_events(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_cli(harness, monkeypatch)
    monkeypatch.delenv("NVIDIA_API_KEY")
    feature = harness.ledger.add_feature(harness.project_id, "invoices", "Invoices")
    runner = CliRunner()
    app = cli.create_app()
    result = runner.invoke(
        app,
        [
            "bug",
            "add",
            "[bold]Missing invoice",
            "--feature",
            "invoices",
            "--room",
            "T01",
        ],
    )
    assert result.exit_code == 0, result.output
    bug = harness.ledger.list_bugs(harness.project_id)[0]
    assert bug.id in result.output and "[bold]" in result.output
    assert bug.title == "[bold]Missing invoice" and bug.source == "user"
    assert bug.feature_id == feature.id and bug.room_id == "T01" and bug.run_id is None
    result = runner.invoke(app, ["bug", "list"])
    assert result.exit_code == 0 and "Bestiary" in result.output
    assert "Missing invoice" in result.output and "invoices" in result.output
    assert "T01" in result.output and "open" in result.output
    result = runner.invoke(app, ["bug", "close", bug.id, "--wontfix"])
    assert result.exit_code == 0 and "wontfix" in result.output
    closed = harness.ledger.get_bug(bug.id)
    assert (
        closed.status == "wontfix"
        and closed.fixed_by_run is None
        and closed.run_id is None
    )
    assert "Missing invoice" not in runner.invoke(app, ["bug", "list"]).output
    result = runner.invoke(app, ["bug", "list", "--all"])
    assert "Missing invoice" in result.output and "wontfix" in result.output
    assert harness.ledger.list_runs(feature.id) == []
    # Also prove commands do not append to an existing run.
    directory = harness.world()
    engine = harness.engine([action(), review(), boss()])
    engine.run(directory)
    events = harness.ledger.list_events(engine.run_id)
    result = runner.invoke(app, ["bug", "add", "Standalone"])
    assert result.exit_code == 0
    standalone = next(
        b
        for b in harness.ledger.list_bugs(harness.project_id)
        if b.title == "Standalone"
    )
    assert standalone.feature_id is None and standalone.room_id is None
    assert (
        runner.invoke(app, ["bug", "close", standalone.id, "--wontfix"]).exit_code == 0
    )
    assert harness.ledger.list_events(engine.run_id) == events


@pytest.mark.parametrize(
    "arguments",
    [
        ["bug", "add", ""],
        ["bug", "add", "Bug", "--feature", "missing"],
        ["bug", "add", "Bug", "--room", "bad"],
        ["bug", "close", "missing", "--wontfix"],
        ["bug", "close", "missing"],
        ["plan", "--from-bug", "missing", "--no-clarify"],
    ],
)
def test_cli_bug_errors_are_clear_and_nonzero(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    patch_cli(harness, monkeypatch)
    provider = ScriptedProvider([])
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    result = CliRunner().invoke(cli.create_app(), arguments)
    assert result.exit_code == 1, result.output
    assert result.output and "Traceback" not in result.output and not provider.calls
    assert os.environ["NVIDIA_API_KEY"] not in result.output
    assert harness.ledger.list_bugs(harness.project_id) == []


def test_source_bug_rejects_closed_and_foreign_project(
    harness: Harness, tmp_path: Path
) -> None:
    bug = harness.bug()
    harness.ledger.close_bug(bug.id, status="wontfix")
    with pytest.raises(BugError, match="open bug"):
        source_bug(harness.ledger, harness.root, bug.id)
    project = harness.ledger.add_project(tmp_path / "other")
    foreign = harness.ledger.add_bug(project.id, "Foreign")
    with pytest.raises(BugError, match="another project"):
        source_bug(harness.ledger, harness.root, foreign.id)
    with pytest.raises(BugError, match="another project"):
        Bugs(harness.ledger, harness.project_id).close_wontfix(foreign.id)
    assert foreign not in Bugs(harness.ledger, harness.project_id).list()


def test_optional_source_bug_event_reads_old_stream_and_roundtrips() -> None:
    old = SpecWritten(
        ts=FakeClock().now(),
        run_id="plan",
        seq=1,
        feature="fix",
        spec_version="version",
    )
    payload = old.model_dump(mode="json")
    del payload["from_bug_id"]
    assert SpecWritten.model_validate(payload).from_bug_id is None
    linked = old.model_copy(update={"from_bug_id": "bug-id"})
    guard = RedactionGuard()
    assert list(decode_events(encode_events([linked], guard), guard)) == [linked]


def test_fix_run_closes_each_open_source_bug_once_and_preserves_wontfix(
    harness: Harness,
) -> None:
    bugs = [harness.bug(body=f"Failure {number}") for number in range(3)]
    for bug in [bugs[0], bugs[0], bugs[1], bugs[2]]:
        planned = plan(
            root=harness.root,
            idea="",
            from_bug_id=bug.id,
            no_clarify=True,
            config=Config(),
            provider=ScriptedProvider([world_reply()]),
            ledger=harness.ledger,
            clock=harness.clock,
            guard=harness.guard,
            answer=lambda question: "",
        )
    harness.ledger.close_bug(bugs[2].id, status="wontfix")
    unrelated = harness.bug("Unrelated")
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "plans")
    # Resolve links from persisted plan events in a newly opened Ledger.
    with Ledger.open(harness.url, clock=harness.clock, guard=harness.guard) as reopened:
        engine = Engine(
            root=harness.root,
            ledger=reopened,
            clock=harness.clock,
            bus=EventBus(harness.guard),
            config=Config(),
            provider=ScriptedProvider([action(), review(), boss()]),
        )
        assert engine.run(planned.directory).status == "finished"
        events = reopened.list_events(engine.run_id)
        closed = [e for e in events if e.type == "bug_closed"]
        assert len(closed) == 2 and {e.bug_id for e in closed} == {
            b.id for b in bugs[:2]
        }
        assert all(e.fixed_by_run == engine.run_id for e in closed)
        for bug in bugs[:2]:
            fixed = reopened.get_bug(bug.id)
            assert fixed.status == "fixed" and fixed.fixed_by_run == engine.run_id
        assert reopened.get_bug(bugs[2].id).status == "wontfix"
        assert reopened.get_bug(unrelated.id).status == "open"
        assert Bugs(reopened, harness.project_id).list() == [unrelated]
        assert len(Bugs(reopened, harness.project_id).list(include_closed=True)) == 4
    again = harness.engine([action(), review(), boss()])
    again.run(planned.directory)
    assert not any(
        e.type == "bug_closed" for e in harness.ledger.list_events(again.run_id)
    )


def test_paused_fix_run_does_not_close_source_bug(harness: Harness) -> None:
    bug = harness.bug()
    planned = plan(
        root=harness.root,
        idea="",
        from_bug_id=bug.id,
        no_clarify=True,
        config=Config(),
        provider=ScriptedProvider([world_reply()]),
        ledger=harness.ledger,
        clock=harness.clock,
        guard=harness.guard,
        answer=lambda question: "",
    )
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "plan")
    engine = harness.engine([action(), review(False)])
    assert engine.run(planned.directory).status == "paused"
    assert harness.ledger.get_bug(bug.id).status == "open"
    assert not any(
        e.type == "bug_closed" for e in harness.ledger.list_events(engine.run_id)
    )


def test_provider_failure_during_boss_opens_no_scenario_bug(harness: Harness) -> None:
    engine = harness.engine([action(), review(), ProviderError(status=401)])
    assert engine.run(harness.world()).status == "paused"
    assert harness.ledger.list_bugs(harness.project_id) == []


def test_skipped_unfinished_attempt_uses_paladins_feedback_as_bug_body(
    harness: Harness,
) -> None:
    engine = harness.engine(
        [
            action("write_file", path="work.txt", content="partial"),
            review(False, "Paladin feedback"),
        ],
        "skip",
    )
    engine.config = Config(limits=LimitsConfig(lives=1, max_steps=1))
    engine.run(harness.world())
    bugs = harness.ledger.list_bugs(harness.project_id)
    assert len(bugs) == 1 and bugs[0].body == "Paladin feedback"
    skipped = next(
        e for e in harness.ledger.list_events(engine.run_id) if e.type == "room_skipped"
    )
    assert "max_steps" in skipped.reason


def test_boss_retry_failure_reuses_one_bug_per_scenario(harness: Harness) -> None:
    choices: list[PauseDecision] = ["retry", "skip"]
    decisions = iter(choices)
    engine = harness.engine([action(), review(), boss(False), boss(False)])
    engine.decide = lambda pause: next(decisions)
    assert engine.run(harness.world()).status == "finished"
    bugs = harness.ledger.list_bugs(harness.project_id, include_closed=True)
    assert len(bugs) == 1
    assert bugs[0].title == "Boss A1: S1" and bugs[0].status == "open"
    assert bugs[0].run_id == engine.run_id and bugs[0].fixed_by_run is None
    events = harness.ledger.list_events(engine.run_id)
    opened = [e for e in events if e.type == "bug_opened"]
    assert len(opened) == 1 and opened[0].bug_id == bugs[0].id
    assert len([e for e in events if e.type == "boss_result" and not e.passed]) == 2
    assert not any(e.type == "bug_closed" for e in events)


def test_boss_retry_pass_closes_bug_fixed_by_current_run(harness: Harness) -> None:
    engine = harness.engine([action(), review(), boss(False), boss()], "retry")
    assert engine.run(harness.world()).status == "finished"
    bugs = harness.ledger.list_bugs(harness.project_id, include_closed=True)
    assert len(bugs) == 1
    assert bugs[0].status == "fixed" and bugs[0].fixed_by_run == engine.run_id
    assert harness.ledger.list_bugs(harness.project_id) == []
    events = harness.ledger.list_events(engine.run_id)
    opened = [e for e in events if e.type == "bug_opened"]
    closed = [e for e in events if e.type == "bug_closed"]
    passed = next(e for e in events if e.type == "boss_result" and e.passed)
    assert len(opened) == len(closed) == 1
    assert closed[0].bug_id == opened[0].bug_id == bugs[0].id
    assert closed[0].status == "fixed" and closed[0].fixed_by_run == engine.run_id
    assert passed.seq < closed[0].seq


def test_failed_boss_skip_keeps_scenario_bug_open(harness: Harness) -> None:
    engine = harness.engine([action(), review(), boss(False)], "skip")
    assert engine.run(harness.world()).status == "finished"
    bugs = harness.ledger.list_bugs(harness.project_id)
    assert len(bugs) == 1 and bugs[0].status == "open"
    assert bugs[0].fixed_by_run is None
    events = harness.ledger.list_events(engine.run_id)
    opened = [e for e in events if e.type == "bug_opened"]
    assert len(opened) == 1 and opened[0].bug_id == bugs[0].id
    assert not any(e.type == "bug_closed" for e in events)


def test_boss_retry_closes_only_passing_scenarios_before_another_pause(
    harness: Harness,
) -> None:
    directory = harness.world()
    spec = directory / "spec.md"
    spec.write_text(
        spec.read_text()
        + "\n### S2 · Second\n```gherkin\nGiven x\nWhen y\nThen z\n```\n"
    )
    rooms = directory / "tasks.md"
    rooms.write_text(
        rooms.read_text()
        .replace("boss: S1", "boss: S1, S2")
        .replace("covers: S1", "covers: S1, S2")
    )
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "boss scenarios")
    results = [
        reply(
            [
                {"scenario": "S1", "passed": passed, "reason": "First scenario"},
                {"scenario": "S2", "passed": False, "reason": "Second scenario"},
            ]
        )
        for passed in [False, True]
    ]
    choices: list[PauseDecision] = ["retry", "skip"]
    decisions = iter(choices)
    engine = harness.engine([action(), review(), *results])
    engine.decide = lambda pause: next(decisions)
    assert engine.run(directory).status == "finished"
    bugs = {
        b.title: b
        for b in harness.ledger.list_bugs(harness.project_id, include_closed=True)
    }
    assert len(bugs) == 2
    assert bugs["Boss A1: S1"].status == "fixed"
    assert bugs["Boss A1: S1"].fixed_by_run == engine.run_id
    assert bugs["Boss A1: S2"].status == "open"
    events = harness.ledger.list_events(engine.run_id)
    assert len([e for e in events if e.type == "bug_opened"]) == 2
    closed = [e for e in events if e.type == "bug_closed"]
    pauses = [e for e in events if e.type == "run_paused"]
    assert len(closed) == 1 and closed[0].bug_id == bugs["Boss A1: S1"].id
    assert pauses[0].seq < closed[0].seq < pauses[1].seq


def test_boss_retry_does_not_close_scenario_bug_from_previous_run(
    harness: Harness,
) -> None:
    directory = harness.world()
    engine = harness.engine([action(), review(), boss(False)], "skip")
    engine.run(directory)
    original_run_id = engine.run_id
    original = harness.ledger.list_bugs(harness.project_id)[0]
    # The same Engine can start another run; bugs are scoped to its current fight.
    engine.provider = ScriptedProvider([action(), review(), boss(False), boss()])
    engine.decide = lambda pause: "retry"
    assert engine.run(directory).status == "finished"
    assert engine.run_id != original_run_id
    assert harness.ledger.get_bug(original.id) == original
    bugs = harness.ledger.list_bugs(harness.project_id, include_closed=True)
    assert len(bugs) == 2
    fixed = next(b for b in bugs if b.id != original.id)
    assert fixed.status == "fixed" and fixed.fixed_by_run == engine.run_id
    assert fixed.run_id == engine.run_id
    closed = [
        e for e in harness.ledger.list_events(engine.run_id) if e.type == "bug_closed"
    ]
    assert len(closed) == 1 and closed[0].bug_id == fixed.id


def test_summary_hook_sees_source_bug_closure_before_finished_summary(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bug = harness.bug()
    planned = plan(
        root=harness.root,
        idea="",
        from_bug_id=bug.id,
        no_clarify=True,
        config=Config(),
        provider=ScriptedProvider([world_reply()]),
        ledger=harness.ledger,
        clock=harness.clock,
        guard=harness.guard,
        answer=lambda question: "",
    )
    git(harness.root, "add", ".")
    git(harness.root, "commit", "-m", "plan")
    original = run_engine.write_summary
    seen: list[str] = []

    def write(root: Path, ledger: Ledger, run_id: str, *, config: Config) -> Path:
        assert ledger.get_run(run_id).status == "finished"
        fixed = ledger.get_bug(bug.id)
        assert fixed.status == "fixed" and fixed.fixed_by_run == run_id
        closed = [e for e in ledger.list_events(run_id) if e.type == "bug_closed"]
        assert len(closed) == 1 and closed[0].bug_id == bug.id
        seen.append(run_id)
        return original(root, ledger, run_id, config=config)

    monkeypatch.setattr(run_engine, "write_summary", write)
    engine = harness.engine([action(), review(), boss()])
    assert engine.run(planned.directory).status == "finished"
    assert seen == [engine.run_id]
    summary = harness.root / f".specvania/runs/{engine.run_id}/summary.md"
    assert "Status: finished" in summary.read_text()


def test_history_shows_cli_and_engine_open_bugs_and_excludes_closed(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = harness.world()
    harness.ledger.add_feature(harness.project_id, "world", "World")
    patch_cli(harness, monkeypatch)
    runner = CliRunner()
    app = cli.create_app()
    added = runner.invoke(
        app, ["bug", "add", "Manual failure", "--feature", "world", "--room", "T01"]
    )
    assert added.exit_code == 0, added.output
    manual = harness.ledger.list_bugs(harness.project_id)[0]
    engine = harness.engine([action(), review(False, "Broken assertion")], "skip")
    engine.run(directory)
    skipped = next(
        b
        for b in harness.ledger.list_bugs(harness.project_id)
        if b.source == "room_skipped"
    )
    overview = runner.invoke(app, ["history"])
    detail = runner.invoke(app, ["history", "world"])
    assert overview.exit_code == detail.exit_code == 0
    assert "world | planned | 1 | 1 | 2" in overview.output
    assert manual.id in detail.output and manual.title in detail.output
    assert skipped.id in detail.output and skipped.title in detail.output
    assert runner.invoke(app, ["bug", "close", manual.id, "--wontfix"]).exit_code == 0
    overview = runner.invoke(app, ["history"])
    detail = runner.invoke(app, ["history", "world"])
    assert overview.exit_code == detail.exit_code == 0
    assert "world | planned | 1 | 1 | 1" in overview.output
    assert manual.id not in detail.output and manual.title not in detail.output
    assert skipped.id in detail.output and skipped.title in detail.output


def reserve_collision_id(harness: Harness) -> tuple[str, str]:
    # The Postgres fixture shares storage across tests and gate reruns. Choose
    # an unused timestamp for both deterministic suffixes before reserving one.
    while True:
        prefix = f"{harness.clock.now():%Y%m%d-%H%M%S}"
        identifiers = [f"{prefix}-aaaa", f"{prefix}-bbbb"]
        taken = False
        for identifier in identifiers:
            try:
                harness.ledger.get_run(identifier)
            except LedgerNotFoundError:
                continue
            taken = True
        if not taken:
            break
        harness.clock.sleep(1)
    feature = harness.ledger.add_feature(harness.project_id, "occupied", "Occupied")
    version = harness.ledger.add_spec_version(feature.id, "spec", "rooms")
    harness.ledger.create_run(feature.id, version.id, "original", run_id=identifiers[0])
    return identifiers[0], identifiers[1]


def test_ledger_run_collision_has_specific_error_and_preserves_original(
    harness: Harness,
) -> None:
    occupied, fresh = reserve_collision_id(harness)
    original = harness.ledger.get_run(occupied)
    with pytest.raises(RunIdTakenError):
        harness.ledger.create_run(
            original.feature_id,
            original.spec_version_id,
            "new",
            run_id=occupied,
        )
    assert harness.ledger.get_run(occupied) == original
    assert harness.ledger.list_events(occupied) == []
    with pytest.raises(LedgerError) as caught:
        harness.ledger.create_run(original.feature_id, "missing", "new", run_id=fresh)
    assert type(caught.value) is LedgerError


def test_engine_retries_colliding_suffix_before_events_or_branch(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = harness.world()
    occupied, fresh = reserve_collision_id(harness)
    original = harness.ledger.get_run(occupied)
    suffixes = iter(["aaaa", "aaaa", "bbbb"])
    generated: list[int] = []

    def suffix(nbytes: int) -> str:
        generated.append(nbytes)
        return next(suffixes)

    monkeypatch.setattr(ledger_repo.secrets, "token_hex", suffix)
    engine = harness.engine([action(), review(), boss()])
    assert engine.run(directory).status == "finished"
    assert generated == [2, 2, 2] and engine.run_id == fresh
    assert harness.ledger.get_run(fresh).branch == f"specvania/{fresh}"
    assert git(harness.root, "branch", "--show-current") == f"specvania/{fresh}"
    assert f"specvania/{occupied}" not in git(harness.root, "branch", "--list")
    events = harness.ledger.list_events(fresh)
    assert events and all(event.run_id == fresh for event in events)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert harness.ledger.get_run(occupied) == original
    assert harness.ledger.list_events(occupied) == []


def test_plan_retries_collision_and_restamps_all_buffered_events(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupied, fresh = reserve_collision_id(harness)
    original = harness.ledger.get_run(occupied)
    bug = harness.bug()
    suffixes = iter(["aaaa", "aaaa", "bbbb"])
    generated: list[int] = []

    def suffix(nbytes: int) -> str:
        generated.append(nbytes)
        return next(suffixes)

    monkeypatch.setattr(ledger_repo.secrets, "token_hex", suffix)
    provider = ScriptedProvider(
        [
            reply(
                {
                    "needs_clarification": True,
                    "questions": [
                        {"id": "C1", "question": "Acceptance?", "default": "test"}
                    ],
                }
            ),
            world_reply(),
        ]
    )
    planned = plan(
        root=harness.root,
        idea="",
        from_bug_id=bug.id,
        config=Config(limits=LimitsConfig(rpm=1000)),
        provider=provider,
        ledger=harness.ledger,
        clock=harness.clock,
        guard=harness.guard,
        answer=lambda question: "",
    )
    assert generated == [2, 2, 2] and planned.run_id == fresh
    assert len(provider.calls) == 2
    row = harness.ledger.get_run(fresh)
    assert row.kind == "plan" and row.status == "finished" and row.branch == ""
    events = harness.ledger.list_events(fresh)
    assert {event.type for event in events} >= {
        "model_call",
        "clarify_question",
        "clarify_answer",
        "spec_written",
    }
    assert all(event.run_id == fresh for event in events)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    written = next(event for event in events if event.type == "spec_written")
    assert (
        written.from_bug_id == bug.id
        and written.spec_version == planned.spec_version_id
    )
    assert (planned.directory / "spec.md").is_file()
    assert (planned.directory / "tasks.md").is_file()
    assert harness.ledger.get_run(occupied) == original
    assert harness.ledger.list_events(occupied) == []


@pytest.mark.parametrize("kind", ["run", "plan"])
def test_run_id_collision_retries_are_bounded_and_preserve_saved_world(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    directory = harness.world()
    occupied, _ = reserve_collision_id(harness)
    calls: list[int] = []

    def suffix(nbytes: int) -> str:
        calls.append(nbytes)
        return "aaaa"

    monkeypatch.setattr(ledger_repo.secrets, "token_hex", suffix)
    if kind == "run":
        engine = harness.engine([])
        with pytest.raises(EngineError, match="unique run id"):
            engine.run(directory)
        assert (
            isinstance(engine.provider, ScriptedProvider) and not engine.provider.calls
        )
    else:
        provider = ScriptedProvider([world_reply()])
        with pytest.raises(PlanError, match="validated world is saved"):
            plan(
                root=harness.root,
                idea="repair",
                no_clarify=True,
                config=Config(),
                provider=provider,
                ledger=harness.ledger,
                clock=harness.clock,
                guard=harness.guard,
                answer=lambda question: "",
            )
        assert len(provider.calls) == 1
        assert (harness.root / ".specvania/specs/repair/spec.md").is_file()
        assert (harness.root / ".specvania/specs/repair/tasks.md").is_file()
    assert calls == [2] * 5
    assert harness.ledger.list_events(occupied) == []
    assert "specvania/" not in git(harness.root, "branch", "--list")
