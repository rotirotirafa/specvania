"""Run the player's configured gates without model-command approval (D8)."""

import math
import os
import signal
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from specvania.clock import Clock
from specvania.config import Config
from specvania.run.events import Event, FrozenModel, GateResult


class GateError(Exception):
    """A gate cannot run; the player can fix its execution settings."""


def _summary(text: str) -> str:
    return text if len(text) <= 200 else text[:188] + " [truncated]"


def _tail_summary(text: str) -> str:
    if len(text) <= 200:
        return text
    prefix = "gate timed out\n" if text.startswith("gate timed out\n") else ""
    marker = "[truncated]\n"
    return prefix + marker + text[-(200 - len(prefix) - len(marker)) :]


def _read_tail(output: BinaryIO, limit: int) -> str:
    output.seek(0, os.SEEK_END)
    size = output.tell()
    marker = "[truncated]\n" if size > limit else ""
    kept = min(size, limit - len(marker.encode()))
    output.seek(size - kept)
    # The cut can fall inside a UTF-8 character; ignore only the incomplete
    # boundary rather than expanding it to replacement characters.
    return marker + output.read(kept).decode("utf-8", errors="ignore")


class GateExecution(FrozenModel):
    """Transient evidence for the Paladin/Warrior; only result is an event."""

    result: GateResult
    output_tail: str


class GateRunner:
    """Synchronous gates with Clock deadlines and bounded event summaries.

    The engine supplies the timeout explicitly. Commands are owned by the
    player, so this runner deliberately has no ApprovalBroker dependency.
    """

    def __init__(
        self,
        *,
        config: Config,
        root: Path,
        clock: Clock,
        emit: Callable[[Event], None],
        run_id: str,
        timeout: float,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise GateError("The gate timeout must be finite and positive.")
        self._root = root.resolve()
        if not self._root.is_dir():
            raise GateError("The gate working directory must exist.")
        self._commands = tuple(config.gates)
        self._clock = clock
        self._emit = emit
        self._run_id = run_id
        self._timeout = timeout
        self._output_limit = config.limits.observation_kb * 1024

    def run(self, room_id: str) -> tuple[GateExecution, ...]:
        """Emit one result per command, including failures; keep config order."""
        results: list[GateExecution] = []
        for command in self._commands:
            code, tail = self._execute(command)
            result = GateResult(
                ts=self._clock.now(),
                run_id=self._run_id,
                seq=0,
                room_id=room_id,
                command=_summary(command),
                exit_code=code,
                output_summary=_tail_summary(tail),
            )
            self._emit(result)
            results.append(GateExecution(result=result, output_tail=tail))
        return tuple(results)

    def _execute(self, command: str) -> tuple[int, str]:
        # Spooling avoids pipe deadlocks and keeps the memory read bounded.
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=self._root,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError:
                return 127, "Cannot start gate; check command and working directory."
            timed_out = False
            started = self._clock.now()
            try:
                while process.poll() is None:
                    remaining = (
                        self._timeout - (self._clock.now() - started).total_seconds()
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
                code = process.wait()
            prefix = "gate timed out\n" if timed_out else ""
            tail = prefix + _read_tail(output, self._output_limit - len(prefix))
            return (124 if timed_out else code), tail
