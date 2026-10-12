"""Repository file boundary, including resolved symlinks and Git ignores."""

import fnmatch
import os
import subprocess
from collections.abc import Callable
from pathlib import Path


class SandboxRefusal(Exception):
    """A file tool tried to access a protected or out-of-root path."""


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise SandboxRefusal("The repository root must be an existing directory.")
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise SandboxRefusal(
                "Install git to enforce ignored repository paths."
            ) from error
        self._git_repository = result.returncode == 0

    def _check_parts(self, path: Path) -> None:
        if any(
            part == ".git"
            or any(
                fnmatch.fnmatchcase(part, pattern)
                for pattern in (".env*", "*.pem", "*.key", "id_*")
            )
            for part in path.parts
        ):
            raise SandboxRefusal(
                "The path is protected by the repository secret policy."
            )

    def _check_ignored(self, path: Path) -> None:
        if not self._git_repository:
            return
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.root),
                    "check-ignore",
                    "--no-index",
                    "--stdin",
                    "-z",
                ],
                input=str(path).encode() + b"\0",
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise SandboxRefusal("Cannot check ignored repository paths.") from error
        if result.returncode == 0:
            raise SandboxRefusal("The path is ignored by the repository.")
        if result.returncode != 1:
            raise SandboxRefusal("Cannot check ignored repository paths.")

    def resolve(self, path: str) -> Path:
        """Check both the requested name and its target before any file access.

        New files are permitted: Path.resolve also resolves existing parents.
        Parent traversal is refused even if normalization would land in-root.
        This is a file boundary, not isolation for an approved shell command.
        """
        return self._resolve(path, self._check_ignored)

    def walk_resolver(self) -> Callable[[str], Path]:
        """Snapshot Git ignores once per walk; never retain a stale ignore cache.

        Untracked ignored directories are collapsed by Git. The second query
        includes tracked files matching ignore rules, preserving check-ignore's
        --no-index semantics. Both names and symlink targets use this snapshot;
        all boundary and secret checks still run for every entry.
        """
        if not self._git_repository:
            return self.resolve
        ignored_files: set[Path] = set()
        ignored_directories: set[Path] = set()
        for selection in (("--others", "--directory"), ("--cached",)):
            try:
                result = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.root),
                        "ls-files",
                        *selection,
                        "--ignored",
                        "--exclude-standard",
                        "-z",
                    ],
                    capture_output=True,
                    check=False,
                )
            except OSError as error:
                raise SandboxRefusal(
                    "Cannot check ignored repository paths."
                ) from error
            if result.returncode != 0:
                raise SandboxRefusal("Cannot check ignored repository paths.")
            for name in result.stdout.split(b"\0"):
                if name:
                    path = Path(os.fsdecode(name))
                    if name.endswith(b"/"):
                        ignored_directories.add(path)
                    else:
                        ignored_files.add(path)
        files = frozenset(ignored_files)
        directories = frozenset(ignored_directories)

        def check_ignored(path: Path) -> None:
            if (
                path in files
                or path in directories
                or any(parent in directories for parent in path.parents)
            ):
                raise SandboxRefusal("The path is ignored by the repository.")

        return lambda path: self._resolve(path, check_ignored)

    def _resolve(self, path: str, check_ignored: Callable[[Path], None]) -> Path:
        requested = Path(path)
        if ".." in requested.parts:
            raise SandboxRefusal("The path must stay inside the repository root.")
        candidate = requested if requested.is_absolute() else self.root / requested
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as error:
            raise SandboxRefusal(
                "The path must stay inside the repository root."
            ) from error
        self._check_parts(relative)
        check_ignored(relative)
        try:
            resolved = candidate.resolve()
            target = resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as error:
            raise SandboxRefusal(
                "The path must resolve inside the repository root."
            ) from error
        self._check_parts(target)
        check_ignored(target)
        return resolved
