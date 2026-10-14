"""Synchronous room orchestration over the existing Warrior and Paladin APIs."""

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
from specvania.bugs import Bugs
from specvania.clock import Clock
from specvania.config import Config
from specvania.ledger.repo import Ledger, RunIdTakenError, generate_run_id
from specvania.pricing import load_pricing
from specvania.provider.client import Provider, ProviderError
from specvania.provider.router import Router
from specvania.run.bus import EventBus
from specvania.run.events import (
    AttemptStarted,
    BossStarted,
    BugClosed,
    Event,
    ModelPrice,
    Respawn,
    RoomCleared,
    RoomEntered,
    RoomFailed,
    RoomSkipped,
    RunFinished,
    RunPaused,
    RunStarted,
    StaminaRestored,
    WorldLoaded,
    build_world_payload,
)
from specvania.run.gitops import GitOps
from specvania.run.state import RunState, fold_events
from specvania.run.summary import write_summary
from specvania.sdd.lint import validate_directory
from specvania.sdd.world import Area, Room, World

type PauseDecision = Literal["retry", "skip", "abort"]


class EventFields(TypedDict):
    ts: datetime
    run_id: str
    seq: int


class EngineError(Exception):
    """A run cannot proceed; the player can fix its inputs."""


class RunCancelled(Exception):
    """The player stopped the run; finish as aborted without a pause."""


class _StaminaStop(Exception):
    """Unwind the pending call without treating a player pause as a failure."""


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
        before_request: Callable[[], None] | None = None,
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
        self._before_request = before_request

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
        write_summary(self.git.root, self.ledger, self.run_id, config=self.config)
        choice = self.decide(pause)
        while reason == "stamina exhausted" and choice == "skip":
            choice = self.decide(pause)
        if choice not in (None, "retry", "skip", "abort"):
            raise EngineError("Choose retry, skip or abort for a paused run.")
        if choice == "abort":
            # Abort leaves uncommitted work in place for the player to inspect.
            self._finish("aborted")
        return choice

    def _check_budget(self) -> None:
        state = rebuild_run_state(self.ledger, self.run_id)
        while (
            state.token_budget is not None and state.tokens_spent >= state.token_budget
        ):
            choice = self._pause("stamina exhausted", state.active_room)
            if choice != "retry":
                raise _StaminaStop()
            # Budgets are whole tokens; round a fractional quarter upward.
            budget = state.token_budget + (self._original_budget + 3) // 4
            self.emit(StaminaRestored(**self._fields(), token_budget=budget))
            state = rebuild_run_state(self.ledger, self.run_id)

    def _finish(self, status: Literal["finished", "aborted"]) -> None:
        self.emit(RunFinished(**self._fields(), status=status))
        if status == "finished":
            events = self.ledger.list_events(self.run_id)
            cleared = {e.room_id for e in events if e.type == "room_cleared"}
            if cleared == {room.id for room in self.world.rooms} and not any(
                e.type == "room_skipped" for e in events
            ):
                self.bugs.close_from_plans(
                    run_id=self.run_id, clock=self.clock, emit=self.emit
                )
        write_summary(self.git.root, self.ledger, self.run_id, config=self.config)

    def _check_request(self) -> None:
        """Refuse budget/cancellation stops before recording a provider request."""
        self._check_budget()
        if self._before_request is not None:
            self._before_request()

    def _reset_router(self) -> None:
        # Explicit player retry restores an exhausted chain, preserving stamina.
        self.router = Router(
            config=self.config,
            provider=self.provider,
            clock=self.clock,
            run_id=self.run_id,
            emit=self.emit,
            before_request=self._check_request,
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

    def _skip(self, room: Room, reason: str, *, feedback: str | None = None) -> None:
        body = reason if feedback is None else feedback
        # Restore the tree before another room can review or commit these edits.
        stash = self.git.stash_room(self.run_id, room.id)
        if stash is not None:
            recovery = f"; saved to stash {stash}"
            reason = reason[: 200 - len(recovery)] + recovery
        self.emit(RoomSkipped(**self._fields(), room_id=room.id, reason=reason[:200]))
        self._open_bug(
            f"Skipped {room.id}: {room.title}",
            body,
            source="room_skipped",
            room_id=room.id,
        )

    def _open_bug(
        self,
        title: str,
        body: str,
        *,
        source: Literal["room_skipped", "boss_scenario"],
        room_id: str | None = None,
    ) -> str:
        bug = self.ledger.add_bug(
            self.bugs.project_id,
            title,
            body=body,
            source=source,
            feature_id=self.feature_id,
            run_id=self.run_id,
            room_id=room_id,
        )
        self.emit(self.bugs.opened_event(bug, run_id=self.run_id, clock=self.clock))
        return bug.id

    def _room(self, room: Room, spec_summary: str) -> str | None:
        """Return cleared/skipped, or None when the run stops at a pause/abort."""
        self.router.start_room(room.id)
        base = self.git.head()
        lives = self.config.limits.lives
        attempt = 0
        feedback = ""
        last_feedback: str | None = None
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
                gates=self.config.gates,
            ).execute(room, spec_summary=spec_summary, feedback=feedback)
            if result.stop_reason == "router_error":
                choice = self._pause(result.reason, room.id)
                if choice == "retry":
                    self._reset_router()
                    continue  # Provider outages never debit a life.
                if choice == "skip":
                    self._skip(room, result.reason, feedback=last_feedback)
                    return "skipped"
                return None
            gates = self.gates.run(room.id)
            while True:
                try:
                    review = self._reviewer().review_room(
                        room, attempt_diff=self.git.diff(base), gates=gates
                    )
                    feedback = review.feedback
                    last_feedback = review.feedback
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
                        self._skip(room, str(error), feedback=last_feedback)
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
                self._skip(room, feedback, feedback=last_feedback)
                return "skipped"
            else:
                return None

    def _boss(self, area: Area, world: World, room_id: str) -> bool:
        self.router.end_room()
        room_ids = {room.id for room in world.rooms if room.area_id == area.id}
        commits = tuple(
            event.commit
            for event in self.ledger.list_events(self.run_id)
            if event.type == "room_cleared" and event.room_id in room_ids
        )
        diff = self.git.area_diff(commits)
        # One fight per area/run; keep ids across evaluations, including fixed bugs.
        scenario_bugs: dict[str, str] = {}
        while True:
            self.emit(
                BossStarted(**self._fields(), area_id=area.id, scenarios=area.boss)
            )
            gates = self.gates.run(room_id)
            try:
                results = self._reviewer().evaluate_boss(
                    area, scenarios=world.scenarios, area_diff=diff, gates=gates
                )
                for result in results:
                    bug_id = scenario_bugs.get(result.scenario)
                    if result.passed:
                        if bug_id is not None:
                            bug = self.ledger.get_bug(bug_id)
                            if bug.status == "open":
                                self.ledger.close_bug(
                                    bug_id, status="fixed", fixed_by_run=self.run_id
                                )
                                self.emit(
                                    BugClosed(
                                        **self._fields(),
                                        bug_id=bug_id,
                                        status="fixed",
                                        fixed_by_run=self.run_id,
                                    )
                                )
                    elif bug_id is None:
                        scenario_bugs[result.scenario] = self._open_bug(
                            f"Boss {area.id}: {result.scenario}",
                            result.reason,
                            source="boss_scenario",
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
        self.world = world
        self._original_budget = self.config.limits.token_budget
        self.git.check(allow_dirty=allow_dirty)
        pricing = load_pricing(self.git.root / ".specvania/pricing.toml")
        try:
            spec_md = (directory / "spec.md").read_text(encoding="utf-8")
            tasks_md = (directory / "tasks.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise EngineError("Cannot snapshot the world; check its files.") from None
        project = self.ledger.add_project(self.git.root, self.git.origin_url())
        feature = self.ledger.add_feature(
            project.id, directory.resolve().name, directory.resolve().name
        )
        self.feature_id = feature.id
        self.bugs = Bugs(self.ledger, project.id)
        version = self.ledger.add_spec_version(feature.id, spec_md, tasks_md)
        for attempt in range(5):
            self.run_id = generate_run_id(self.clock)
            try:
                self.ledger.create_run(
                    feature.id,
                    version.id,
                    f"specvania/{self.run_id}",
                    run_id=self.run_id,
                )
                break
            except RunIdTakenError:
                if attempt == 4:
                    raise EngineError(
                        "Cannot allocate a unique run id; try again."
                    ) from None
        branch = f"specvania/{self.run_id}"
        self.git.create_branch(self.run_id)
        self.emit(
            RunStarted(
                **self._fields(),
                feature=feature.slug,
                branch=branch,
                lives=self.config.limits.lives,
                token_budget=self.config.limits.token_budget,
                prices=tuple(
                    ModelPrice(
                        model=price.model,
                        input_per_mtok=price.input_per_mtok,
                        output_per_mtok=price.output_per_mtok,
                        currency=price.currency,
                        source_url=str(price.source_url),
                        checked_on=price.checked_on,
                    )
                    for price in pricing.prices
                ),
            )
        )
        payload = build_world_payload(world)
        self.emit(WorldLoaded(**self._fields(), **payload.model_dump()))
        for bug in self.bugs.list():
            if bug.feature_id == feature.id:
                self.emit(
                    self.bugs.opened_event(bug, run_id=self.run_id, clock=self.clock)
                )
        self.sandbox = Sandbox(self.git.root)
        self.router = Router(
            config=self.config,
            provider=self.provider,
            clock=self.clock,
            run_id=self.run_id,
            emit=self.emit,
            before_request=self._check_request,
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
        try:
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
                        passed = self._boss(area, world, room.id)
                        if not passed:
                            return rebuild_run_state(self.ledger, self.run_id)
        except RunCancelled:
            self._finish("aborted")
        except _StaminaStop:
            pass
        else:
            self._finish("finished")
        return rebuild_run_state(self.ledger, self.run_id)
