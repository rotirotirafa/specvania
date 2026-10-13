"""Long-lived studio lifetime; plan/run controls belong to T25."""

from collections.abc import Callable
from threading import Event as ThreadEvent
from threading import RLock, Thread

from specvania.clock import Clock
from specvania.run.bus import EventBus
from specvania.run.events import RedactionGuard
from specvania.run.state import RunState
from specvania.ui.server import Shutdown, StateMessage, StudioPhase, UIServer


class StudioError(Exception):
    """The studio cannot perform this transition or finish its active run."""


class Studio:
    def __init__(self, *, guard: RedactionGuard, clock: Clock, port: int = 0) -> None:
        self.phase: StudioPhase = "idle"
        self.stopped = ThreadEvent()
        self._lock = RLock()
        self._worker: Thread | None = None
        self._abort: Callable[[], None] | None = None
        self._run_state: Callable[[], RunState] = RunState
        self.server = UIServer(
            guard=guard,
            clock=clock,
            port=port,
            bus=EventBus(guard),
            studio_snapshot=self.snapshot,
            control_responder=self.control,
        )

    def snapshot(self) -> StateMessage:
        with self._lock:
            return StateMessage(phase=self.phase, state=self._run_state())

    def transition(self, phase: StudioPhase) -> None:
        with self._lock:
            next_phase: dict[StudioPhase, tuple[StudioPhase, ...]] = {
                "idle": ("project_open",),
                "project_open": ("planning",),
                "planning": ("world_ready", "project_open"),
                "world_ready": ("running", "project_open"),
                "running": ("finished",),
                "finished": ("project_open",),
            }
            if phase not in next_phase[self.phase]:
                raise StudioError("Studio message does not fit the current phase.")
            self.phase = phase
            if phase == "project_open":
                self.server.run_id = ""
                self._run_state = RunState
            self.server.publish_studio_state()

    def attach_active_run(
        self,
        *,
        worker: Thread,
        abort: Callable[[], None],
        run_id: str,
        state: Callable[[], RunState],
    ) -> None:
        """The worker owns the engine: cancellation must use its normal abort path.

        T25 supplies the worker and its responder cancellation. Shutdown never
        writes a synthetic event or races the engine's ledger sequence allocator.
        """
        with self._lock:
            if self.phase != "world_ready" or self._worker is not None:
                raise StudioError("Studio cannot attach another active run.")
            self._worker, self._abort, self._run_state = worker, abort, state
            self.server.run_id = run_id
            self.transition("running")

    def control(self, message: Shutdown) -> bool:
        with self._lock:
            worker, abort = self._worker, self._abort
        if worker is not None and worker.is_alive():
            assert abort is not None
            abort()
            worker.join()
            if self._run_state().status not in ("aborted", "finished"):
                raise StudioError("The active run did not finish as aborted.")
        with self._lock:
            self._worker = None
            self._abort = None
            if self.phase == "running":
                self.transition("finished")
            self.stopped.set()
            self.server.stop()
        return True

    def serve(self, *, on_started: Callable[[], None]) -> None:
        self.server.serve(on_started=on_started)
