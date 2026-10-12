import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from specvania.agent import sandbox as sandbox_module
from specvania.agent.approval import ApprovalBroker, ScriptedResponder
from specvania.agent.sandbox import Sandbox, SandboxRefusal
from specvania.agent.tools import ToolError, Tools
from specvania.clock import FakeClock
from specvania.run.events import Event, ToolRefused


def make_tools(
    root: Path, *, allow: bool = False, kb: int = 8
) -> tuple[Tools, list[Event], FakeClock]:
    events: list[Event] = []
    clock = FakeClock()
    broker = ApprovalBroker(
        [ScriptedResponder("terminal", [(allow, 0)])],
        clock=clock,
        emit=events.append,
        run_id="run",
    )
    return (
        Tools(
            Sandbox(root),
            broker=broker,
            clock=clock,
            emit=events.append,
            run_id="run",
            room_id="T08",
            observation_kb=kb,
            shell_timeout=5,
        ),
        events,
        clock,
    )


@pytest.fixture
def ignored_repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, capture_output=True
    )
    (tmp_path / ".gitignore").write_text("ignored.txt\nignored_dir/\n")
    (tmp_path / "ignored.txt").write_text("hidden needle")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir/private.txt").write_text("hidden needle")
    (tmp_path / "visible.txt").write_text("visible needle")
    return tmp_path


@pytest.mark.parametrize("path", ["ignored.txt", "ignored_dir/private.txt"])
def test_ignored_files_and_directories_are_refused_for_read_and_write(
    ignored_repo: Path, path: str
) -> None:
    tools, events, _ = make_tools(ignored_repo)
    with pytest.raises(SandboxRefusal, match="ignored"):
        tools.read_file(path)
    with pytest.raises(SandboxRefusal, match="ignored"):
        tools.write_file(path, "changed")
    assert (ignored_repo / path).read_text() == "hidden needle"
    assert [event.type for event in events] == ["tool_refused", "tool_refused"]
    with pytest.raises(SandboxRefusal, match="ignored"):
        tools.write_file("ignored_dir/new.txt", "changed")
    assert not (ignored_repo / "ignored_dir/new.txt").exists()


def test_ignored_files_and_directories_are_hidden_from_list_and_search(
    ignored_repo: Path,
) -> None:
    tools, _, _ = make_tools(ignored_repo)
    assert tools.list_dir(".").output.splitlines() == [".gitignore", "visible.txt"]
    assert tools.search("needle").output == "visible.txt:1:visible needle"
    with pytest.raises(SandboxRefusal, match="ignored"):
        tools.list_dir("ignored_dir")


@pytest.mark.parametrize("tool", ["search", "list_dir"])
def test_directory_walks_use_bounded_git_subprocesses(
    ignored_repo: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    expected_matches: list[str] = []
    for directory in range(10):
        folder = ignored_repo / f"dir{directory:02}"
        folder.mkdir()
        for number in range(20):
            name = f"dir{directory:02}/file{number:02}.txt"
            (ignored_repo / name).write_text("visible needle")
            expected_matches.append(f"{name}:1:visible needle")
    tools, _, _ = make_tools(ignored_repo)
    run = sandbox_module.subprocess.run
    calls: list[tuple[str, ...]] = []

    def counted(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(tuple(command))
        return run(command, **kwargs)

    monkeypatch.setattr(sandbox_module.subprocess, "run", counted)
    if tool == "search":
        expected_matches.append("visible.txt:1:visible needle")
        assert tools.search("needle").output.splitlines() == expected_matches
    else:
        assert tools.list_dir(".").output.splitlines() == [
            ".gitignore",
            *[f"dir{directory:02}/" for directory in range(10)],
            "visible.txt",
        ]
    assert 1 <= len(calls) <= 4, f"{tool} launched {len(calls)} Git subprocesses"


def test_walks_check_symlink_targets_against_ignore_rules(ignored_repo: Path) -> None:
    (ignored_repo / "alias.txt").symlink_to(ignored_repo / "ignored.txt")
    (ignored_repo / "alias_dir").symlink_to(ignored_repo / "ignored_dir")
    tools, _, _ = make_tools(ignored_repo)
    assert tools.list_dir(".").output.splitlines() == [".gitignore", "visible.txt"]
    assert tools.search("needle").output == "visible.txt:1:visible needle"


def test_walk_ignore_snapshot_is_refreshed_for_each_call(ignored_repo: Path) -> None:
    tools, _, _ = make_tools(ignored_repo)
    assert tools.search("needle").output == "visible.txt:1:visible needle"
    (ignored_repo / ".gitignore").write_text("visible.txt\nignored_dir/\n")
    assert tools.search("needle").output == "ignored.txt:1:hidden needle"
    assert tools.list_dir(".").output.splitlines() == [".gitignore", "ignored.txt"]


def test_walk_ignore_snapshot_preserves_nested_rules_and_exceptions(
    ignored_repo: Path,
) -> None:
    folder = ignored_repo / "nested"
    folder.mkdir()
    (folder / ".gitignore").write_text("*.txt\n!keep.txt\nempty/\n")
    (folder / "drop.txt").write_text("hidden needle")
    (folder / "keep.txt").write_text("nested needle")
    (folder / "empty").mkdir()
    tools, _, _ = make_tools(ignored_repo)
    assert tools.list_dir("nested").output.splitlines() == [".gitignore", "keep.txt"]
    assert tools.search("needle").output.splitlines() == [
        "nested/keep.txt:1:nested needle",
        "visible.txt:1:visible needle",
    ]


@pytest.mark.parametrize("tool", ["read_file", "write_file", "edit_file", "list_dir"])
@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink"])
def test_escape_refusal_emits_event_without_touching_file(
    tmp_path: Path, tool: str, kind: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("old")
    (root / "escape").symlink_to(outside)
    path = {"parent": "../outside.txt", "absolute": str(outside), "symlink": "escape"}[
        kind
    ]
    tools, events, _ = make_tools(root)
    with pytest.raises(SandboxRefusal):
        if tool == "read_file":
            tools.read_file(path)
        elif tool == "write_file":
            tools.write_file(path, "new")
        elif tool == "edit_file":
            tools.edit_file(path, "old", "new")
        else:
            tools.list_dir(path)
    assert outside.read_text() == "old"
    assert len(events) == 1
    assert isinstance(events[0], ToolRefused)
    assert events[0].tool == tool and events[0].room_id == "T08"
    assert events[0].reason and len(events[0].reason) <= 200


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "nested/private.pem",
        "private.key",
        "id_rsa",
        "nested/id_ed25519",
    ],
)
@pytest.mark.parametrize("tool", ["read_file", "write_file", "edit_file"])
def test_secrets_are_refused_for_read_and_write(
    tmp_path: Path, path: str, tool: str
) -> None:
    secret = tmp_path / path
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("PRIVATE-FILE-CONTENT-123")
    tools, events, _ = make_tools(tmp_path)
    with pytest.raises(SandboxRefusal):
        if tool == "read_file":
            tools.read_file(path)
        elif tool == "write_file":
            tools.write_file(path, "changed")
        else:
            tools.edit_file(path, "PRIVATE-FILE-CONTENT-123", "changed")
    assert secret.read_text() == "PRIVATE-FILE-CONTENT-123"
    assert len(events) == 1 and isinstance(events[0], ToolRefused)
    assert "PRIVATE-FILE-CONTENT-123" not in events[0].model_dump_json()


@pytest.mark.parametrize("kind", ["outside", "secret", "alias"])
def test_refusal_precedes_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (root / ".env").write_text("private")
    (root / "alias").symlink_to(root / ".env")
    tools, _, _ = make_tools(root)
    name = {"outside": str(outside), "secret": ".env", "alias": "alias"}[kind]

    def forbidden_open(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Refused file was opened")

    # File content I/O must never occur, even if a refusal is raised later.
    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(SandboxRefusal):
        tools.read_file(name)
    with pytest.raises(SandboxRefusal):
        tools.write_file(name, "changed")
    with pytest.raises(SandboxRefusal):
        tools.edit_file(name, "private", "changed")


@pytest.mark.parametrize(
    "content, old, count",
    [
        ("hello", "absent", 0),
        ("same same", "same", 2),
        ("hello", "", 6),
        ("aaa", "aa", 2),
    ],
)
def test_edit_requires_exactly_one_match(
    tmp_path: Path, content: str, old: str, count: int
) -> None:
    path = tmp_path / "note.txt"
    path.write_text(content)
    tools, _, _ = make_tools(tmp_path)
    with pytest.raises(ToolError, match=f"{count} matches"):
        tools.edit_file("note.txt", old, "new")
    assert path.read_text() == content


def test_read_write_edit_and_line_range(tmp_path: Path) -> None:
    tools, _, _ = make_tools(tmp_path)
    tools.write_file("nested/note.txt", "one\ntwo\nthree\n")
    assert tools.read_file("nested/note.txt", 2, 3).output == "two\nthree\n"
    tools.edit_file("nested/note.txt", "two", "TWO")
    assert (tmp_path / "nested/note.txt").read_text() == "one\nTWO\nthree\n"
    with pytest.raises(ToolError, match="line"):
        tools.read_file("nested/note.txt", 0, 3)
    with pytest.raises(ToolError, match="Cannot read"):
        tools.read_file("absent.txt")


def test_list_and_search_filter_protected_paths_and_symlinks(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/note.py").write_text("hello\nmatch needle\n")
    (tmp_path / "note.txt").write_text("needle\n")
    (tmp_path / ".env").write_text("SECRET needle")
    (tmp_path / "private.key").write_text("SECRET needle")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("SECRET needle")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("SECRET needle")
    (tmp_path / "escape").symlink_to(outside)
    (tmp_path / "alias").symlink_to(tmp_path / ".env")
    tools, _, _ = make_tools(tmp_path)
    assert tools.list_dir(".").output.splitlines() == ["nested/", "note.txt"]
    assert tools.search("needle", "*.py").output == "nested/note.py:2:match needle"
    assert tools.search("needle").output.splitlines() == [
        "nested/note.py:2:match needle",
        "note.txt:1:needle",
    ]
    with pytest.raises(ToolError, match="pattern"):
        tools.search("[")


def test_denied_shell_command_never_runs(tmp_path: Path) -> None:
    tools, events, _ = make_tools(tmp_path)
    result = tools.run_shell("printf ran > marker.txt")
    assert result.output == "denied by user"
    assert result.exit_code is None
    assert not (tmp_path / "marker.txt").exists()
    assert [event.type for event in events] == [
        "approval_requested",
        "approval_answered",
    ]


@pytest.fixture
def completed_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run short real commands to completion before advancing virtual time.

    FakeClock cannot drive the OS scheduler. Keep actual cwd, output and exit
    behavior, but separate these assertions from the deadline test below.
    """
    launch = subprocess.Popen

    def completed(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = launch(*args, **kwargs)
        process.wait()
        return process

    monkeypatch.setattr(subprocess, "Popen", completed)


def test_approved_shell_runs_in_root_and_captures_exit_code(
    tmp_path: Path, completed_shell: None
) -> None:
    tools, _, _ = make_tools(tmp_path, allow=True)
    result = tools.run_shell(
        "printf ran > marker.txt; printf stdout; printf stderr >&2; exit 7"
    )
    assert result.exit_code == 7 and not result.timed_out
    assert result.output == "stdoutstderr"
    assert (tmp_path / "marker.txt").read_text() == "ran"


def test_shell_timeout_uses_clock_and_kills_process(tmp_path: Path) -> None:
    tools, _, clock = make_tools(tmp_path, allow=True)
    before = clock.now()
    command = f"{shlex.quote(sys.executable)} -c 'while True: pass'"
    result = tools.run_shell(command)
    assert result.timed_out and result.exit_code != 0
    assert "timed out" in result.output
    assert (clock.now() - before).total_seconds() == pytest.approx(5)


@pytest.mark.parametrize("tool", ["read_file", "list_dir", "search", "run_shell"])
def test_observations_are_utf8_bounded_with_visible_marker(
    tmp_path: Path, tool: str, completed_shell: None
) -> None:
    tools, _, _ = make_tools(tmp_path, allow=True, kb=1)
    (tmp_path / "long.txt").write_text("é" * 3000)
    if tool == "read_file":
        result = tools.read_file("long.txt")
    elif tool == "search":
        result = tools.search("é")
    elif tool == "list_dir":
        for index in range(100):
            (tmp_path / f"{index:03}-{'x' * 40}").touch()
        result = tools.list_dir(".")
    else:
        result = tools.run_shell("printf '%03000d' 0")
    assert result.truncated
    assert "[truncated]" in result.output
    assert len(result.output.encode("utf-8")) <= 1024
    assert "�" not in result.output
