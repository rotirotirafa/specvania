"""Synchronous room orchestration over the existing Warrior and Paladin APIs."""

import secrets
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal, TypedDict

from specvania.agent.approval import ApprovalBroker, ApprovalResponder
from specvania.agent.executor import Executor
from specvania.agent.gates import GateRunner
from specvania.agent.reviewer import Reviewer, ReviewError
from specvania.agent.sandbox import Sandbox
from specvania.agent.tools import Tools
from specvania.clock import Clock
from specvania.config import Config
from specvania.ledger.repo import Ledger
from specvania.provider.client import Provider, ProviderError
from specvania.provider.router import Router
from specvania.run.bus import EventBus
from specvania.run.events import (
    AttemptStarted,
    BossStarted,
    Event,
    Respawn,
    RoomCleared,
    RoomEntered,
    RoomFailed,
    RoomSkipped,
    RunFinished,
    RunPaused,
    RunStarted,
    WorldLoaded,
    build_world_payload,
)
from specvania.run.gitops import GitOps
from specvania.run.state import RunState, fold_events
from specvania.sdd.lint import validate_directory
from specvania.sdd.world import Area, Room, World

type PauseDecision = Literal["retry", "skip", "abort"]


class EventFields(TypedDict):
    ts: datetime
    run_id: str
    seq: int


class EngineError(Exception):
    """A run cannot proceed; the player can fix its inputs."""


def rebuild_run_state(ledger: Ledger, run_id: str) -> RunState:
    """Recovery is a fresh projection of durable events, with no model calls."""
    return fold_events(ledger.list_events(run_id))


def _feedback_summary(feedback: str) -> str:
    if len(feedback) <= 200:
        return feedback
    marker = "\n[truncated]\n"
    return feedback[:80] + marker + feedback[-(200 - 80 - len(marker)) :]


class Engine:
    def __init__(
        self,
        *,
        root: Path,
        config: Config,
        provider: Provider,
        ledger: Ledger,
        bus: EventBus,
        clock: Clock,
        responders: Sequence[ApprovalResponder] = (),
        decide: Callable[[RunPaused], PauseDecision | None] = lambda pause: None,
        shell_timeout: float = 60,
        gate_timeout: float = 300,
    ) -> None:
        self.git = GitOps(root)
        self.config = config
        self.provider = provider
        self.ledger = ledger
        self.bus = bus
        self.clock = clock
        self.responders = tuple(responders)
        self.decide = decide
        self.shell_timeout = shell_timeout
        self.gate_timeout = gate_timeout
        self.run_id = ""
        self._emit_lock = RLock()

    def emit(self, event: Event) -> None:
        """Allocate and guard in the ledger before publishing, as one ordered pair."""
        with self._emit_lock:
            assigned = self.ledger.append_event(event)
            self.bus.publish(assigned)

    def _fields(self) -> EventFields:
        return {"ts": self.clock.now(), "run_id": self.run_id, "seq": 0}

    def _pause(self, reason: str, room_id: str | None = None) -> PauseDecision | None:
        pause = RunPaused(**self._fields(), reason=reason[:200], room_id=room_id)
        self.emit(pause)
        choice = self.decide(pause)
        if choice not in (None, "retry", "skip", "abort"):
            raise EngineError("Choose retry, skip or abort for a paused run.")
        if choice == "abort":
            # Abort leaves uncommitted work in place for the player to inspect.
            self._finish("aborted")
        return choice

    def _finish(self, status: Literal["finished", "aborted"]) -> None:
        self.emit(RunFinished(**self._fields(), status=status))

    def _reset_router(self) -> None:
        # Explicit player retry restores an exhausted chain, preserving stamina.
        self.router = Router(
            config=self.config,
            provider=self.provider,
            clock=self.clock,
            run_id=self.run_id,
            emit=self.emit,
            limiter=self.router.limiter,
        )

    def _reviewer(self) -> Reviewer:
        return Reviewer(
            router=self.router,
            sandbox=self.sandbox,
            clock=self.clock,
            run_id=self.run_id,
            emit=self.emit,
        )

    def _skip(self, room: Room, reason: str) -> None:
        # Restore the tree before another room can review or commit these edits.
        stash = self.git.stash_room(self.run_id, room.id)
        if stash is not None:
            recovery = f"; saved to stash {stash}"
            reason = reason[: 200 - len(recovery)] + recovery
        self.emit(RoomSkipped(**self._fields(), room_id=room.id, reason=reason[:200]))

    def _room(self, room: Room, spec_summary: str) -> str | None:
        """Return cleared/skipped, or None when the run stops at a pause/abort."""
        self.router.start_room(room.id)
        base = self.git.head()
        lives = self.config.limits.lives
        attempt = 0
        feedback = ""
        should_respawn = False
        while True:
            attempt += 1
            self.emit(RoomEntered(**self._fields(), room_id=room.id))
            if should_respawn:
                self.emit(
                    Respawn(
                        **self._fields(),
                        room_id=room.id,
                        attempt=attempt,
                        feedback=_feedback_summary(feedback),
                    )
                )
                should_respawn = False
            self.emit(
                AttemptStarted(**self._fields(), room_id=room.id, attempt=attempt)
            )
            tools = Tools(
                self.sandbox,
                broker=self.broker,
                clock=self.clock,
                emit=self.emit,
                run_id=self.run_id,
                room_id=room.id,
                observation_kb=self.config.limits.observation_kb,
                shell_timeout=self.shell_timeout,
            )
            result = Executor(
                router=self.router,
                tools=tools,
                sandbox=self.sandbox,
                limits=self.config.limits,
                clock=self.clock,
                run_id=self.run_id,
                emit=self.emit,
            ).execute(room, spec_summary=spec_summary, feedback=feedback)
            if result.stop_reason == "router_error":
                choice = self._pause(result.reason, room.id)
                if choice == "retry":
                    self._reset_router()
                    continue  # Provider outages never debit a life.
                if choice == "skip":
                    self._skip(room, result.reason)
                    return "skipped"
                return None
            gates = self.gates.run(room.id)
            while True:
                try:
                    review = self._reviewer().review_room(
                        room, attempt_diff=self.git.diff(base), gates=gates
                    )
                    feedback = review.feedback
                    passed = review.verdict == "pass" and result.status == "finished"
                    if result.status != "finished":
                        feedback = result.reason + "\n" + feedback
                    break
                except ProviderError as error:
                    choice = self._pause(str(error), room.id)
                    if choice == "retry":
                        self._reset_router()
                        self.router.start_room(room.id)
                        continue
                    if choice == "skip":
                        self._skip(room, str(error))
                        return "skipped"
                    return None
                except ReviewError as error:
                    feedback = str(error)
                    passed = False
                    break
            if passed:
                commit = self.git.commit_room(room.id, room.title)
                self.emit(RoomCleared(**self._fields(), room_id=room.id, commit=commit))
                return "cleared"
            lives -= 1
            should_respawn = True
            self.emit(
                RoomFailed(**self._fields(), room_id=room.id, reason=feedback[:200])
            )
            if lives:
                continue
            choice = self._pause(f"Out of lives in {room.id}: {feedback}", room.id)
            if choice == "retry":
                lives = self.config.limits.lives
            elif choice == "skip":
                self._skip(room, feedback)
                return "skipped"
            else:
                return None

    def _boss(self, area: Area, world: World, room_id: str) -> bool:
        room_ids = {room.id for room in world.rooms if room.area_id == area.id}
        commits = tuple(
            event.commit
            for event in self.ledger.list_events(self.run_id)
            if event.type == "room_cleared" and event.room_id in room_ids
        )
        diff = self.git.area_diff(commits)
        while True:
            self.emit(
                BossStarted(**self._fields(), area_id=area.id, scenarios=area.boss)
            )
            gates = self.gates.run(room_id)
            try:
                results = self._reviewer().evaluate_boss(
                    area, scenarios=world.scenarios, area_diff=diff, gates=gates
                )
                failures = [r for r in results if not r.passed]
                if not failures:
                    return True
                reason = "; ".join(f"{r.scenario}: {r.reason}" for r in failures)
            except (ProviderError, ReviewError) as error:
                reason = str(error)
            choice = self._pause(f"Boss {area.id}: {reason}")
            if choice == "retry":
                self._reset_router()
            elif choice == "skip":
                return True  # Failed boss events remain; room doors are unaffected.
            else:
                return False

    def run(
        self,
        directory: Path,
        *,
        allow_dirty: bool = False,
        on_started: Callable[[str], Sequence[ApprovalResponder] | None] | None = None,
    ) -> RunState:
        world = validate_directory(directory)
        self.git.check(allow_dirty=allow_dirty)
        try:
            spec_md = (directory / "spec.md").read_text(encoding="utf-8")
            tasks_md = (directory / "tasks.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise EngineError("Cannot snapshot the world; check its files.") from None
        project = self.ledger.add_project(self.git.root, self.git.origin_url())
        feature = self.ledger.add_feature(
            project.id, directory.resolve().name, directory.resolve().name
        )
        version = self.ledger.add_spec_version(feature.id, spec_md, tasks_md)
        self.run_id = f"{self.clock.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        branch = f"specvania/{self.run_id}"
        self.ledger.create_run(feature.id, version.id, branch, run_id=self.run_id)
        self.git.create_branch(self.run_id)
        self.emit(RunStarted(**self._fields(), feature=feature.slug, branch=branch))
        payload = build_world_payload(world)
        self.emit(WorldLoaded(**self._fields(), **payload.model_dump()))
        self.sandbox = Sandbox(self.git.root)
        self.router = Router(
            config=self.config,
            provider=self.provider,
            clock=self.clock,
            run_id=self.run_id,
            emit=self.emit,
        )
        self.gates = GateRunner(
            config=self.config,
            root=self.git.root,
            clock=self.clock,
            emit=self.emit,
            run_id=self.run_id,
            timeout=self.gate_timeout,
        )
        # Live callers return UI responders before any Warrior action can start.
        extra_responders = on_started(self.run_id) if on_started is not None else None
        self.broker = ApprovalBroker(
            self.responders + tuple(extra_responders or ()),
            clock=self.clock,
            emit=self.emit,
            run_id=self.run_id,
        )
        remaining = {room.id: room for room in world.rooms}
        cleared: set[str] = set()
        fought: set[str] = set()
        while remaining:
            ready = [r for r in remaining.values() if set(r.depends_on) <= cleared]
            if not ready:
                break  # Skipped sources keep their dependent rooms locked.
            room = min(ready, key=lambda r: r.id)
            outcome = self._room(
                room, spec_md[: self.config.limits.observation_kb * 1024]
            )
            if outcome is None:
                return rebuild_run_state(self.ledger, self.run_id)
            del remaining[room.id]
            if outcome == "cleared":
                cleared.add(room.id)
            for area in world.areas:
                if area.id in fought or not area.boss:
                    continue
                area_rooms = {r.id for r in world.rooms if r.area_id == area.id}
                if area_rooms and area_rooms <= cleared:
                    fought.add(area.id)
                    if not self._boss(area, world, room.id):
                        return rebuild_run_state(self.ledger, self.run_id)
        self._finish("finished")
        return rebuild_run_state(self.ledger, self.run_id)
