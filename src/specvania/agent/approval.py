"""Synchronous approval broker with cancellable, non-blocking responders."""

import os
import select
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from specvania.clock import Clock
from specvania.run.events import ApprovalAnswered, ApprovalRequested, Event


class ApprovalError(Exception):
    """Approval cannot be obtained; the shell command must not run."""


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    request_id: str
    command: str
    cwd: str


class ApprovalResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    request_id: str
    answer: bool
    responder: str


class ApprovalResponder(Protocol):
    @property
    def name(self) -> str: ...

    def start(self, request: ApprovalRequest, answer: Callable[[bool], None]) -> None:
        """Display a request and retain the callback; never block for input."""
        ...

    def poll(self) -> None:
        """Read available input, without waiting; callbacks may also be threaded."""
        ...

    def cancel(self, request_id: str, resolution: ApprovalResolution | None) -> None:
        """Stop input and show who answered; None means an interrupted request."""
        ...


class ApprovalBroker:
    def __init__(
        self,
        responders: Sequence[ApprovalResponder],
        *,
        clock: Clock,
        emit: Callable[[Event], None],
        run_id: str,
    ) -> None:
        names = [responder.name for responder in responders]
        if len(set(names)) != len(names) or any(
            not name or len(name) > 200 for name in names
        ):
            raise ApprovalError(
                "Approval responders need unique names of 1–200 characters."
            )
        self._responders = tuple(responders)
        self._clock = clock
        self._emit = emit
        self._run_id = run_id
        self._lock = RLock()
        self._active: ApprovalRequest | None = None
        self._resolution: ApprovalResolution | None = None

    def _answer(self, request_id: str, responder: str, answer: bool) -> None:
        with self._lock:
            if (
                self._active is None
                or self._active.request_id != request_id
                or self._resolution is not None
            ):
                return
            self._resolution = ApprovalResolution(
                request_id=request_id, answer=answer, responder=responder
            )

    def request(self, command: str, cwd: Path) -> ApprovalResolution:
        if not self._responders:
            raise ApprovalError("No approval responder is available.")
        request = ApprovalRequest(request_id=uuid4().hex, command=command, cwd=str(cwd))
        with self._lock:
            if self._active is not None:
                raise ApprovalError("An approval request is already active.")
            self._active = request
            self._resolution = None
        started: list[ApprovalResponder] = []
        resolution: ApprovalResolution | None = None
        try:
            with ExitStack() as cleanup:
                for responder in self._responders:
                    started.append(responder)
                    cleanup.callback(
                        lambda responder=responder: responder.cancel(
                            request.request_id, resolution
                        )
                    )
                    responder.start(
                        request,
                        lambda answer, name=responder.name: self._answer(
                            request.request_id, name, answer
                        ),
                    )
                self._emit(
                    ApprovalRequested(
                        ts=self._clock.now(),
                        run_id=self._run_id,
                        seq=0,
                        request_id=request.request_id,
                        command=command[:200],
                        cwd=str(cwd)[:200],
                    )
                )
                while True:
                    for responder in started:
                        with self._lock:
                            if self._resolution is not None:
                                break
                        responder.poll()
                    with self._lock:
                        resolution = self._resolution
                    if resolution is not None:
                        self._emit(
                            ApprovalAnswered(
                                ts=self._clock.now(),
                                run_id=self._run_id,
                                seq=0,
                                request_id=request.request_id,
                                answer=resolution.answer,
                                responder=resolution.responder,
                            )
                        )
                        return resolution
                    self._clock.sleep(0.05)
        finally:
            with self._lock:
                self._active = None
                self._resolution = None


class DesktopResponder:
    """Thread-safe bridge; register this instance alongside the terminal responder.

    Requests and resolutions are displayed through the broker's existing events.
    The UI server validates the control token before calling answer().
    """

    name = "desktop window"

    def __init__(self) -> None:
        self._lock = RLock()
        self._request: ApprovalRequest | None = None
        self._callback: Callable[[bool], None] | None = None

    @property
    def pending(self) -> ApprovalRequest | None:
        with self._lock:
            return self._request

    def start(self, request: ApprovalRequest, answer: Callable[[bool], None]) -> None:
        with self._lock:
            if self._request is not None:
                raise ApprovalError(
                    "The desktop window already has an approval request."
                )
            self._request = request
            self._callback = answer

    def answer(self, request_id: str, answer: bool) -> bool:
        with self._lock:
            if (
                self._request is None
                or request_id != self._request.request_id
                or self._callback is None
            ):
                return False
            callback, self._callback = self._callback, None
        # Do not hold the responder lock while acquiring the broker lock.
        callback(answer)
        return True

    def poll(self) -> None:
        pass

    def cancel(self, request_id: str, resolution: ApprovalResolution | None) -> None:
        with self._lock:
            if self._request is not None and request_id == self._request.request_id:
                self._callback = None
                self._request = None


class ScriptedResponder:
    """One (answer, polls to wait) per request; exhausted scripts fail closed."""

    def __init__(self, name: str, script: Sequence[tuple[bool, int]]) -> None:
        if any(polls < 0 for _, polls in script):
            raise ApprovalError("Scripted responder waits must be non-negative.")
        self.name = name
        self._script = deque(script)
        self._callback: Callable[[bool], None] | None = None
        self._request_id: str | None = None
        self._answer_value = False
        self._polls = 0

    def start(self, request: ApprovalRequest, answer: Callable[[bool], None]) -> None:
        if not self._script:
            raise ApprovalError(
                "The scripted approval responder has no answer remaining."
            )
        self._answer_value, self._polls = self._script.popleft()
        self._request_id = request.request_id
        self._callback = answer

    def poll(self) -> None:
        if self._callback is None:
            return
        if self._polls:
            self._polls -= 1
            return
        callback, self._callback = self._callback, None
        callback(self._answer_value)

    def cancel(self, request_id: str, resolution: ApprovalResolution | None) -> None:
        if request_id == self._request_id:
            self._callback = None
            self._request_id = None


class TerminalResponder:
    """POSIX stdin responder; presentation is injected by cli.py (Rich only there).

    Reading a descriptor rather than TextIO.readline keeps cancellation possible
    even with an incomplete line. The original blocking mode is restored on every
    resolution or interruption. No terminal reads happen in a worker thread.
    """

    name = "terminal"

    def __init__(self, stdin_fd: int, display: Callable[[str], None]) -> None:
        self._fd = stdin_fd
        self._display = display
        self._request_id: str | None = None
        self._callback: Callable[[bool], None] | None = None
        self._was_blocking: bool | None = None
        self._input = bytearray()

    def start(self, request: ApprovalRequest, answer: Callable[[bool], None]) -> None:
        if self._request_id is not None:
            raise ApprovalError("The terminal already has an approval request.")
        self._request_id = request.request_id
        self._callback = answer
        self._input.clear()
        try:
            self._was_blocking = os.get_blocking(self._fd)
            os.set_blocking(self._fd, False)
        except OSError as error:
            raise ApprovalError("Cannot read terminal approval input.") from error
        self._display(f"{request.command}\ncwd: {request.cwd}\nAllow? [y/N]")

    def poll(self) -> None:
        if self._callback is None:
            return
        try:
            ready, _, _ = select.select([self._fd], [], [], 0)
            if not ready:
                return
            chunk = os.read(self._fd, 4096)
        except BlockingIOError:
            return
        except (OSError, ValueError) as error:
            raise ApprovalError("Cannot read terminal approval input.") from error
        self._input.extend(chunk)
        if b"\n" in self._input or not chunk:
            line = bytes(self._input).split(b"\n", 1)[0].strip().lower()
            callback, self._callback = self._callback, None
            callback(bool(chunk) and line in (b"y", b"yes"))

    def cancel(self, request_id: str, resolution: ApprovalResolution | None) -> None:
        if self._request_id != request_id:
            return
        self._request_id = None
        self._callback = None
        self._input.clear()
        if self._was_blocking is not None:
            os.set_blocking(self._fd, self._was_blocking)
            self._was_blocking = None
        if resolution is not None and resolution.responder != self.name:
            word = "approved" if resolution.answer else "denied"
            self._display(f"{word} in {resolution.responder}")
