"""Offline Mage acceptance evidence: real lint, router, storage and CLI."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import initialize_config
from specvania.ledger import Ledger
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.run.events import Role
from specvania.sdd.planner import PlanResult, Question
from specvania.ui.server import UIServer


@pytest.fixture
def no_ui_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def git_probe(
        arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        assert arguments[0] == "git" and arguments[-2:] == [
            "rev-parse",
            "--is-inside-work-tree",
        ]
        return subprocess.CompletedProcess(
            arguments, returncode=1, stdout=b"", stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", git_probe)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No UI test processes")
    )


def world_reply() -> Completion:
    return Completion(
        content=json.dumps(
            {
                "spec_md": (
                    "# Invoices\n\n### S1 · Invoices\n```gherkin\n"
                    "Given an invoice\nWhen fetched\nThen it is returned\n```\n"
                ),
                "tasks_md": (
                    "## A1 · API\nboss: S1\n\n### T01 · Invoices\n"
                    "depends_on: none\ncovers: S1\nfiles: invoices.py\n\n"
                    "Checklist:\n- [ ] Fetch an invoice\n"
                ),
            }
        ),
        tokens_in=12,
        tokens_out=30,
    )


@pytest.mark.parametrize(
    "ui_args,configured,expected",
    [
        (["--ui"], "auto", "auto"),
        (["--ui"], "browser", "browser"),
        (["--ui"], "desktop", "desktop"),
        (["--ui=auto"], "desktop", "auto"),
        (["--ui=browser"], "desktop", "browser"),
        (["--ui=desktop"], "browser", "desktop"),
        (["--ui", "browser"], "desktop", "browser"),
    ],
)
def test_cli_plan_ui_launches_before_mage_and_streams_without_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_ui_processes: None,
    ui_args: list[str],
    configured: str,
    expected: str,
) -> None:
    from starlette.testclient import TestClient

    from specvania.run.bus import Subscription
    from specvania.run.events import Event, RedactionGuard

    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text(f'[ui]\nmode = "{configured}"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    servers: list[UIServer] = []
    launched: list[str] = []
    received: list[Event] = []
    subscriptions: list[Subscription] = []

    def serve(self: UIServer, *, on_started: Callable[[], None]) -> None:
        assert self.ledger is None and self.replay is None and self.bus is not None
        self.port = 8765
        servers.append(self)
        subscriptions.append(self.bus.subscribe(self.run_id))
        on_started()

    def launch(port: int, view: str, control: str, **kwargs: object) -> str:
        assert port == 8765
        assert view == servers[0].view_token and control == servers[0].control_token
        launched.append(str(kwargs["mode"]))
        return expected

    def draft() -> Completion:
        import sqlite3

        assert launched == [expected]
        with sqlite3.connect(tmp_path / ".specvania/ledger.db") as connection:
            assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        return world_reply()

    provider = ScriptedProvider([draft])
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(UIServer, "serve", serve)
    monkeypatch.setattr(cli, "launch_ui", launch)
    result = CliRunner().invoke(
        cli.create_app(), ["plan", "invoices", "--no-clarify", *ui_args]
    )
    assert result.exit_code == 0, result.output
    assert len(servers) == 1 and launched == [expected]
    server = servers[0]
    assert server.live_snapshot is not None
    received = [subscriptions[0].get(timeout=0) for _ in range(3)]
    subscriptions[0].close()
    assert list(server.live_snapshot()) == received
    assert [event.type for event in received] == [
        "model_requested",
        "model_call",
        "spec_written",
    ]
    assert [event.seq for event in received] == [1, 2, 3]
    assert "bus only" in result.output.lower()
    with Ledger.open(
        f"sqlite:///{tmp_path / '.specvania/ledger.db'}",
        clock=FakeClock(),
        guard=RedactionGuard(),
    ) as ledger:
        assert ledger.get_run(server.run_id).kind == "plan"
        assert ledger.list_events(server.run_id) == received
    with TestClient(server.app, base_url=server.origin) as client:
        with client.websocket_connect(
            f"{server.origin.replace('http:', 'ws:')}/ws"
            f"?view_token={server.view_token}",
            headers={"Origin": server.origin},
        ) as websocket:
            state = websocket.receive_json()["state"]
            assert state["run_id"] == server.run_id and state["seq"] == 3
            assert state["models"][0]["calls"] == 1


@pytest.mark.parametrize("command", ["run", "plan"])
@pytest.mark.parametrize("ui_args", [["--ui=invalid"], ["--ui", "invalid"]])
def test_cli_invalid_ui_mode_is_usage_error_before_work(
    monkeypatch: pytest.MonkeyPatch, command: str, ui_args: list[str]
) -> None:
    monkeypatch.setattr(
        cli, "load_config", lambda *args: pytest.fail("Invalid UI must prevent work")
    )
    result = CliRunner().invoke(cli.create_app(), [command, "world", *ui_args])
    assert result.exit_code == 2, result.output
    assert "Usage:" in result.output
    assert "Traceback" not in result.output


def test_cli_failed_plan_ui_keeps_live_events_without_ledger_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_ui_processes: None
) -> None:
    import sqlite3

    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    servers: list[UIServer] = []
    monkeypatch.setattr(
        cli, "start_run_ui", lambda server, **kwargs: servers.append(server)
    )
    provider = ScriptedProvider([clarify_reply(), ProviderError(status=401)])
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)

    def answer(*args: object, **kwargs: object) -> str:
        server = servers[0]
        assert server.live_snapshot is not None
        events = server.live_snapshot()
        assert events[-1].type == "clarify_question"
        with sqlite3.connect(tmp_path / ".specvania/ledger.db") as connection:
            assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        return "test"

    monkeypatch.setattr(cli.Prompt, "ask", answer)
    result = CliRunner().invoke(cli.create_app(), ["plan", "invoices", "--ui"])
    assert result.exit_code == 1 and "401" in result.output
    assert "Traceback" not in result.output
    server = servers[0]
    assert server.ledger is None and server.live_snapshot is not None
    events = server.live_snapshot()
    assert any(event.type == "clarify_answer" for event in events)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert not any(event.type == "spec_written" for event in events)
    assert (tmp_path / ".specvania/specs/invoices/spec.draft.md").is_file()
    assert all(server.control_token not in event.model_dump_json() for event in events)
    with sqlite3.connect(tmp_path / ".specvania/ledger.db") as connection:
        for table in ("projects", "features", "spec_versions", "runs", "events"):
            assert (
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            )


def test_cli_plan_ui_start_failure_exits_before_mage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_ui_processes: None
) -> None:
    from specvania.ui.server import UIServerError

    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    provider = ScriptedProvider([world_reply()])
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)

    def serve(self: UIServer, **kwargs: object) -> None:
        raise UIServerError("Cannot bind local port")

    monkeypatch.setattr(UIServer, "serve", serve)
    result = CliRunner().invoke(
        cli.create_app(), ["plan", "invoices", "--no-clarify", "--ui"]
    )
    assert result.exit_code == 1 and "Cannot bind local port" in result.output
    assert "Traceback" not in result.output
    assert provider.calls == []


def test_cli_no_clarify_prints_no_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    initialize_config(tmp_path)
    provider = ScriptedProvider([world_reply()])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    result = CliRunner().invoke(cli.create_app(), ["plan", "invoices", "--no-clarify"])
    assert result.exit_code == 0, result.output
    assert "?" not in result.output
    assert "default" not in result.output.lower()
    assert len(provider.calls) == 1
    assert (tmp_path / ".specvania/specs/invoices/spec.md").is_file()


def clarify_reply(count: int = 1, *, needed: bool = True) -> Completion:
    return Completion(
        content=json.dumps(
            {
                "needs_clarification": needed,
                "questions": [
                    {
                        "id": f"C{i}",
                        "question": f"Which acceptance signal {i}?",
                        "options": ["test", "manual"],
                        "default": "test",
                    }
                    for i in range(1, count + 1)
                ]
                if needed
                else [],
            }
        )
    )


def execute_plan(
    tmp_path: Path,
    script: list[Completion | ProviderError],
    *,
    answer: Callable[[Question], str] = lambda question: "",
    no_clarify: bool = False,
    gates: list[str] | None = None,
) -> tuple[PlanResult, ScriptedProvider, Ledger]:
    from specvania.config import Config
    from specvania.ledger import Ledger
    from specvania.run.events import RedactionGuard
    from specvania.sdd.planner import plan

    provider = ScriptedProvider(script)
    clock = FakeClock()
    guard = RedactionGuard()
    ledger = Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=clock, guard=guard
    )
    try:
        result = plan(
            root=tmp_path,
            idea="invoices",
            config=Config(gates=gates or []),
            provider=provider,
            ledger=ledger,
            clock=clock,
            guard=guard,
            answer=answer,
            no_clarify=no_clarify,
        )
        return result, provider, ledger
    except BaseException:
        ledger.close()
        raise


@pytest.mark.parametrize("gates", [[], ["uv run pytest -q", "ruff check ."]])
@pytest.mark.parametrize("call_index", [0, 1], ids=["clarify", "spec"])
def test_mage_requests_include_configured_gates(
    tmp_path: Path, gates: list[str], call_index: int
) -> None:
    _, provider, ledger = execute_plan(
        tmp_path, [clarify_reply(), world_reply()], gates=gates
    )
    with ledger:
        context = json.loads(provider.calls[call_index].messages[-1].content)
        assert context["gates"] == gates


def test_spec_prompt_mentions_gates(tmp_path: Path) -> None:
    _, provider, ledger = execute_plan(tmp_path, [world_reply()], no_clarify=True)
    with ledger:
        assert "gates" in provider.calls[0].messages[0].content.lower()


@pytest.mark.parametrize("count", [1, 5])
def test_ambiguous_idea_defaults_and_clarify_log(tmp_path: Path, count: int) -> None:
    asked: list[Question] = []

    def answer(question: Question) -> str:
        asked.append(question)
        return ""

    result, provider, ledger = execute_plan(
        tmp_path, [clarify_reply(count), world_reply()], answer=answer
    )
    with ledger:
        assert len(asked) == count
        events = ledger.list_events(result.run_id)
        questions = [e for e in events if e.type == "clarify_question"]
        answers = [e for e in events if e.type == "clarify_answer"]
        assert len(questions) == len(answers) == count
        assert [e.answer for e in answers] == ["test"] * count
        spec = (result.directory / "spec.md").read_text()
        assert "## Clarify log" in spec
        for i in range(1, count + 1):
            assert f"Which acceptance signal {i}?" in spec
        assert "test" in spec
        assert '"answer": "test"' in provider.calls[1].messages[-1].content
        assert '"idea": "invoices"' in provider.calls[0].messages[-1].content


def test_decided_idea_asks_nothing(tmp_path: Path) -> None:
    def forbidden(question: Question) -> str:
        pytest.fail("No open decisions should ask no questions")

    result, _, ledger = execute_plan(
        tmp_path, [clarify_reply(needed=False), world_reply()], answer=forbidden
    )
    with ledger:
        assert not any(
            e.type.startswith("clarify_") for e in ledger.list_events(result.run_id)
        )


def test_written_world_lint_repair_includes_diagnostics(tmp_path: Path) -> None:
    from specvania.sdd.lint import lint_directory

    invalid = Completion(
        content=json.dumps(
            {
                "spec_md": (
                    "### S1 · Uncovered\n```gherkin\nGiven x\nWhen y\nThen z\n```\n"
                ),
                "tasks_md": "",
            }
        )
    )
    result, provider, ledger = execute_plan(
        tmp_path, [invalid, world_reply()], no_clarify=True
    )
    with ledger:
        assert lint_directory(result.directory) == ()
        assert len(provider.calls) == 2
        repair = json.loads(provider.calls[1].messages[-1].content)
        assert any(
            "L4" in diagnostic and "not covered" in diagnostic
            for diagnostic in repair["lint_errors"]
        )
        assert repair["draft"]["spec_md"].startswith("### S1")
        assert not (result.directory / "spec.draft.md").exists()


def test_cli_two_failed_repairs_leave_last_drafts_and_no_ledger_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from specvania.ledger import Ledger
    from specvania.run.events import RedactionGuard

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    initialize_config(tmp_path)
    provider = ScriptedProvider(
        [
            Completion(
                content=json.dumps(
                    {"spec_md": f"draft {i}", "tasks_md": "### T01 · Missing area\n"}
                )
            )
            for i in range(3)
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    result = CliRunner().invoke(cli.create_app(), ["plan", "invoices", "--no-clarify"])
    assert result.exit_code == 1, result.output
    assert "2 repair" in result.output
    assert "Traceback" not in result.output
    assert len(provider.calls) == 3
    directory = tmp_path / ".specvania/specs/invoices"
    assert (directory / "spec.draft.md").read_text() == "draft 2"
    assert (directory / "tasks.draft.md").read_text() == "### T01 · Missing area\n"
    assert not (directory / "spec.md").exists()
    with Ledger.open(
        f"sqlite:///{tmp_path / '.specvania/ledger.db'}",
        clock=FakeClock(),
        guard=RedactionGuard(),
    ) as ledger:
        # No feature should have been recorded by the failed plan.
        project = ledger.add_project(tmp_path)
        assert ledger.list_features(project.id) == []


def test_success_persists_ordered_events_versions_and_reuses_feature(
    tmp_path: Path,
) -> None:
    import re

    from specvania.config import Config
    from specvania.run.events import RedactionGuard
    from specvania.sdd.planner import plan

    result, provider, ledger = execute_plan(tmp_path, [clarify_reply(), world_reply()])
    with ledger:
        run = ledger.get_run(result.run_id)
        assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", run.id)
        assert run.kind == "plan" and run.status == "finished" and run.branch == ""
        assert run.finished_at is not None
        feature = ledger.get_feature(run.feature_id)
        assert feature.slug == "invoices"
        version = ledger.get_spec_version(run.spec_version_id)
        assert version.spec_md == (result.directory / "spec.md").read_text()
        assert version.tasks_md == (result.directory / "tasks.md").read_text()
        events = ledger.list_events(run.id)
        assert [e.type for e in events] == [
            "model_requested",
            "model_call",
            "clarify_question",
            "clarify_answer",
            "cooldown",
            "model_requested",
            "model_call",
            "spec_written",
        ]
        assert [e.seq for e in events] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert all(e.role == Role.MAGE for e in events if e.type == "model_requested")
        assert all(e.run_id == run.id for e in events)
        assert (
            events[-1].type == "spec_written" and events[-1].spec_version == version.id
        )
        assert run.calls == 2 and run.tokens_in == 12 and run.tokens_out == 30
        again = plan(
            root=tmp_path,
            idea="invoices",
            config=Config(),
            provider=ScriptedProvider([clarify_reply(), world_reply()]),
            ledger=ledger,
            clock=FakeClock(),
            guard=RedactionGuard(),
            answer=lambda question: "",
        )
        assert ledger.get_run(again.run_id).feature_id == feature.id
        assert ledger.get_run(again.run_id).spec_version_id == version.id
        assert len(ledger.list_runs(feature.id)) == 2
        assert len(ledger.list_spec_versions(feature.id)) == 1
        assert len(provider.calls) == 2


def test_router_error_preserves_last_draft_without_ledger_records(
    tmp_path: Path,
) -> None:
    from specvania.config import Config
    from specvania.ledger import Ledger
    from specvania.provider.client import ProviderError
    from specvania.run.events import RedactionGuard
    from specvania.sdd.planner import PlanError, plan

    guard = RedactionGuard()
    with Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=FakeClock(), guard=guard
    ) as ledger:
        provider = ScriptedProvider(
            [
                Completion(
                    content=json.dumps(
                        {
                            "spec_md": "last draft",
                            "tasks_md": "### T01 · Missing area\n",
                        }
                    )
                ),
                ProviderError(status=401),
            ]
        )
        with pytest.raises(PlanError, match="Provider"):
            plan(
                root=tmp_path,
                idea="invoices",
                config=Config(),
                provider=provider,
                ledger=ledger,
                clock=FakeClock(),
                guard=guard,
                answer=lambda question: "",
                no_clarify=True,
            )
        project = ledger.add_project(tmp_path)
        assert ledger.list_features(project.id) == []
        directory = tmp_path / ".specvania/specs/invoices"
        assert (directory / "spec.draft.md").read_text() == "last draft"
        assert (directory / "tasks.draft.md").read_text() == "### T01 · Missing area\n"


def test_router_error_before_generation_leaves_empty_drafts(tmp_path: Path) -> None:
    from specvania.provider.client import ProviderError
    from specvania.sdd.planner import PlanError

    with pytest.raises(PlanError):
        execute_plan(tmp_path, [ProviderError(status=401)])
    directory = tmp_path / ".specvania/specs/invoices"
    assert (directory / "spec.draft.md").read_text() == ""
    assert (directory / "tasks.draft.md").read_text() == ""


def test_fallback_and_cooldown_are_buffered_with_plan_id(tmp_path: Path) -> None:
    from specvania.provider.client import ProviderError

    result, _, ledger = execute_plan(
        tmp_path,
        [ProviderError(status=429, retry_after="5")] * 3 + [world_reply()],
        no_clarify=True,
    )
    with ledger:
        events = ledger.list_events(result.run_id)
        assert sum(e.type == "model_call" for e in events) == 4
        assert sum(e.type == "cooldown" for e in events) == 3
        assert sum(e.type == "fallback" for e in events) == 1
        assert all(e.run_id == result.run_id for e in events)
        assert [e.seq for e in events] == list(range(1, len(events) + 1))


def test_context_uses_markdown_resources_and_safe_truncated_map(tmp_path: Path) -> None:
    import subprocess
    from importlib.resources import files

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    for name in ["README.md", "docs/conventions.md", "docs/domain.md"]:
        (tmp_path / name).write_text(f"context from {name}")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored/secret.txt").write_text("ignored contents")
    (tmp_path / ".env.local").write_text("secret contents")
    (tmp_path / "key.pem").write_text("private key")
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside contents")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    for i in range(310):
        (tmp_path / f"file{i:03}.py").write_text("code")
    result, provider, ledger = execute_plan(tmp_path, [clarify_reply(), world_reply()])
    with ledger:
        context = json.loads(provider.calls[0].messages[-1].content)
        assert len(context["repo_map"]) == 300 and context["repo_map_truncated"] is True
        joined = "\n".join(context["repo_map"])
        assert all(
            value not in joined
            for value in [".git/", ".env", "key.pem", "ignored/", "escape"]
        )
        for name in ["README.md", "docs/conventions.md", "docs/domain.md"]:
            assert context["documents"][name] == f"context from {name}"
        for index, name in [(0, "clarify.md"), (1, "spec.md")]:
            prompt = files("specvania.sdd").joinpath("prompts", name).read_text()
            instruction = (
                files("specvania.agent.prompts")
                .joinpath("language.md")
                .read_text()
                .replace("{language}", "English")
            )
            assert provider.calls[index].messages[0].content == (
                prompt.rstrip() + "\n\n" + instruction
            )
        assert result.directory.is_dir()


def test_cli_enter_accepts_visible_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from specvania.ledger import Ledger
    from specvania.run.events import RedactionGuard

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    initialize_config(tmp_path)
    provider = ScriptedProvider([clarify_reply(), world_reply()])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    result = CliRunner().invoke(cli.create_app(), ["plan", "invoices"], input="\n")
    assert result.exit_code == 0, result.output
    assert "Which acceptance signal 1?" in result.output
    assert "Answer (test)" in result.output
    assert '"answer": "test"' in provider.calls[1].messages[-1].content
    with Ledger.open(
        f"sqlite:///{tmp_path / '.specvania/ledger.db'}",
        clock=FakeClock(),
        guard=RedactionGuard(),
    ) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.list_features(project.id)[0]
        events = ledger.list_events(ledger.list_runs(feature.id)[0].id)
        assert any(e.type == "clarify_answer" and e.answer == "test" for e in events)


@pytest.mark.parametrize(
    "reply",
    [
        {"needs_clarification": True, "questions": []},
        {
            "needs_clarification": False,
            "questions": [{"id": "C1", "question": "Scope?", "default": "small"}],
        },
        {
            "needs_clarification": True,
            "questions": [
                {"id": f"C{i}", "question": "Scope?", "default": "small"}
                for i in range(6)
            ],
        },
        {
            "needs_clarification": True,
            "questions": [
                {
                    "id": "C1",
                    "question": "Scope?",
                    "options": ["small"],
                    "default": "large",
                }
            ],
        },
        {
            "needs_clarification": True,
            "questions": [{"id": "C1", "question": "Scope?", "default": "small"}] * 2,
        },
    ],
)
def test_invalid_clarification_is_rejected_before_questions(
    tmp_path: Path, reply: dict[str, object]
) -> None:
    from specvania.sdd.planner import PlanError

    def forbidden(question: Question) -> str:
        pytest.fail("Invalid clarification must never reach the answer function")

    with pytest.raises(PlanError):
        execute_plan(
            tmp_path, [Completion(content=json.dumps(reply))] * 3, answer=forbidden
        )
    assert (tmp_path / ".specvania/specs/invoices/spec.draft.md").is_file()


def test_explicit_answer_full_log_and_short_event_summary(tmp_path: Path) -> None:
    chosen = "manual | acceptance\n" + "x" * 220
    result, provider, ledger = execute_plan(
        tmp_path, [clarify_reply(), world_reply()], answer=lambda question: chosen
    )
    with ledger:
        events = ledger.list_events(result.run_id)
        answer = next(e for e in events if e.type == "clarify_answer")
        assert answer.type == "clarify_answer" and answer.answer == chosen[:200]
        spec = (result.directory / "spec.md").read_text()
        assert "manual \\| acceptance<br>" + "x" * 220 in spec
        assert (
            json.loads(provider.calls[1].messages[-1].content)["clarify_answers"][0][
                "answer"
            ]
            == chosen
        )


def test_secret_event_rejection_leaves_no_ledger_records(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, text

    from specvania.config import Config
    from specvania.ledger import Ledger
    from specvania.run.events import RedactionGuard
    from specvania.sdd.planner import PlanError, plan

    guard = RedactionGuard()
    secret = "fake-planning-key"
    guard.register(secret)
    url = f"sqlite:///{tmp_path / 'ledger.db'}"
    with Ledger.open(url, clock=FakeClock(), guard=guard) as ledger:
        with pytest.raises(PlanError, match="secret"):
            plan(
                root=tmp_path,
                idea="invoices",
                config=Config(),
                provider=ScriptedProvider([clarify_reply(), world_reply()]),
                ledger=ledger,
                clock=FakeClock(),
                guard=guard,
                answer=lambda question: secret,
            )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            for table in ("projects", "features", "spec_versions", "runs", "events"):
                assert (
                    connection.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    ).scalar_one()
                    == 0
                )
    finally:
        engine.dispose()


def test_spec_written_summary_is_guarded_before_creating_records(
    tmp_path: Path,
) -> None:
    from sqlalchemy import create_engine, text

    from specvania.config import Config
    from specvania.ledger import Ledger
    from specvania.run.events import RedactionGuard
    from specvania.sdd.planner import PlanError, plan

    guard = RedactionGuard()
    guard.register("fake-key-in-idea")
    url = f"sqlite:///{tmp_path / 'ledger.db'}"
    with Ledger.open(url, clock=FakeClock(), guard=guard) as ledger:
        with pytest.raises(PlanError, match="secret"):
            plan(
                root=tmp_path,
                idea="fake-key-in-idea",
                config=Config(),
                provider=ScriptedProvider([world_reply()]),
                ledger=ledger,
                clock=FakeClock(),
                guard=guard,
                answer=lambda question: "",
                no_clarify=True,
            )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            for table in ("projects", "features", "spec_versions", "runs", "events"):
                assert (
                    connection.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    ).scalar_one()
                    == 0
                )
    finally:
        engine.dispose()


def test_fenced_clarification_is_accepted_without_repair(tmp_path: Path) -> None:
    reply = clarify_reply()
    result, provider, ledger = execute_plan(
        tmp_path,
        [Completion(content=f"```json\n{reply.content}\n```"), world_reply()],
    )
    with ledger:
        assert len(provider.calls) == 2
        assert any(
            event.type == "clarify_answer" and event.answer == "test"
            for event in ledger.list_events(result.run_id)
        )


def test_prose_wrapped_draft_does_not_consume_lint_repair(tmp_path: Path) -> None:
    from specvania.sdd.lint import lint_directory

    reply = world_reply()
    result, provider, ledger = execute_plan(
        tmp_path,
        [Completion(content=f"Here is the world:\n{reply.content}\nReady.")],
        no_clarify=True,
    )
    with ledger:
        assert len(provider.calls) == 1
        assert lint_directory(result.directory) == ()
        assert ledger.get_run(result.run_id).calls == 1


def test_two_invalid_clarify_outputs_are_repaired_with_validation_errors(
    tmp_path: Path,
) -> None:
    # The first failure is extraction; the second is the typed output contract.
    result, provider, ledger = execute_plan(
        tmp_path,
        [
            Completion(content="Not JSON yet"),
            Completion(content='{"needs_clarification": true, "questions": []}'),
            clarify_reply(),
            world_reply(),
        ],
    )
    with ledger:
        assert len(provider.calls) == 4
        first = json.loads(provider.calls[1].messages[-1].content)
        second = json.loads(provider.calls[2].messages[-1].content)
        assert "JSON object" in first["validation_error"]
        assert "1 and 5 questions" in second["validation_error"]
        assert provider.calls[1].messages[-2].content == "Not JSON yet"
        assert provider.calls[2].messages[-2].content == (
            '{"needs_clarification": true, "questions": []}'
        )
        events = ledger.list_events(result.run_id)
        assert sum(event.type == "clarify_question" for event in events) == 1
        assert ledger.get_run(result.run_id).calls == 4


@pytest.mark.parametrize(
    "invalid",
    ["No JSON here", '{"needs_clarification": true, "questions": []}'],
)
def test_third_invalid_clarify_exits_cleanly_without_ledger_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    from sqlalchemy import create_engine, text

    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text("")
    provider = ScriptedProvider([Completion(content=invalid)] * 3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    result = CliRunner().invoke(cli.create_app(), ["plan", "invoices"])
    assert result.exit_code == 1, result.output
    assert "Clarify output is invalid after 2 repairs" in result.output
    assert "Traceback" not in result.output
    assert "Answer" not in result.output
    assert len(provider.calls) == 3
    assert (tmp_path / ".specvania/specs/invoices/spec.draft.md").read_text() == ""
    assert (tmp_path / ".specvania/specs/invoices/tasks.draft.md").read_text() == ""
    engine = create_engine(f"sqlite:///{tmp_path / '.specvania/ledger.db'}")
    try:
        with engine.connect() as connection:
            for table in ("projects", "features", "spec_versions", "runs", "events"):
                assert (
                    connection.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    ).scalar_one()
                    == 0
                )
    finally:
        engine.dispose()
