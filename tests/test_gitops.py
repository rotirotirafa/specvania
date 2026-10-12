"""D7 exercised against disposable repositories, never this worktree's Git."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from specvania.run.gitops import GitError, GitOps


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def initialize_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init")
    git(root, "config", "user.name", "Specvania test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text(
        ".specvania/ledger.db*\n.specvania/runs/\n.specvania/gate-marker\nignored/\n"
    )
    (root / "tracked.txt").write_text("original\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    initialize_repo(root)
    return root


def test_requires_repository_and_existing_head(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        GitOps(tmp_path)
    git(tmp_path, "init")
    with pytest.raises(GitError):
        GitOps(tmp_path).check()


@pytest.mark.parametrize("dirty", ["tracked", "staged", "untracked"])
def test_dirty_tree_refused_unless_allowed(repo: Path, dirty: str) -> None:
    path = repo / ("new.txt" if dirty == "untracked" else "tracked.txt")
    path.write_text("changed\n")
    if dirty == "staged":
        git(repo, "add", "tracked.txt")
    ops = GitOps(repo)
    with pytest.raises(GitError, match="--allow-dirty"):
        ops.check()
    ops.check(allow_dirty=True)


def test_branch_from_head_commits_once_and_never_pushes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    original = subprocess.run

    def recorded(command: list[str], **kwargs: Any) -> Any:
        commands.append(command)
        assert "push" not in command
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorded)
    ops = GitOps(repo)
    base = ops.head()
    ops.check()
    assert ops.create_branch("20000101-000000-abcd") == "specvania/20000101-000000-abcd"
    assert ops.head() == base
    (repo / "tracked.txt").write_text("changed\n")
    commit = ops.commit_room("T11", "Engine")
    assert (
        git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        == "specvania/20000101-000000-abcd"
    )
    assert git(repo, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert git(repo, "log", "-1", "--format=%s") == "specvania(T11): Engine"
    assert commit == ops.head()
    assert commands and all("push" not in command for command in commands)


def test_diff_and_commit_include_staged_unstaged_new_and_deleted_files(
    repo: Path,
) -> None:
    (repo / "deleted.txt").write_text("remove\n")
    git(repo, "add", "deleted.txt")
    git(repo, "commit", "-m", "seed deletion")
    ops = GitOps(repo)
    base = ops.head()
    (repo / "tracked.txt").write_text("staged\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("working\n")
    (repo / "deleted.txt").unlink()
    (repo / "new.txt").write_text("new contents\n")
    (repo / ".env").write_text("FAKE_SECRET\n")
    (repo / "ignored").mkdir()
    (repo / "ignored/data.txt").write_text("IGNORED\n")
    diff = ops.diff(base)
    assert "+working" in diff and "+new contents" in diff
    assert "deleted file mode" in diff
    assert "FAKE_SECRET" not in diff and "IGNORED" not in diff
    commit = ops.commit_room("T01", "Files")
    assert git(repo, "show", "HEAD:new.txt") == "new contents"
    assert git(repo, "show", "HEAD:tracked.txt") == "working"
    assert ".env" not in git(repo, "ls-files").splitlines()
    assert ops.area_diff((commit,)) == git(repo, "show", "--format=", commit) + "\n"


def test_noop_room_still_has_one_commit(repo: Path) -> None:
    ops = GitOps(repo)
    base = ops.head()
    ops.commit_room("T01", "Already correct")
    assert git(repo, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert ops.diff(base) == ""


def test_git_errors_do_not_expose_diagnostics(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops = GitOps(repo)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "SECRET"),
    )
    with pytest.raises(GitError) as error:
        ops.head()
    assert "SECRET" not in str(error.value)


def test_stash_preserves_staged_edits_deletions_and_untracked_files(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (repo / "deleted.txt").write_text("remove\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "seed")
    (repo / "tracked.txt").write_text("staged\n")
    git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("unstaged\n")
    (repo / "deleted.txt").unlink()
    (repo / "new.txt").write_text("saved new file\n")
    (repo / "ignored").mkdir()
    (repo / "ignored/data.txt").write_text("ignored file\n")
    original = subprocess.run
    commands: list[list[str]] = []

    def recorded(command: list[str], **kwargs: Any) -> Any:
        commands.append(command)
        assert command[3] not in ("push", "clean", "reset")
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorded)
    stash = GitOps(repo).stash_room("20000101-000000-abcd", "T01")
    assert stash is not None
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "tracked.txt").read_text() == "original\n"
    assert (repo / "deleted.txt").read_text() == "remove\n"
    assert not (repo / "new.txt").exists()
    assert (repo / "ignored/data.txt").read_text() == "ignored file\n"
    assert "specvania 20000101-000000-abcd T01 skipped" in git(repo, "stash", "list")
    assert git(repo, "show", f"{stash}:tracked.txt") == "unstaged"
    assert git(repo, "show", f"{stash}^2:tracked.txt") == "staged"
    assert git(repo, "show", f"{stash}^3:new.txt") == "saved new file"
    assert "deleted.txt" not in git(repo, "ls-tree", "--name-only", stash)
    assert any(command[3:5] == ["stash", "push"] for command in commands)
    assert GitOps(repo).stash_room("20000101-000000-abcd", "T02") is None
    assert len(git(repo, "stash", "list").splitlines()) == 1


def test_missing_or_unreadable_origin_returns_empty_url(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops = GitOps(repo)
    assert ops.origin_url() == ""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 128, "", "private failure"
        ),
    )
    assert ops.origin_url() == ""
