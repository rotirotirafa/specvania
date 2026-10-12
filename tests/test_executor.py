import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from specvania.agent.approval import ApprovalBroker, ScriptedResponder
from specvania.agent.executor import Executor
from specvania.agent.sandbox import Sandbox
from specvania.agent.tools import Tools
from specvania.clock import FakeClock
from specvania.config import Config, LimitsConfig, RolesConfig
from specvania.provider.client import Completion, ProviderError
from specvania.provider.fake import ScriptedProvider
from specvania.provider.router import Router
from specvania.run.events import Action, Event
from specvania.sdd.world import ChecklistItem, Room


def reply(name: str, **args: Any) -> Completion:
    return Completion(
        content=json.dumps({"thought": "next", "action": name, "args": args})
    )


def assert_no_room_transitions(events: list[Event]) -> None:
    assert not any(
        event.type in ("room_failed", "room_cleared", "respawn") for event in events
    )


def room(*files: str) -> Room:
    return Room(
        path=Path("tasks.md"),
        line=5,
        id="T09",
        title="Example room",
        area_id="A3",
        files=files,
        context="Implement the change.",
        checklist=(
            ChecklistItem(
                path=Path("tasks.md"), line=8, text="Change works", checked=False
            ),
        ),
    )


def setup_executor(
    root: Path,
    script: list[Completion | ProviderError],
    *,
    max_steps: int = 25,
    allow: bool = True,
) -> tuple[Executor, ScriptedProvider, list[Event], FakeClock]:
    events: list[Event] = []
    clock = FakeClock()
    limits = LimitsConfig(max_steps=max_steps)
    config = Config(
        limits=limits,
        roles=RolesConfig(mage=["mage"], warrior=["warrior"], paladin=["paladin"]),
    )
    provider = ScriptedProvider(script)
    router = Router(
        config=config,
        provider=provider,
        clock=clock,
        run_id="run",
        emit=events.append,
        jitter=lambda: 0,
    )
    sandbox = Sandbox(root)
    broker = ApprovalBroker(
        [ScriptedResponder("terminal", [(allow, 0)])],
        clock=clock,
        emit=events.append,
        run_id="run",
    )
    tools = Tools(
        sandbox,
        broker=broker,
        clock=clock,
        emit=events.append,
        run_id="run",
        room_id="T09",
        observation_kb=8,
        shell_timeout=5,
    )
    return (
        Executor(
            router=router,
            tools=tools,
            sandbox=sandbox,
            limits=limits,
            clock=clock,
            run_id="run",
            emit=events.append,
        ),
        provider,
        events,
        clock,
    )


def test_two_invalid_outputs_are_repaired_then_valid_action_finishes(
    tmp_path: Path,
) -> None:
    executor, provider, events, _ = setup_executor(
        tmp_path,
        [
            Completion(content="not JSON"),
            Completion(
                content='{"thought":"x","action":"finish","args":{"extra":true}}'
            ),
            reply("finish"),
        ],
    )
    result = executor.execute(room(), spec_summary="summary")
    assert result.status == "finished" and result.steps == 1
    assert len(provider.calls) == 3
    assert "exactly one JSON object" in provider.calls[1].messages[-1].content
    assert "extra" in provider.calls[2].messages[-1].content
    assert [event.action for event in events if isinstance(event, Action)] == ["finish"]
    assert_no_room_transitions(events)


def test_third_invalid_output_fails_with_reason_and_no_action(tmp_path: Path) -> None:
    executor, provider, events, _ = setup_executor(
        tmp_path, [Completion(content="invalid") for _ in range(3)]
    )
    result = executor.execute(room(), spec_summary="summary")
    assert result.status == "failed" and result.stop_reason == "invalid_output"
    assert result.steps == 0 and len(provider.calls) == 3
    assert (
        "2 repair prompts" in result.reason
        and "exactly one JSON object" in result.reason
    )
    assert not any(isinstance(event, Action) for event in events)
    assert_no_room_transitions(events)


def test_loop_stops_at_max_steps_and_records_why(tmp_path: Path) -> None:
    executor, provider, events, _ = setup_executor(
        tmp_path, [reply("list_dir", path=".") for _ in range(3)], max_steps=2
    )
    result = executor.execute(room(), spec_summary="summary")
    assert result.status == "failed" and result.stop_reason == "max_steps"
    assert result.steps == len(provider.calls) == 2
    assert "max_steps=2" in result.reason and "without finish" in result.reason
    assert_no_room_transitions(events)


def test_repairs_reset_each_turn_and_finish_on_last_step(tmp_path: Path) -> None:
    executor, provider, _, _ = setup_executor(
        tmp_path,
        [
            Completion(content="invalid"),
            reply("list_dir", path="."),
            Completion(content="invalid"),
            Completion(content="invalid"),
            reply("finish"),
        ],
        max_steps=2,
    )
    result = executor.execute(room(), spec_summary="summary")
    assert result.status == "finished" and result.steps == 2
    assert len(provider.calls) == 5


def test_scripted_read_edit_approved_shell_and_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "note.txt").write_text("old-content")
    launch = subprocess.Popen

    def completed(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = launch(*args, **kwargs)
        process.wait()
        return process

    monkeypatch.setattr(subprocess, "Popen", completed)
    executor, provider, events, clock = setup_executor(
        tmp_path,
        [
            reply("read_file", path="note.txt"),
            reply("edit_file", path="note.txt", old="old-content", new="new-content"),
            reply("run_shell", command="printf shell-output > marker.txt"),
            reply("finish"),
        ],
    )
    result = executor.execute(room("note.txt"), spec_summary="summary")
    assert result.status == "finished" and result.steps == 4
    assert (tmp_path / "note.txt").read_text() == "new-content"
    assert (tmp_path / "marker.txt").read_text() == "shell-output"
    assert json.loads(provider.calls[1].messages[-1].content)["output"] == "old-content"
    assert json.loads(provider.calls[2].messages[-1].content)["output"] == "file edited"
    assert json.loads(provider.calls[3].messages[-1].content)["exit_code"] == 0
    assert [event.type for event in events if event.type.startswith("approval_")] == [
        "approval_requested",
        "approval_answered",
    ]
    assert all(event.seq == 0 and event.run_id == "run" for event in events)
    assert [event.ts for event in events] == sorted(event.ts for event in events)
    assert events[-1].ts == clock.now()
    for index, event in enumerate(events):
        if isinstance(event, Action):
            assert events[index - 1].type == "model_call"
            assert event.ts == events[index - 1].ts
    actions = [event for event in events if isinstance(event, Action)]
    assert [event.action for event in actions] == [
        "read_file",
        "edit_file",
        "run_shell",
        "finish",
    ]
    assert all(
        event.room_id == "T09" and len(event.args_summary) <= 200 for event in actions
    )
    assert "old-content" not in str(events) and "new-content" not in str(events)
    assert_no_room_transitions(events)


def test_denied_shell_returns_observation_and_continues(tmp_path: Path) -> None:
    executor, provider, _, _ = setup_executor(
        tmp_path,
        [reply("run_shell", command="printf ran > marker"), reply("finish")],
        allow=False,
    )
    assert executor.execute(room(), spec_summary="summary").status == "finished"
    assert not (tmp_path / "marker").exists()
    assert (
        json.loads(provider.calls[1].messages[-1].content)["output"] == "denied by user"
    )


@pytest.mark.parametrize("status", [401, 503])
def test_router_error_ends_attempt_and_records_reason(
    tmp_path: Path, status: int
) -> None:
    executor, provider, events, _ = setup_executor(
        tmp_path, [ProviderError(status=status) for _ in range(3)]
    )
    result = executor.execute(room(), spec_summary="summary")
    assert result.status == "failed" and result.stop_reason == "router_error"
    assert len(provider.calls) == (1 if status == 401 else 3)
    assert result.steps == 0
    assert result.reason == (
        "Provider call failed (HTTP 401); check provider configuration."
        if status == 401
        else "warrior model chain exhausted; retry or change the chain."
    )
    assert_no_room_transitions(events)


def test_attempt_context_contains_required_docs_room_files_and_feedback(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/domain.md").write_text("DOMAIN-DOC")
    (tmp_path / "docs/conventions.md").write_text("CONVENTIONS-DOC")
    (tmp_path / "note.txt").write_text("ROOM-FILE-CONTENT")
    (tmp_path / "unrelated.txt").write_text("UNRELATED-CONTENT")
    (tmp_path / ".env").write_text("SECRET-CONTENT")
    executor, provider, _, _ = setup_executor(
        tmp_path, [reply("finish"), reply("finish")]
    )
    executor.execute(
        room("note.txt", ".env"),
        spec_summary="SPEC-SUMMARY",
        feedback="PALADIN-FEEDBACK",
    )
    first = provider.calls[0].messages
    assert first[0].role == "system" and "read_file" in first[0].content
    context = json.loads(first[1].content)
    assert context["spec_summary"] == "SPEC-SUMMARY"
    assert "T09" in context["room_block"] and "Change works" in context["room_block"]
    assert (
        context["conventions"] == "CONVENTIONS-DOC"
        and context["domain"] == "DOMAIN-DOC"
    )
    assert "note.txt" in context["repo_map"] and "unrelated.txt" in context["repo_map"]
    assert context["feedback"] == "PALADIN-FEEDBACK"
    assert "ROOM-FILE-CONTENT" in first[1].content
    assert (
        "UNRELATED-CONTENT" not in first[1].content
        and "SECRET-CONTENT" not in first[1].content
    )
    executor.execute(room(), spec_summary="NEW-SUMMARY")
    assert len(provider.calls[1].messages) == 2
    assert "PALADIN-FEEDBACK" not in provider.calls[1].messages[1].content


def test_refused_and_failed_tools_return_observations_to_warrior(
    tmp_path: Path,
) -> None:
    executor, provider, events, _ = setup_executor(
        tmp_path,
        [
            reply("read_file", path="../outside"),
            reply("read_file", path="missing"),
            reply("finish"),
        ],
    )
    assert executor.execute(room(), spec_summary="summary").status == "finished"
    assert (
        "repository root"
        in json.loads(provider.calls[1].messages[-1].content)["output"]
    )
    assert "Cannot read" in json.loads(provider.calls[2].messages[-1].content)["output"]
    assert any(event.type == "tool_refused" for event in events)


def test_all_actions_emit_summary_only_events_and_thought_cap(tmp_path: Path) -> None:
    executor, _, events, _ = setup_executor(
        tmp_path,
        [
            reply("write_file", path="note", content="PRIVATE-FILE-CONTENT"),
            reply("search", pattern="PRIVATE-FILE-CONTENT"),
            reply("list_dir", path="."),
            Completion(
                content=json.dumps(
                    {"thought": "x" * 600, "action": "finish", "args": {}}
                )
            ),
        ],
    )
    assert executor.execute(room(), spec_summary="summary").status == "finished"
    actions = [event for event in events if isinstance(event, Action)]
    assert [event.action for event in actions] == [
        "write_file",
        "search",
        "list_dir",
        "finish",
    ]
    assert all(len(event.args_summary) <= 200 for event in actions)
    assert actions[-1].thought == "x" * 500
    assert "PRIVATE-FILE-CONTENT" not in str(events)


def test_context_map_is_bounded_and_room_folders_use_safe_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "room").mkdir()
    for number in range(305):
        (tmp_path / f"entry{number:03}.txt").write_text("not-selected")
    (tmp_path / "room/selected.txt").write_text("SELECTED-CONTENT")
    (tmp_path / "room/.env.local").write_text("SECRET-CONTENT")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.write_text("OUTSIDE-CONTENT")
    (tmp_path / "room/escape.txt").symlink_to(outside)
    executor, provider, _, _ = setup_executor(tmp_path, [reply("finish")])
    executor.execute(room("room"), spec_summary="summary")
    context = json.loads(provider.calls[0].messages[1].content)
    assert len(context["repo_map"]) == 300
    assert [entry["path"] for entry in context["room_files"]] == ["room/selected.txt"]
    assert "SELECTED-CONTENT" in provider.calls[0].messages[1].content
    assert "SECRET-CONTENT" not in provider.calls[0].messages[1].content
    assert "OUTSIDE-CONTENT" not in provider.calls[0].messages[1].content


def test_truncated_tool_observations_and_context_reach_warrior(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 10_000)
    executor, provider, _, _ = setup_executor(
        tmp_path, [reply("read_file", path="large.txt"), reply("finish")]
    )
    executor.execute(room("large.txt"), spec_summary="summary")
    initial = json.loads(provider.calls[0].messages[1].content)["room_files"][0][
        "observation"
    ]
    observation = json.loads(provider.calls[1].messages[-1].content)
    for value in (initial, observation):
        assert value["truncated"] is True
        assert len(value["output"].encode()) <= 8 * 1024
        assert value["output"].endswith("[truncated]")


def test_router_error_during_repair_counts_only_completed_actions(
    tmp_path: Path,
) -> None:
    executor, provider, events, _ = setup_executor(
        tmp_path,
        [
            reply("list_dir", path="."),
            Completion(content="invalid"),
            ProviderError(status=401),
        ],
    )
    result = executor.execute(room(), spec_summary="summary")
    assert result.stop_reason == "router_error" and result.steps == 1
    assert len(provider.calls) == 3
    assert [event.action for event in events if isinstance(event, Action)] == [
        "list_dir"
    ]
    assert result.reason == (
        "Provider call failed (HTTP 401); check provider configuration."
    )
    assert_no_room_transitions(events)
