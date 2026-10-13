"""Local Git operations for a run; no remote or destructive operations."""

import re
import subprocess
from pathlib import Path

from specvania.agent.sandbox import Sandbox, SandboxRefusal


class GitError(Exception):
    """The player can fix repository state, Git identity or permissions."""


class GitOps:
    def __init__(self, directory: Path) -> None:
        self.root = directory.resolve()
        self.root = Path(self._git("rev-parse", "--show-toplevel").strip())

    def _git(
        self,
        *args: str,
        allowed_codes: tuple[int, ...] = (0,),
        binary_marker: bool = False,
    ) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                check=False,
            )
        except OSError:
            raise GitError("Cannot run Git; check its installation.") from None
        if result.returncode not in allowed_codes:
            # Git diagnostics can contain paths, credentials or hook output.
            raise GitError(
                f"Git {args[0]} failed; check repository state, "
                "identity and permissions."
            )
        if binary_marker:
            if b"\0" in result.stdout:
                return "Binary file (diff omitted).\n"
            try:
                return result.stdout.decode("utf-8")
            except UnicodeError:
                return "Binary file (diff omitted).\n"
        return result.stdout.decode("utf-8")

    def check(self, *, allow_dirty: bool = False) -> None:
        self.head()  # A run needs an existing HEAD, not an unborn repository.
        if self._git("status", "--porcelain") and not allow_dirty:
            raise GitError("Git tree is dirty; commit changes or use --allow-dirty.")

    def dirty_paths(self) -> tuple[str, ...]:
        """Porcelain NUL records preserve spaces, newlines and renamed paths."""
        records = iter(self._git("status", "--porcelain=v1", "-z").split("\0"))
        paths: list[str] = []
        for record in records:
            if not record:
                continue
            paths.append(record[3:])
            if "R" in record[:2] or "C" in record[:2]:
                paths.append(next(records))
        return tuple(dict.fromkeys(paths))

    def save_world(self, slug: str) -> str:
        """Commit this world on the current branch, preserving other index entries."""
        # --only uses working-tree contents of these tracked/new files, ignoring
        # unrelated staged entries and leaving them staged after the commit.
        directory = f".specvania/specs/{slug}/"
        self._git("--literal-pathspecs", "add", "--all", "--", directory)
        if not self._git(
            "--literal-pathspecs",
            "diff",
            "--cached",
            "--name-only",
            "--no-ext-diff",
            "--no-textconv",
            "--",
            directory,
        ):
            return self.head()
        self._git(
            "--literal-pathspecs",
            "commit",
            "--only",
            "-m",
            f"specvania: map {slug}",
            "--",
            directory,
        )
        return self.head()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def code_paths(self, commit: str | None = None) -> tuple[tuple[str, str], ...]:
        """Read changed paths without rename pairs containing protected names."""
        if commit is not None:
            if re.fullmatch(r"[0-9a-fA-F]{7,64}", commit) is None:
                raise GitError("Git commit unavailable; check repository state.")
            records = self._git(
                "show",
                "--format=",
                "--name-status",
                "-z",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                commit,
                "--",
            )
        else:
            records = self._git(
                "diff",
                "--name-status",
                "-z",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
            )
        parts = records.split("\0")
        paths = [(parts[i + 1], parts[i]) for i in range(0, len(parts) - 1, 2)]
        if commit is None:
            paths.extend((name, "A") for name in self._paths("--others"))
        return tuple(sorted(set(paths)))

    def code_diff(self, path: str, *, commit: str | None = None) -> str:
        """One Sandbox-approved path; external diff and textconv never run."""
        options = (
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-color",
            "--no-relative",
            "--src-prefix=a/",
            "--dst-prefix=b/",
        )
        if commit is not None:
            return self._git(
                "--literal-pathspecs",
                "show",
                "--format=",
                *options,
                commit,
                "--",
                path,
                binary_marker=True,
            )
        if path in self._paths("--others"):
            return self._git(
                "diff",
                *options,
                "--no-index",
                "--",
                "/dev/null",
                path,
                allowed_codes=(0, 1),
                binary_marker=True,
            )
        return self._git(
            "--literal-pathspecs",
            "diff",
            *options,
            "HEAD",
            "--",
            path,
            binary_marker=True,
        )

    def code_files(self) -> tuple[str, ...]:
        """Tracked and non-ignored untracked candidates; caller applies Sandbox."""
        return tuple(sorted(set(self._paths("--cached", "--others"))))

    def origin_url(self) -> str:
        """Read local remote metadata only; a missing origin never blocks a run."""
        try:
            return self._git("remote", "get-url", "origin").strip()
        except (GitError, UnicodeError):
            return ""

    def create_branch(self, run_id: str) -> str:
        branch = f"specvania/{run_id}"
        self._git("switch", "-c", branch)
        return branch

    def stash_room(self, run_id: str, room_id: str) -> str | None:
        """Preserve skipped work locally; return a stable recovery id, if needed."""
        if not self._git("status", "--porcelain"):
            return None
        self._git(
            "stash",
            "push",
            "--include-untracked",
            "-m",
            f"specvania {run_id} {room_id} skipped",
        )
        stash = self._git("rev-parse", "refs/stash").strip()
        if self._git("status", "--porcelain"):
            raise GitError(
                f"Work saved in stash {stash}, but the tree remains dirty; "
                "inspect it before continuing."
            )
        return stash

    def _paths(self, *selection: str) -> tuple[str, ...]:
        return tuple(
            name
            for name in self._git(
                "ls-files", "-z", *selection, "--exclude-standard"
            ).split("\0")
            if name
        )

    def diff(self, base: str) -> str:
        """Include staged edits, working edits, deletions and new safe files."""
        diff = self._git("diff", "--no-ext-diff", "--no-textconv", base, "--")
        resolve = Sandbox(self.root).walk_resolver()
        for name in self._paths("--others"):
            try:
                path = resolve(name)
            except SandboxRefusal:
                continue
            if path.is_file():
                diff += self._git(
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-index",
                    "--",
                    "/dev/null",
                    name,
                    allowed_codes=(0, 1),
                )
        return diff

    def commit_room(self, room_id: str, title: str) -> str:
        resolve = Sandbox(self.root).walk_resolver()
        paths: list[str] = []
        for name in self._paths("--cached", "--others"):
            try:
                resolve(name)
            except SandboxRefusal:
                continue
            paths.append(name)
        if paths:
            self._git("--literal-pathspecs", "add", "--all", "--", *paths)
        # A reviewed no-op room still has exactly one rollback point.
        self._git("commit", "--allow-empty", "-m", f"specvania({room_id}): {title}")
        return self.head()

    def area_diff(self, commits: tuple[str, ...]) -> str:
        """Only this area's room commits, even when door order interleaves areas."""
        return "".join(
            self._git("show", "--format=", "--no-ext-diff", "--no-textconv", commit)
            for commit in commits
        )
