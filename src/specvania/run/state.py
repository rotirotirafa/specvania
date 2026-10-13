"""Deterministic run projections; no clock, I/O or shared mutable state."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from specvania.run.events import (
    Action,
    ApprovalAnswered,
    ApprovalRequested,
    BossScenario,
    Event,
    FrozenModel,
    ModelPrice,
    PositiveInt,
    Review,
    Role,
    RoomInfo,
)


class RoomState(StrEnum):
    LOCKED = "locked"
    OPEN = "open"
    ACTIVE = "active"
    CLEARED = "cleared"
    FAILED = "failed"
    SKIPPED = "skipped"


class RoomProgress(RoomInfo):
    state: RoomState = RoomState.LOCKED
    attempt: int = 0
    commit: str | None = None
    feedback: str | None = None
    failed_attempts: int = 0


class ModelMetrics(FrozenModel):
    model: str
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency: float = 0
    rate_limits: int = 0
    fallbacks: int = 0


type PartyStatus = Literal[
    "idle", "thinking", "acting", "cooldown", "waiting for player"
]


class PartyMember(FrozenModel):
    role: Role
    model: str
    status: PartyStatus = "idle"
    cooldown_seconds: float = 0
    cooldown_duration: float = 0


class BossProgress(FrozenModel):
    area_id: str
    scenarios: tuple[str, ...] = ()
    results: tuple[BossScenario, ...] = ()
    passed: bool | None = None


class BugProgress(FrozenModel):
    bug_id: str
    title: str = ""
    body: str = ""
    feature: str | None = None
    room_id: str | None = None
    status: Literal["open", "fixed", "wontfix"] = "open"
    fixed_by_run: str | None = None


class RunState(FrozenModel):
    run_id: str | None = None
    seq: int = 0
    status: Literal["pending", "running", "paused", "finished", "aborted"] = "pending"
    feature: str | None = None
    branch: str | None = None
    reason: str | None = None
    active_room: str | None = None
    rooms: tuple[RoomProgress, ...] = ()
    models: tuple[ModelMetrics, ...] = ()
    party: tuple[PartyMember, ...] = ()
    bosses: tuple[BossProgress, ...] = ()
    bugs: tuple[BugProgress, ...] = ()
    lives: PositiveInt | None = None
    prices: tuple[ModelPrice, ...] = ()
    dialogue: Action | Review | ApprovalRequested | ApprovalAnswered | None = None
    party_room: str | None = None


class StateFoldError(ValueError):
    """One projection accepts one run in strictly increasing sequence order."""


def _party_statuses(event: Event) -> dict[Role, PartyStatus]:
    """Snapshot counterpart of hud/party.js's documented event mapping."""
    match event.type:
        case "room_entered":
            return {Role.WARRIOR: "thinking", Role.MAGE: "idle", Role.PALADIN: "idle"}
        case "attempt_started" | "respawn" | "tool_refused":
            return {Role.WARRIOR: "thinking"}
        case "model_call" | "fallback":
            return {event.role: "thinking"}
        case "cooldown":
            return {event.role: "cooldown"}
        case "action":
            return {Role.WARRIOR: "idle" if event.action == "finish" else "acting"}
        case "gate_result":
            return {Role.WARRIOR: "idle", Role.PALADIN: "thinking"}
        case "review":
            return {Role.WARRIOR: "idle", Role.PALADIN: "idle"}
        case "room_cleared" | "room_failed" | "room_skipped":
            return {Role.WARRIOR: "idle"}
        case "approval_requested":
            return {Role.WARRIOR: "waiting for player"}
        case "approval_answered":
            return {Role.WARRIOR: "acting"}
        case "clarify_question":
            return {Role.MAGE: "waiting for player"}
        case "clarify_answer":
            return {Role.MAGE: "thinking"}
        case "spec_written":
            return {Role.MAGE: "idle"}
        case "boss_started":
            return {Role.PALADIN: "thinking", Role.MAGE: "idle", Role.WARRIOR: "idle"}
        case "boss_scenario":
            return {Role.PALADIN: "acting"}
        case "boss_result":
            return {Role.PALADIN: "idle"}
        case "run_paused" | "run_finished":
            return {
                role: "waiting for player" if event.type == "run_paused" else "idle"
                for role in Role
            }
        case _:
            return {}


def fold_events(events: Iterable[Event]) -> RunState:
    """Rebuild a run from ordered events without mutating any input.

    A snapshot may start mid-run, so unknown rooms/bosses/bugs receive minimal
    metadata. Doors open only when every source room is cleared, never skipped.
    """
    state = RunState()
    rooms: dict[str, RoomProgress] = {}
    models: dict[str, ModelMetrics] = {}
    party: dict[Role, PartyMember] = {}
    bosses: dict[str, BossProgress] = {}
    bugs: dict[str, BugProgress] = {}
    cooldown_ends: dict[Role, datetime] = {}
    for event in events:
        if state.run_id is not None and event.run_id != state.run_id:
            raise StateFoldError("Cannot fold events from different runs")
        if event.seq <= state.seq:
            raise StateFoldError("Fold events must have increasing assigned seq")
        state = state.model_copy(update={"run_id": event.run_id, "seq": event.seq})
        if isinstance(event, Action | Review | ApprovalRequested | ApprovalAnswered):
            state = state.model_copy(update={"dialogue": event})
        match event.type:
            case "run_started":
                state = state.model_copy(
                    update={
                        "status": "running",
                        "feature": event.feature,
                        "branch": event.branch,
                        "reason": None,
                        "lives": event.lives,
                        "prices": event.prices,
                    }
                )
            case "world_loaded":
                rooms = {
                    room.id: RoomProgress(**room.model_dump()) for room in event.rooms
                }
            case "spec_written":
                state = state.model_copy(update={"feature": event.feature})
            case (
                "room_entered"
                | "attempt_started"
                | "respawn"
                | "room_cleared"
                | "room_failed"
                | "room_skipped"
                | "review"
            ):
                room = rooms.get(
                    event.room_id, RoomProgress(id=event.room_id, area="", title="")
                )
                match event.type:
                    case "room_entered":
                        room = room.model_copy(
                            update={
                                "state": RoomState.ACTIVE,
                                "failed_attempts": 0
                                if state.party_room != event.room_id
                                else room.failed_attempts,
                            }
                        )
                        state = state.model_copy(
                            update={
                                "active_room": event.room_id,
                                "status": "running",
                                "reason": None,
                                "party_room": event.room_id,
                            }
                        )
                    case "attempt_started":
                        room = room.model_copy(
                            update={
                                "state": RoomState.ACTIVE,
                                "attempt": event.attempt,
                                "failed_attempts": 0
                                if event.attempt == 1
                                else max(room.failed_attempts, event.attempt - 1),
                            }
                        )
                    case "respawn":
                        room = room.model_copy(
                            update={
                                "state": RoomState.ACTIVE,
                                "attempt": event.attempt,
                                "feedback": event.feedback,
                            }
                        )
                    case "room_cleared":
                        room = room.model_copy(
                            update={"state": RoomState.CLEARED, "commit": event.commit}
                        )
                    case "room_failed":
                        room = room.model_copy(
                            update={
                                "state": RoomState.FAILED,
                                "feedback": event.reason,
                                "failed_attempts": max(
                                    room.failed_attempts, room.attempt or 1
                                ),
                            }
                        )
                    case "room_skipped":
                        room = room.model_copy(
                            update={
                                "state": RoomState.SKIPPED,
                                "feedback": event.reason,
                            }
                        )
                    case "review":
                        room = room.model_copy(
                            update={
                                "feedback": event.feedback,
                                "failed_attempts": max(
                                    room.failed_attempts, room.attempt or 1
                                )
                                if event.verdict == "fail"
                                else room.failed_attempts,
                            }
                        )
                rooms[event.room_id] = room
                if event.type in ("room_cleared", "room_failed", "room_skipped"):
                    state = state.model_copy(update={"active_room": None})
            case "model_call":
                metrics = models.get(event.model, ModelMetrics(model=event.model))
                models[event.model] = metrics.model_copy(
                    update={
                        "calls": metrics.calls + 1,
                        "tokens_in": metrics.tokens_in + event.tokens_in,
                        "tokens_out": metrics.tokens_out + event.tokens_out,
                        "latency": metrics.latency + event.latency,
                        "rate_limits": metrics.rate_limits + (event.status == 429),
                    }
                )
                party[event.role] = PartyMember(role=event.role, model=event.model)
            case "cooldown":
                cooldown_ends[event.role] = event.ts + timedelta(seconds=event.seconds)
                party[event.role] = PartyMember(
                    role=event.role,
                    model=event.model,
                    status="cooldown",
                    cooldown_seconds=event.seconds,
                    cooldown_duration=event.seconds,
                )
            case "fallback":
                metrics = models.get(
                    event.from_model, ModelMetrics(model=event.from_model)
                )
                models[event.from_model] = metrics.model_copy(
                    update={"fallbacks": metrics.fallbacks + 1}
                )
                party[event.role] = PartyMember(role=event.role, model=event.to_model)
            case "boss_started":
                bosses[event.area_id] = BossProgress(
                    area_id=event.area_id, scenarios=event.scenarios
                )
            case "boss_scenario":
                boss = bosses.get(event.area_id, BossProgress(area_id=event.area_id))
                results = {result.scenario: result for result in boss.results}
                results[event.scenario] = event
                bosses[event.area_id] = boss.model_copy(
                    update={"results": tuple(results.values())}
                )
            case "boss_result":
                boss = bosses.get(event.area_id, BossProgress(area_id=event.area_id))
                bosses[event.area_id] = boss.model_copy(update={"passed": event.passed})
            case "bug_opened":
                bugs[event.bug_id] = BugProgress(
                    bug_id=event.bug_id,
                    title=event.title,
                    body=event.body,
                    feature=event.feature,
                    room_id=event.room_id,
                )
            case "bug_closed":
                bug = bugs.get(event.bug_id, BugProgress(bug_id=event.bug_id))
                bugs[event.bug_id] = bug.model_copy(
                    update={"status": event.status, "fixed_by_run": event.fixed_by_run}
                )
            case "run_paused":
                state = state.model_copy(
                    update={"status": "paused", "reason": event.reason}
                )
            case "run_finished":
                state = state.model_copy(
                    update={"status": event.status, "reason": None, "active_room": None}
                )
        for role, status in _party_statuses(event).items():
            member = party.get(role, PartyMember(role=role, model=""))
            party[role] = member.model_copy(update={"status": status})
            if status != "cooldown":
                cooldown_ends.pop(role, None)
                party[role] = party[role].model_copy(update={"cooldown_seconds": 0})
        for role, end in cooldown_ends.items():
            remaining = max(0, (end - event.ts).total_seconds())
            party[role] = party[role].model_copy(
                update={
                    "cooldown_seconds": remaining,
                    "status": "cooldown" if remaining else "thinking",
                }
            )
        for room_id, room in rooms.items():
            if room.state in (RoomState.LOCKED, RoomState.OPEN):
                is_open = all(
                    source in rooms and rooms[source].state == RoomState.CLEARED
                    for source in room.depends_on
                )
                rooms[room_id] = room.model_copy(
                    update={"state": RoomState.OPEN if is_open else RoomState.LOCKED}
                )
    return state.model_copy(
        update={
            "rooms": tuple(rooms.values()),
            "models": tuple(models.values()),
            "party": tuple(party.values()),
            "bosses": tuple(bosses.values()),
            "bugs": tuple(bugs.values()),
        }
    )
