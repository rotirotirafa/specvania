"""Warrior file tools and approved shell execution; no terminal rendering."""

import fnmatch
import math
import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from specvania.agent.approval import ApprovalBroker
from specvania.agent.sandbox import Sandbox, SandboxRefusal
from specvania.clock import Clock
from specvania.run.events import Event, ToolRefused


class ToolError(Exception):
    """A tool failed for a reason the Warrior or player can fix."""


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    output: str
    exit_code: int | None = None
    truncated: bool = False
    timed_out: bool = False


class Tools:
    def __init__(
        self,
        sandbox: Sandbox,
        *,
        broker: ApprovalBroker,
        clock: Clock,
        emit: Callable[[Event], None],
        run_id: str,
        room_id: str,
        observation_kb: int,
        shell_timeout: float,
    ) -> None:
        if observation_kb <= 0:
            raise ToolError("observation_kb must be positive.")
        if not math.isfinite(shell_timeout) or shell_timeout <= 0:
            raise ToolError("The shell timeout must be finite and positive.")
        self._sandbox = sandbox
        self._broker = broker
        self._clock = clock
        self._emit = emit
        self._run_id = run_id
        self._room_id = room_id
        self._limit = observation_kb * 1024
        self._shell_timeout = shell_timeout

    def _resolve(self, tool: str, path: str) -> Path:
        try:
            return self._sandbox.resolve(path)
        except SandboxRefusal as error:
            self._emit(
                ToolRefused(
                    ts=self._clock.now(),
                    run_id=self._run_id,
                    seq=0,
                    room_id=self._room_id,
                    tool=tool,
                    reason=str(error)[:200],
                )
            )
            raise

    def _observation(
        self, output: str, *, exit_code: int | None = None, timed_out: bool = False
    ) -> Observation:
        data = output.encode("utf-8")
        truncated = len(data) > self._limit
        if truncated:
            marker = "\n[truncated]"
            output = (
                data[: self._limit - len(marker.encode())].decode(
                    "utf-8", errors="ignore"
                )
                + marker
            )
        return Observation(
            output=output, exit_code=exit_code, truncated=truncated, timed_out=timed_out
        )

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ToolError(
                "Cannot read file; check its path, permissions and UTF-8 encoding."
            ) from error

    def read_file(
        self, path: str, start: int | None = None, end: int | None = None
    ) -> Observation:
        resolved = self._resolve("read_file", path)
        first = 1 if start is None else start
        if first < 1 or (end is not None and end < first):
            raise ToolError(
                "File line ranges are 1-based and inclusive; end must follow start."
            )
        content = self._read(resolved)
        if start is not None or end is not None:
            content = "".join(content.splitlines(keepends=True)[first - 1 : end])
        return self._observation(content)

    def write_file(self, path: str, content: str) -> Observation:
        resolved = self._resolve("write_file", path)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ToolError(
                "Cannot write file; check its path, permissions and encoding."
            ) from error
        return self._observation("file written")

    def edit_file(self, path: str, old: str, new: str) -> Observation:
        resolved = self._resolve("edit_file", path)
        content = self._read(resolved)
        # Count overlapping occurrences too: 'aa' in 'aaa' is ambiguous.
        count = sum(content.startswith(old, index) for index in range(len(content) + 1))
        if count != 1 or not old:
            raise ToolError(
                f"edit_file requires exactly one match; old has {count} matches."
            )
        try:
            resolved.write_text(content.replace(old, new, 1), encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ToolError(
                "Cannot edit file; check its permissions and encoding."
            ) from error
        return self._observation("file edited")

    def list_dir(self, path: str) -> Observation:
        resolved = self._resolve("list_dir", path)
        resolve = self._sandbox.walk_resolver()
        entries: list[str] = []
        try:
            for child in sorted(resolved.iterdir()):
                try:
                    safe = resolve(str(child))
                except SandboxRefusal:
                    continue
                entries.append(child.name + ("/" if safe.is_dir() else ""))
        except OSError as error:
            raise ToolError(
                "Cannot list directory; check its path and permissions."
            ) from error
        return self._observation("\n".join(entries))

    def _files(self) -> Iterator[tuple[str, Path]]:
        resolve = self._sandbox.walk_resolver()

        def failed(error: OSError) -> None:
            raise ToolError(
                "Cannot search repository; check directory permissions."
            ) from error

        for directory, dirs, files in os.walk(
            self._sandbox.root, followlinks=False, onerror=failed
        ):
            safe_dirs: list[str] = []
            for name in sorted(dirs):
                try:
                    resolve(str(Path(directory) / name))
                except SandboxRefusal:
                    continue
                safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in sorted(files):
                path = Path(directory) / name
                try:
                    safe = resolve(str(path))
                except SandboxRefusal:
                    continue
                yield path.relative_to(self._sandbox.root).as_posix(), safe

    def search(self, pattern: str, glob: str | None = None) -> Observation:
        try:
            expression = re.compile(pattern)
        except re.error as error:
            raise ToolError(
                "Invalid search pattern; use a valid regular expression."
            ) from error
        matches: list[tuple[str, int, str]] = []
        for name, path in self._files():
            if glob is not None and not fnmatch.fnmatchcase(name, glob):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            except OSError as error:
                raise ToolError("Cannot search file; check its permissions.") from error
            for number, line in enumerate(content.splitlines(), 1):
                if expression.search(line):
                    matches.append((name, number, line))
        return self._observation(
            "\n".join(
                f"{name}:{number}:{line}" for name, number, line in sorted(matches)
            )
        )

    def run_shell(self, command: str) -> Observation:
        """Approval authorizes a local shell, not an OS-level sandbox (D8).

        Output is spooled to a file to avoid pipe deadlock and unbounded memory.
        The Clock controls all polling deadlines. On POSIX a separate process
        group lets timeout/interruption terminate the shell and its children.
        """
        resolution = self._broker.request(command, self._sandbox.root)
        if not resolution.answer:
            return self._observation("denied by user")
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=self._sandbox.root,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as error:
                raise ToolError("Cannot run the approved shell command.") from error
            timed_out = False
            before = self._clock.now()
            try:
                while process.poll() is None:
                    remaining = (
                        self._shell_timeout
                        - (self._clock.now() - before).total_seconds()
                    )
                    if remaining <= 0:
                        timed_out = True
                        break
                    self._clock.sleep(min(0.05, remaining))
            finally:
                if process.poll() is None or timed_out:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
            output.seek(0)
            content = output.read(self._limit + 1).decode("utf-8", errors="replace")
            if timed_out:
                content = "command timed out\n" + content
            return self._observation(
                content, exit_code=process.returncode, timed_out=timed_out
            )
