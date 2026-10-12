import subprocess
from pathlib import Path

import pytest

from specvania.agent.sandbox import Sandbox, SandboxRefusal


@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink"])
def test_repository_escape_is_refused(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "escape").symlink_to(outside)
    path = {"parent": "../outside.txt", "absolute": str(outside), "symlink": "escape"}
    with pytest.raises(SandboxRefusal, match="repository root"):
        Sandbox(root).resolve(path[kind])
    assert outside.read_text() == "outside"


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".envrc",
        "a.pem",
        "a.key",
        "id_rsa",
        "nested/id_ed25519",
        ".git/config",
    ],
)
def test_protected_paths_are_refused(tmp_path: Path, path: str) -> None:
    with pytest.raises(SandboxRefusal):
        Sandbox(tmp_path).resolve(path)


def test_symlink_alias_cannot_expose_secret(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("secret")
    (tmp_path / "ordinary.txt").symlink_to(secret)
    with pytest.raises(SandboxRefusal, match="protected"):
        Sandbox(tmp_path).resolve("ordinary.txt")


def test_ignored_files_are_refused(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, capture_output=True
    )
    (tmp_path / ".gitignore").write_text("ignored.txt\nignored_dir/\n")
    (tmp_path / "ignored.txt").write_text("ignored content")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir/private.txt").write_text("ignored content")
    sandbox = Sandbox(tmp_path)
    for path in ("ignored.txt", "ignored_dir", "ignored_dir/private.txt"):
        with pytest.raises(SandboxRefusal, match="ignored"):
            sandbox.resolve(path)


def test_in_root_paths_resolve_for_existing_and_new_files(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source")
    (tmp_path / "alias").symlink_to(tmp_path / "source.txt")
    sandbox = Sandbox(tmp_path)
    assert sandbox.resolve("alias") == tmp_path / "source.txt"
    assert sandbox.resolve(str(tmp_path / "source.txt")) == tmp_path / "source.txt"
    assert sandbox.resolve("new/note.txt") == tmp_path / "new/note.txt"
