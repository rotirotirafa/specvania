"""T26 room code contracts: disposable Git repositories, no network."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from test_gitops import git, initialize_repo

from specvania.clock import FakeClock
from specvania.run.bus import EventBus
from specvania.run.events import (
    Event,
    RedactionGuard,
    RoomCleared,
    RoomEntered,
    RoomInfo,
    RunStarted,
    WorldLoaded,
)
from specvania.ui.server import UIServer
from specvania.ui.studio import Studio

ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    initialize_repo(root)
    return root


def server(
    root: Path,
    entries: str = "tracked.txt",
    *,
    active: bool = False,
    commit: str | None = None,
) -> UIServer:
    world = root / "world"
    world.mkdir(exist_ok=True)
    (world / "spec.md").write_text("")
    (world / "tasks.md").write_text(
        "## A1 · Files\nboss: none\n\n### T01 · Read files\n"
        f"depends_on: none\ncovers: none\nfiles: {entries}\n\n"
        "Checklist:\n- [ ] Read files\n"
    )
    clock = FakeClock()
    fields: dict[str, Any] = {"ts": clock.now(), "run_id": "code"}
    events: list[Event] = [
        RunStarted(**fields, seq=1, feature="world", branch="specvania/code"),
        WorldLoaded(
            **fields, seq=2, rooms=(RoomInfo(id="T01", area="A1", title="Files"),)
        ),
    ]
    if active or commit:
        events.append(RoomEntered(**fields, seq=3, room_id="T01"))
    if commit:
        events.append(RoomCleared(**fields, seq=4, room_id="T01", commit=commit))
    guard = RedactionGuard()
    return UIServer(
        guard=guard,
        clock=clock,
        run_id="code",
        bus=EventBus(guard),
        live_snapshot=lambda: tuple(events),
        repository_root=root,
        world_directory=world,
        port=8765,
    )


def code(ui: UIServer, room: str = "T01") -> Any:
    with TestClient(ui.app, base_url=ui.origin) as client:
        return client.get(
            f"/api/rooms/{room}/code", params={"view_token": ui.view_token}
        )


def test_cleared_room_returns_only_its_commit_files_and_diff(repo: Path) -> None:
    (repo / "tracked.txt").write_text("cleared change\n")
    (repo / "added space.txt").write_text("committed addition\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "room cleared")
    commit = git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("later change\n")
    (repo / "unrelated.txt").write_text("not in commit\n")
    response = code(server(repo, entries="unrelated.txt", commit=commit))
    assert response.status_code == 200
    result = response.json()
    assert result["room_id"] == "T01"
    assert result["kind"] == "commit" and result["commit"] == commit
    assert result["reason"] is None and result["truncated"] is False
    files = {file["path"]: file for file in result["files"]}
    assert set(files) == {"tracked.txt", "added space.txt"}
    assert files["tracked.txt"]["status"] == "M"
    assert "-original" in files["tracked.txt"]["diff"]
    assert "+cleared change" in files["tracked.txt"]["diff"]
    assert "later change" not in response.text
    assert files["added space.txt"]["status"] == "A"
    assert "+committed addition" in files["added space.txt"]["diff"]
    assert all("content" not in file for file in files.values())


def test_active_room_returns_working_diff_including_staged_and_untracked(
    repo: Path,
) -> None:
    (repo / "tracked.txt").write_text("staged\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("working\n")
    (repo / "new.txt").write_text("untracked\n")
    response = code(server(repo, active=True))
    assert response.status_code == 200
    result = response.json()
    assert result["kind"] == "working" and result["commit"] is None
    files = {file["path"]: file for file in result["files"]}
    assert set(files) == {"tracked.txt", "new.txt", "world/spec.md", "world/tasks.md"}
    assert "-original" in files["tracked.txt"]["diff"]
    assert "+working" in files["tracked.txt"]["diff"]
    assert "+untracked" in files["new.txt"]["diff"]


def test_other_room_returns_declared_files_and_expands_directories(repo: Path) -> None:
    (repo / "folder").mkdir()
    (repo / "folder/new.txt").write_text("new\n")
    (repo / "outside-selection.txt").write_text("not selected\n")
    result = code(server(repo, "tracked.txt, folder/, missing.txt")).json()
    assert result["kind"] == "files" and result["commit"] is None
    assert result["files"] == [
        {
            "path": "folder/new.txt",
            "status": "file",
            "content": "new\n",
            "truncated": False,
        },
        {
            "path": "tracked.txt",
            "status": "file",
            "content": "original\n",
            "truncated": False,
        },
    ]


def assert_omitted(repo: Path, entry: str) -> None:
    result = code(server(repo, entry)).json()
    assert result["files"] == []


def test_git_directory_is_omitted(repo: Path) -> None:
    assert_omitted(repo, ".git/config, .git/")


def test_env_is_omitted(repo: Path) -> None:
    (repo / ".env").write_text("private\n")
    assert_omitted(repo, ".env")


@pytest.mark.parametrize(
    "entry", ["private.pem", "private.key", "id_rsa", ".env.local"]
)
def test_other_sandbox_secret_patterns_are_omitted(repo: Path, entry: str) -> None:
    (repo / entry).write_text("private\n")
    assert_omitted(repo, entry)


def test_ignored_file_is_omitted_even_when_tracked(repo: Path) -> None:
    (repo / "ignored").mkdir()
    (repo / "ignored/file.txt").write_text("private\n")
    git(repo, "add", "-f", "ignored/file.txt")
    assert_omitted(repo, "ignored/file.txt, ignored/")


def test_absolute_path_outside_root_is_omitted(repo: Path) -> None:
    outside = repo.parent / "outside.txt"
    outside.write_text("private\n")
    assert_omitted(repo, str(outside))


def test_symlink_outside_root_is_omitted(repo: Path) -> None:
    outside = repo.parent / "outside.txt"
    outside.write_text("private\n")
    (repo / "link.txt").symlink_to(outside)
    assert_omitted(repo, "link.txt")


def test_parent_traversal_files_entry_is_omitted(repo: Path) -> None:
    (repo.parent / "outside.txt").write_text("private\n")
    assert_omitted(repo, "../outside.txt, folder/../tracked.txt")


def test_committed_env_is_omitted_from_cleared_diff(repo: Path) -> None:
    (repo / ".env").write_text("committed private value\n")
    (repo / "tracked.txt").write_text("public\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "unsafe historical commit")
    response = code(server(repo, commit=git(repo, "rev-parse", "HEAD")))
    assert {file["path"] for file in response.json()["files"]} == {"tracked.txt"}
    assert "committed private value" not in response.text
    assert ".env" not in response.text


def test_active_diff_omits_secret_ignored_and_outside_symlink_paths(repo: Path) -> None:
    (repo / ".env").write_text("private env\n")
    (repo / "ignored").mkdir()
    (repo / "ignored/file.txt").write_text("private ignored\n")
    outside = repo.parent / "outside.txt"
    outside.write_text("private outside\n")
    (repo / "link.txt").symlink_to(outside)
    git(repo, "add", "-f", ".env", "ignored/file.txt", "link.txt")
    response = code(server(repo, active=True))
    assert all(
        word not in response.text
        for word in ("private", ".env", "link.txt", "ignored/file.txt")
    )


def test_rename_from_secret_path_never_exposes_old_path_or_diff(repo: Path) -> None:
    (repo / ".env").write_text("old private content\n")
    git(repo, "add", ".env")
    git(repo, "commit", "-m", "historical secret")
    git(repo, "mv", ".env", "public.txt")
    (repo / "public.txt").write_text("public content\n")
    git(repo, "commit", "-am", "move")
    response = code(server(repo, commit=git(repo, "rev-parse", "HEAD")))
    assert ".env" not in response.text and "old private content" not in response.text
    assert "+public content" in response.json()["files"][0]["diff"]


@pytest.mark.parametrize("active", [False, True])
def test_binary_files_are_markers_not_bytes(repo: Path, active: bool) -> None:
    (repo / "binary.dat").write_bytes(b"\x00binary-private-data\xff")
    result = code(server(repo, "binary.dat", active=active)).json()
    file = next(file for file in result["files"] if file["path"] == "binary.dat")
    body = file["diff" if active else "content"]
    assert "binary" in body.lower() and "binary-private-data" not in body


@pytest.mark.parametrize("active", [False, True])
def test_per_file_cap_has_visible_marker_and_flags(repo: Path, active: bool) -> None:
    (repo / "large.txt").write_text("é" * 150_000)
    result = code(server(repo, "large.txt", active=active)).json()
    file = next(file for file in result["files"] if file["path"] == "large.txt")
    body = file["diff" if active else "content"]
    assert len(body.encode()) <= 200_000
    assert "truncated by specvania (200 kB cap)" in body
    assert file["truncated"] is True and result["truncated"] is True


def test_large_tracked_file_reads_are_bounded(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = repo / "logs"
    folder.mkdir()
    large = folder / "large.txt"
    large.write_bytes(b"x" * 5_000_000)
    git(repo, "add", "logs/large.txt")
    ui = server(repo, "logs/")
    original_open = Path.open
    reads: list[tuple[int, int]] = []

    def watched_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        stream = original_open(path, *args, **kwargs)
        if path != large:
            return stream
        wrapped = MagicMock(wraps=stream)
        wrapped.__enter__.return_value = wrapped
        wrapped.__exit__.side_effect = stream.__exit__

        def read(size: int = -1) -> bytes:
            data = stream.read(size)
            reads.append((size, len(data)))
            return data

        wrapped.read.side_effect = read
        return wrapped

    monkeypatch.setattr(Path, "open", watched_open)
    response = code(ui)
    assert response.status_code == 200
    assert reads and all(0 < size <= 201_000 for size, _ in reads)
    assert sum(length for _, length in reads) <= 201_000
    result = response.json()
    file = result["files"][0]
    assert file["path"] == "logs/large.txt"
    assert "truncated by specvania (200 kB cap)" in file["content"]
    assert len(file["content"].encode()) <= 200_000
    assert file["truncated"] is True and result["truncated"] is True


@pytest.mark.parametrize("escaped", [False, True])
def test_bounded_read_redacts_long_secret_across_cap(repo: Path, escaped: bool) -> None:
    ui = server(repo, "secret.txt")
    secret = "unique-prefix-" + "é" * 300 + "-tail"
    ui.guard.register(secret)
    value = json.dumps(secret)[1:-1] if escaped else secret
    (repo / "secret.txt").write_text("x" * 199_900 + value + "y" * 5_000_000)
    response = code(ui)
    assert response.status_code == 200
    body = response.json()["files"][0]["content"]
    assert "unique-prefix" not in body
    assert "truncated by specvania (200 kB cap)" in body


def test_bounded_read_discards_partial_secret_after_redaction_shrinks_body(
    repo: Path,
) -> None:
    ui = server(repo, "secret.txt")
    secret = "unique-prefix-" + "s" * 600 + "-tail"
    ui.guard.register(secret)
    prefix = secret * 300
    # Put a second secret inside the lookahead, straddling its end.
    (repo / "secret.txt").write_text(
        prefix + "x" * (200_600 - len(prefix)) + secret + "y" * 5_000_000
    )
    response = code(ui)
    assert response.status_code == 200
    body = response.json()["files"][0]["content"]
    assert "[redacted]" in body and "unique-prefix" not in body
    assert "truncated by specvania (200 kB cap)" in body
    assert response.json()["files"][0]["truncated"] is True


def test_bounded_read_detects_nul_in_lookahead(repo: Path) -> None:
    ui = server(repo, "binary.dat")
    (repo / "binary.dat").write_bytes(b"x" * 200_005 + b"\0" + b"y" * 5_000_000)
    response = code(ui)
    assert response.status_code == 200
    body = response.json()["files"][0]["content"]
    assert "binary" in body.lower() and "xxxxx" not in body


def test_response_cap_includes_json_encoding_and_has_marker(repo: Path) -> None:
    (repo / "many").mkdir()
    for index in range(12):
        (repo / f"many/{index:02}.txt").write_text('"\\\n' * 30_000)
    response = code(server(repo, "many/"))
    assert response.status_code == 200 and len(response.content) <= 1_000_000
    result = response.json()
    assert result["truncated"] is True
    assert any(
        "truncated by specvania (1 MB cap)" in file["content"] and file["truncated"]
        for file in result["files"]
    )


@pytest.mark.parametrize("active", [False, True])
def test_registered_keys_and_tokens_are_redacted_before_file_cap(
    repo: Path, active: bool, caplog: pytest.LogCaptureFixture
) -> None:
    ui = server(repo, "secret.txt", active=active)
    key = "registered-test-key"
    ui.guard.register(key)
    # Full value crosses the cap, including a diff header in the active case.
    (repo / "secret.txt").write_text(
        f"safe prefix {ui.view_token} {ui.control_token} {key}\n"
        + "x" * 199_700
        + key
        + "z" * 1_000
    )
    with caplog.at_level(logging.DEBUG):
        response = code(ui)
    assert response.status_code == 200
    file = next(
        file for file in response.json()["files"] if file["path"] == "secret.txt"
    )
    body = file["diff" if active else "content"]
    assert "safe prefix [redacted] [redacted] [redacted]" in body
    assert key not in response.text and "registered-test" not in body
    assert ui.view_token not in response.text and ui.control_token not in response.text
    assert "safe prefix" not in caplog.text


def test_secret_straddling_exact_cap_cannot_leak_prefix(repo: Path) -> None:
    ui = server(repo, "secret.txt")
    secret = "unique-prefix-secret-tail"
    ui.guard.register(secret)
    (repo / "secret.txt").write_text("x" * 199_995 + secret + "y" * 100)
    body = code(ui).json()["files"][0]["content"]
    assert "unique" not in body and "truncated by specvania" in body


@pytest.mark.parametrize("token", [None, "wrong"])
def test_room_code_requires_view_token(repo: Path, token: str | None) -> None:
    ui = server(repo)
    with TestClient(ui.app, base_url=ORIGIN) as client:
        response = client.get(
            "/api/rooms/T01/code", params={} if token is None else {"view_token": token}
        )
    assert response.status_code == 401


def test_room_code_accepts_view_cookie_and_enforces_loopback_host(repo: Path) -> None:
    ui = server(repo)
    with TestClient(ui.app, base_url=ORIGIN) as client:
        assert client.get("/", params={"view_token": ui.view_token}).status_code == 200
        assert client.get("/api/rooms/T01/code").status_code == 200
        assert (
            client.get(
                "/api/rooms/T01/code", headers={"host": "foreign.invalid"}
            ).status_code
            == 403
        )


@pytest.mark.parametrize("room", ["T99", "T1", "bad", "T001unknown"])
def test_unknown_or_invalid_room_returns_404(repo: Path, room: str) -> None:
    response = code(server(repo), room)
    assert response.status_code == 404 and response.json()["detail"]


def test_exported_replay_has_no_repository(repo: Path) -> None:
    live = server(repo)
    assert live.live_snapshot is not None
    ui = UIServer(
        guard=RedactionGuard(),
        clock=FakeClock(),
        run_id="code",
        replay=live.live_snapshot(),
        port=8765,
    )
    response = code(ui)
    assert response.status_code == 404 and response.json()["detail"]


def test_studio_without_project_returns_404() -> None:
    studio = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    response = code(studio.server)
    assert response.status_code == 404 and response.json()["detail"]


def test_git_errors_return_non_secret_reason(repo: Path) -> None:
    ui = server(repo, active=True)
    # A genuinely missing object exercises Git's diagnostics without mocking it.
    git(repo, "config", "diff.external", str(repo / "secret-command"))
    head = repo / ".git/HEAD"
    head.write_text("ref: refs/heads/missing-secret-ref\n")
    response = code(ui)
    assert response.status_code == 404
    assert "Git" in response.json()["detail"]
    assert "missing-secret-ref" not in response.text


def test_diff_drivers_are_disabled_and_endpoint_never_writes_git(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = server(repo, active=True)
    (repo / "tracked.txt").write_text("working\n")
    (repo / ".gitattributes").write_text("*.txt diff=unsafe\n")
    git(repo, "config", "diff.unsafe.textconv", "false")
    git(repo, "config", "diff.external", "false")
    original = subprocess.run
    commands: list[list[str]] = []

    def recorded(command: list[str], **kwargs: Any) -> Any:
        commands.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorded)
    response = code(ui)
    assert response.status_code == 200 and "+working" in response.text
    assert all(
        not {"add", "commit", "stash", "checkout", "switch", "push"}.intersection(
            command[3:]
        )
        for command in commands
    )
    for command in commands:
        if "diff" in command or "show" in command:
            assert "--no-ext-diff" in command and "--no-textconv" in command


def test_cleared_diff_still_available_when_world_documents_are_removed(
    repo: Path,
) -> None:
    (repo / "tracked.txt").write_text("committed\n")
    git(repo, "commit", "-am", "room")
    ui = server(repo, commit=git(repo, "rev-parse", "HEAD"))
    (repo / "world/tasks.md").unlink()
    response = code(ui)
    assert response.status_code == 200
    assert "+committed" in response.json()["files"][0]["diff"]


def test_directory_expansion_filters_each_child_and_keeps_tracked_files(
    repo: Path,
) -> None:
    folder = repo / "folder"
    folder.mkdir()
    (folder / "tracked.txt").write_text("tracked child\n")
    git(repo, "add", "folder/tracked.txt")
    git(repo, "commit", "-m", "child")
    (folder / "new.txt").write_text("new child\n")
    (folder / ".env").write_text("private child\n")
    (folder / "ignored.txt").write_text("ignored child\n")
    (repo / ".gitignore").write_text("folder/ignored.txt\n")
    (folder / "link.txt").symlink_to(repo / "folder/.env")
    response = code(server(repo, "folder/"))
    assert {file["path"] for file in response.json()["files"]} == {
        "folder/new.txt",
        "folder/tracked.txt",
    }
    assert "private child" not in response.text and "ignored child" not in response.text


def test_deleted_file_commit_and_initial_commit_diffs(repo: Path) -> None:
    initial = git(repo, "rev-parse", "HEAD")
    assert "+original" in code(server(repo, commit=initial)).json()["files"][1]["diff"]
    (repo / "tracked.txt").unlink()
    git(repo, "commit", "-am", "delete")
    result = code(server(repo, commit=git(repo, "rev-parse", "HEAD"))).json()
    assert result["files"][0]["status"] == "D"
    assert "-original" in result["files"][0]["diff"]


def test_studio_selected_world_uses_open_project_not_process_directory(
    repo: Path,
) -> None:
    from specvania.config import initialize_config
    from specvania.ui.server import OpenProject, SaveWorld

    initialize_config(repo)
    studio = Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)
    world = repo / ".specvania/specs/world"
    world.mkdir(parents=True)
    (world / "spec.md").write_text("")
    (world / "tasks.md").write_text(
        "## A1 · Files\nboss: none\n\n### T01 · Files\ndepends_on: none\n"
        "covers: none\nfiles: tracked.txt\n\nChecklist:\n- [ ] Read files\n"
    )
    studio.control(OpenProject(kind="open_project", path=str(repo)))
    studio.control(SaveWorld(kind="save_world", slug="world"))
    result = code(studio.server).json()
    assert result["kind"] == "files"
    assert result["files"][0]["content"] == "original\n"


def test_cli_plan_ui_reads_its_repository_files(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_planner import world_reply
    from typer.testing import CliRunner

    from specvania import cli
    from specvania.config import initialize_config
    from specvania.provider.fake import ScriptedProvider

    initialize_config(repo)
    (repo / "invoices.py").write_text("invoice code\n")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    monkeypatch.setattr(
        cli, "OpenAIProvider", lambda *args, **kwargs: ScriptedProvider([world_reply()])
    )
    servers: list[UIServer] = []
    monkeypatch.setattr(cli, "start_run_ui", lambda ui, **kwargs: servers.append(ui))
    result = CliRunner().invoke(
        cli.create_app(), ["plan", "invoices", "--no-clarify", "--ui=browser"]
    )
    assert result.exit_code == 0, result.output
    response = code(servers[0])
    assert response.status_code == 200
    assert response.json()["files"][0]["content"] == "invoice code\n"


def test_cli_run_ui_reads_active_attempt_and_never_records_content(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_engine import action, boss, review, write_world
    from typer.testing import CliRunner

    from specvania import cli
    from specvania.config import initialize_config
    from specvania.provider.client import Completion
    from specvania.provider.fake import ScriptedProvider

    initialize_config(repo)
    world = write_world(repo, [("T01", "A1", ())])
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    servers: list[UIServer] = []
    monkeypatch.setattr(cli, "start_run_ui", lambda ui, **kwargs: servers.append(ui))

    def inspect() -> Completion:
        response = code(servers[0])
        assert response.status_code == 200
        assert response.json()["kind"] == "working"
        assert "+unique room content" in response.json()["files"][0]["diff"]
        ledger = servers[0].ledger
        assert ledger is not None
        assert all(
            "unique room content" not in event.model_dump_json()
            for event in ledger.list_events(servers[0].run_id)
        )
        return action()

    provider = ScriptedProvider(
        [
            action("write_file", path="T01.txt", content="unique room content\n"),
            inspect,
            review(),
            boss(),
        ]
    )
    monkeypatch.setattr(cli, "OpenAIProvider", lambda *args, **kwargs: provider)
    result = CliRunner().invoke(cli.create_app(), ["run", str(world), "--ui=browser"])
    assert result.exit_code == 0, result.output
    replay_servers: list[UIServer] = []
    monkeypatch.setattr(
        UIServer, "serve", lambda ui, **kwargs: replay_servers.append(ui)
    )
    result = CliRunner().invoke(cli.create_app(), ["replay", servers[0].run_id])
    assert result.exit_code == 0, result.output
    response = code(replay_servers[0])
    assert response.status_code == 200
    assert response.json()["kind"] == "commit"
    assert "+unique room content" in response.json()["files"][0]["diff"]


def test_replay_demo_endpoint_has_no_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from specvania import cli

    servers: list[UIServer] = []
    monkeypatch.setattr(UIServer, "serve", lambda ui, **kwargs: servers.append(ui))
    result = CliRunner().invoke(cli.create_app(), ["replay", "demo"])
    assert result.exit_code == 0, result.output
    assert code(servers[0]).status_code == 404


@pytest.mark.parametrize("cleared", [False, True])
def test_binary_diff_is_marker_even_when_attributes_force_text(
    repo: Path, cleared: bool
) -> None:
    (repo / ".gitattributes").write_text("binary.dat diff\n")
    (repo / "binary.dat").write_bytes(b"\0private-binary-bytes\xff\n")
    commit = None
    if cleared:
        git(repo, "add", ".")
        git(repo, "commit", "-m", "binary")
        commit = git(repo, "rev-parse", "HEAD")
    response = code(server(repo, active=not cleared, commit=commit))
    assert response.status_code == 200
    file = next(
        file for file in response.json()["files"] if file["path"] == "binary.dat"
    )
    assert "binary" in file["diff"].lower()
    assert "private-binary-bytes" not in response.text


def test_metadata_alone_is_response_capped_with_visible_marker() -> None:
    from specvania.ui.server import RoomCode, RoomCodeFile, _cap_room_code

    files = iter(
        [
            RoomCodeFile(path="x" * 999_900, status="file", content=""),
            RoomCodeFile(path="next.txt", status="file", content=""),
        ]
    )
    result = _cap_room_code(RoomCode(room_id="T01", kind="files"), files)
    assert len(result.model_dump_json().encode()) <= 1_000_000
    assert result.truncated
    assert "truncated by specvania" in result.model_dump_json()
