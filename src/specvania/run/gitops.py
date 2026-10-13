"""Local Git operations for a run; no remote or destructive operations."""

import codecs
import hashlib
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

from specvania.agent.sandbox import Sandbox, SandboxRefusal


class GitError(Exception):
    """The player can fix repository state, Git identity or permissions."""


class _CodeSandbox(Sandbox):
    def __init__(self, root: Path) -> None:
        # GitOps already verified this canonical repository root. Reuse that
        # result; all inherited path/target/secret checks remain in force.
        self.root = root
        self._git_repository = True


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
        data = self._git_bytes(*args, allowed_codes=allowed_codes)
        if binary_marker:
            return self._code_text(data)
        return data.decode("utf-8")

    def _git_bytes(
        self,
        *args: str,
        allowed_codes: tuple[int, ...] = (0,),
        input: bytes | None = None,
    ) -> bytes:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                check=False,
                input=input,
            )
        except OSError:
            raise GitError("Cannot run Git; check its installation.") from None
        if result.returncode not in allowed_codes:
            # Git diagnostics can contain paths, credentials or hook output.
            raise GitError(
                f"Git {args[0]} failed; check repository state, "
                "identity and permissions."
            )
        return result.stdout

    @staticmethod
    def _code_text(data: bytes) -> str:
        if b"\0" not in data:
            try:
                return data.decode("utf-8")
            except UnicodeError:
                pass
        return "Binary file (diff omitted).\n"

    def code_resolver(self, names: Iterable[str]) -> Callable[[str], Path]:
        """Batch ignores for names, targets and directory ancestors; fail closed.

        Sandbox still checks traversal, root boundaries and secret components
        on every resolution, including a target changed since this snapshot.
        """
        sandbox = _CodeSandbox(self.root)
        checked: set[Path] = {Path(".")}

        def collect(path: Path) -> None:
            checked.add(path)
            checked.update(path.parents)

        for name in names:
            try:
                sandbox._resolve(name, collect)
            except SandboxRefusal:
                continue
        ignored = {
            Path(name.decode("utf-8"))
            for name in self._git_bytes(
                "check-ignore",
                "--no-index",
                "--stdin",
                "-z",
                allowed_codes=(0, 1),
                input=b"".join(
                    b"./" + str(path).encode("utf-8") + b"\0" for path in checked
                ),
            ).split(b"\0")
            if name
        }

        def check(path: Path) -> None:
            if path not in checked or path in ignored:
                raise SandboxRefusal("The path is ignored by the repository.")

        return lambda name: sandbox._resolve(name, check)

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

    def code_paths(
        self, commit: str | None = None, *, untracked: tuple[str, ...] | None = None
    ) -> tuple[tuple[str, str], ...]:
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
            paths.extend(
                (name, "A")
                for name in (
                    self._paths("--others") if untracked is None else untracked
                )
            )
        return tuple(sorted(set(paths)))

    def code_diffs(
        self, paths: tuple[str, ...], *, commit: str | None = None
    ) -> dict[str, str]:
        """One patch call; NUL raw names map ordered patches without parsing quotes."""
        if not paths:
            return {}
        data = self._git_bytes(
            "--literal-pathspecs",
            "show" if commit is not None else "diff",
            *(("--format=",) if commit is not None else ()),
            "--raw",
            "-z",
            "--patch",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--no-color",
            "--no-relative",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            commit or "HEAD",
            "--",
            *paths,
        )
        raw, separator, patch = data.partition(b"\0\0")
        if not separator:
            return {}
        names = [name.decode("utf-8") for name in raw.split(b"\0")[1::2]]
        patches = re.split(rb"(?m)(?=^diff --git )", patch)[1:]
        if len(names) != len(patches) or not set(names).issubset(paths):
            raise GitError("Git diff unavailable; check repository state.")
        return dict(zip(names, map(self._code_text, patches), strict=True))

    def code_attributes(self, names: tuple[str, ...]) -> dict[str, str]:
        """Snapshot binary/text attributes for untracked additions in one call."""
        if not names:
            return {}
        records = self._git_bytes(
            "check-attr",
            "--all",
            "-z",
            "--stdin",
            input=b"".join(name.encode() + b"\0" for name in names),
        ).split(b"\0")
        attributes = dict.fromkeys(names, "unspecified")
        for index in range(0, len(records) - 1, 3):
            if records[index + 1] == b"diff":
                attributes[records[index].decode()] = records[index + 2].decode()
        return attributes

    def code_addition(
        self,
        name: str,
        path: Path,
        *,
        attribute: str = "unspecified",
        limit: int = 200_000,
        margin: int = 5,
    ) -> tuple[str, bool]:
        """Read a bounded addition; omit the blob index if content exceeds the cap."""
        requested = self.root / name
        symlink = requested.is_symlink()
        if symlink:
            data = os.fsencode(os.readlink(requested))
        else:
            with path.open("rb") as stream:
                data = stream.read(limit + margin)
        truncated = len(data) > limit
        index = ""
        if not truncated:
            digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()[:7]
            index = f"index 0000000..{digest}\n"
        mode = (
            "120000"
            if symlink
            else "100755"
            if path.stat().st_mode & 0o100
            else "100644"
        )
        old, new = self._code_quote("a/" + name), self._code_quote("b/" + name)
        header = f"diff --git {old} {new}\nnew file mode {mode}\n"
        if attribute == "unset" or (attribute != "set" and b"\0" in data[:8000]):
            return (
                header + index + f"Binary files /dev/null and {new} differ\n",
                False,
            )
        if b"\0" in data:
            return "Binary file (diff omitted).\n", False
        try:
            text = codecs.getincrementaldecoder("utf-8")().decode(
                data, final=not truncated
            )
        except UnicodeError:
            return "Binary file (diff omitted).\n", False
        if not data:
            hunk = ""
        else:
            lines = text.split("\n")
            if lines[-1] == "":
                lines.pop()
                ending = ""
            else:
                ending = "" if truncated else "\\ No newline at end of file\n"
            count = "1" if len(lines) == 1 else f"1,{len(lines)}"
            hunk = (
                f"@@ -0,0 +{count} @@\n"
                + "".join(f"+{line}\n" for line in lines)
                + ending
            )
        if not data:
            return header + index, False
        tab = "\t" if " " in name else ""
        return (
            header + index + f"--- /dev/null\n+++ {new}{tab}\n" + hunk,
            truncated,
        )

    @staticmethod
    def _code_quote(name: str) -> str:
        escapes = {
            7: r"\a",
            8: r"\b",
            9: r"\t",
            10: r"\n",
            11: r"\v",
            12: r"\f",
            13: r"\r",
            34: r"\"",
            92: r"\\",
        }
        if all(32 <= byte < 127 and byte not in escapes for byte in name.encode()):
            return name
        return (
            '"'
            + "".join(
                escapes.get(byte, chr(byte) if 32 <= byte < 127 else f"\\{byte:03o}")
                for byte in name.encode()
            )
            + '"'
        )

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
