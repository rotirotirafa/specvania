"""Immutable event catalog and an instance-owned secret rejection guard.

Events carry summaries only. Sequence allocation belongs to the ledger; seq=0
can represent an event awaiting append. Consumers receive assigned sequences.
"""

import json
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from specvania.sdd.world import World

type ShortText = Annotated[str, Field(max_length=200)]
type Thought = Annotated[str, Field(max_length=500)]
type NonNegativeInt = Annotated[int, Field(ge=0)]
type PositiveInt = Annotated[int, Field(ge=1)]
type NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Role(StrEnum):
    MAGE = "mage"
    WARRIOR = "warrior"
    PALADIN = "paladin"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class EventBase(FrozenModel):
    ts: AwareDatetime
    run_id: ShortText
    seq: NonNegativeInt


class ModelPrice(FrozenModel):
    """Optional reference rates for the HUD; configured prices, never defaults."""

    model: ShortText
    input_per_mtok: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    output_per_mtok: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    currency: ShortText


class RunStarted(EventBase):
    type: Literal["run_started"] = "run_started"
    feature: ShortText
    branch: ShortText
    lives: PositiveInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    prices: tuple[ModelPrice, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class RoomInfo(FrozenModel):
    id: ShortText
    area: ShortText
    title: ShortText
    depends_on: tuple[ShortText, ...] = ()
    area_title: ShortText = Field(default="", exclude_if=lambda value: not value)
    boss_ids: tuple[ShortText, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class WorldRoomInfo(RoomInfo):
    """Initial map state without altering T03's RoomProgress state enum."""

    state: Literal["locked", "open", "active", "cleared", "failed", "skipped"] = (
        "locked"
    )


class AreaInfo(FrozenModel):
    id: ShortText
    title: ShortText
    boss_ids: tuple[ShortText, ...] = ()


class DoorInfo(FrozenModel):
    src: ShortText
    dst: ShortText


class WorldPayload(FrozenModel):
    areas: tuple[AreaInfo, ...] = ()
    rooms: tuple[WorldRoomInfo, ...]
    doors: tuple[DoorInfo, ...] = ()
    boss_ids: tuple[ShortText, ...] = ()


def build_world_payload(world: World) -> WorldPayload:
    """Pure initial topology for WorldLoaded; execution events own progress.

    Boss ids are scenario ids listed on each area's boss line. Checklist ticks
    describe the spec, not commits in this run, so they do not clear rooms.
    Area metadata on rooms also survives the T03 mid-run state projection.
    """
    areas = {area.id: area for area in world.areas}
    return WorldPayload(
        areas=tuple(
            AreaInfo(id=area.id, title=area.title, boss_ids=area.boss)
            for area in world.areas
        ),
        rooms=tuple(
            WorldRoomInfo(
                id=room.id,
                area=room.area_id,
                title=room.title,
                depends_on=room.depends_on,
                area_title=areas[room.area_id].title,
                boss_ids=areas[room.area_id].boss,
                state="locked" if room.depends_on else "open",
            )
            for room in world.rooms
        ),
        doors=tuple(DoorInfo(src=door.src, dst=door.dst) for door in world.doors),
        boss_ids=tuple(boss for area in world.areas for boss in area.boss),
    )


class WorldLoaded(EventBase):
    type: Literal["world_loaded"] = "world_loaded"
    # Accept legacy RoomInfo instances and JSON alongside the richer payload.
    rooms: tuple[RoomInfo | WorldRoomInfo, ...]
    areas: tuple[AreaInfo, ...] = Field(default=(), exclude_if=lambda value: not value)
    doors: tuple[DoorInfo, ...] = Field(default=(), exclude_if=lambda value: not value)
    boss_ids: tuple[ShortText, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class ClarifyQuestion(EventBase):
    type: Literal["clarify_question"] = "clarify_question"
    question_id: ShortText
    question: ShortText
    options: tuple[ShortText, ...] = ()
    default: ShortText


class ClarifyAnswer(EventBase):
    type: Literal["clarify_answer"] = "clarify_answer"
    question_id: ShortText
    answer: ShortText


class SpecWritten(EventBase):
    type: Literal["spec_written"] = "spec_written"
    feature: ShortText
    spec_version: ShortText
    from_bug_id: ShortText | None = None


class RoomEvent(EventBase):
    room_id: ShortText


class RoomEntered(RoomEvent):
    type: Literal["room_entered"] = "room_entered"


class AttemptStarted(RoomEvent):
    type: Literal["attempt_started"] = "attempt_started"
    attempt: PositiveInt


class ModelCall(EventBase):
    type: Literal["model_call"] = "model_call"
    role: Role
    model: ShortText
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    latency: NonNegativeFloat
    status: Annotated[int, Field(ge=100, le=599)]


class Cooldown(EventBase):
    type: Literal["cooldown"] = "cooldown"
    role: Role
    model: ShortText
    seconds: NonNegativeFloat
    reason: ShortText


class Fallback(EventBase):
    type: Literal["fallback"] = "fallback"
    role: Role
    from_model: ShortText
    to_model: ShortText
    reason: ShortText


class Action(RoomEvent):
    type: Literal["action"] = "action"
    thought: Thought
    action: ShortText
    args_summary: ShortText


class ToolRefused(RoomEvent):
    type: Literal["tool_refused"] = "tool_refused"
    tool: ShortText
    reason: ShortText


class ApprovalRequested(EventBase):
    type: Literal["approval_requested"] = "approval_requested"
    request_id: ShortText
    command: ShortText
    cwd: ShortText


class ApprovalAnswered(EventBase):
    type: Literal["approval_answered"] = "approval_answered"
    request_id: ShortText
    answer: bool
    responder: ShortText


class GateResult(RoomEvent):
    type: Literal["gate_result"] = "gate_result"
    command: ShortText
    exit_code: int
    output_summary: ShortText


class ChecklistResult(FrozenModel):
    item: ShortText
    ok: bool
    note: ShortText


class Review(RoomEvent):
    type: Literal["review"] = "review"
    verdict: Literal["pass", "fail"]
    checklist: tuple[ChecklistResult, ...]
    feedback: ShortText


class Respawn(RoomEvent):
    type: Literal["respawn"] = "respawn"
    attempt: PositiveInt
    feedback: ShortText


class RoomCleared(RoomEvent):
    type: Literal["room_cleared"] = "room_cleared"
    commit: ShortText


class RoomFailed(RoomEvent):
    type: Literal["room_failed"] = "room_failed"
    reason: ShortText


class RoomSkipped(RoomEvent):
    type: Literal["room_skipped"] = "room_skipped"
    reason: ShortText


class BossStarted(EventBase):
    type: Literal["boss_started"] = "boss_started"
    area_id: ShortText
    scenarios: tuple[ShortText, ...]


class BossScenario(EventBase):
    type: Literal["boss_scenario"] = "boss_scenario"
    area_id: ShortText
    scenario: ShortText
    passed: bool
    reason: ShortText


class BossResult(EventBase):
    type: Literal["boss_result"] = "boss_result"
    area_id: ShortText
    passed: bool


class BugOpened(EventBase):
    type: Literal["bug_opened"] = "bug_opened"
    bug_id: ShortText
    title: ShortText
    body: ShortText
    feature: ShortText | None = None
    room_id: ShortText | None = None
    source: Literal["user", "room_skipped", "boss_scenario"]


class BugClosed(EventBase):
    type: Literal["bug_closed"] = "bug_closed"
    bug_id: ShortText
    status: Literal["fixed", "wontfix"]
    fixed_by_run: ShortText | None = None


class RunPaused(EventBase):
    type: Literal["run_paused"] = "run_paused"
    reason: ShortText
    room_id: ShortText | None = None


class RunFinished(EventBase):
    type: Literal["run_finished"] = "run_finished"
    status: Literal["finished", "aborted"]


type Event = Annotated[
    RunStarted
    | WorldLoaded
    | ClarifyQuestion
    | ClarifyAnswer
    | SpecWritten
    | RoomEntered
    | AttemptStarted
    | ModelCall
    | Cooldown
    | Fallback
    | Action
    | ToolRefused
    | ApprovalRequested
    | ApprovalAnswered
    | GateResult
    | Review
    | Respawn
    | RoomCleared
    | RoomFailed
    | RoomSkipped
    | BossStarted
    | BossScenario
    | BossResult
    | BugOpened
    | BugClosed
    | RunPaused
    | RunFinished,
    Field(discriminator="type"),
]

# Schema adapter only; all per-run and registered-secret state is instance-owned.
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(
    Event, config=ConfigDict(hide_input_in_errors=True)
)


class SecretLeakError(ValueError):
    """A registered secret was found; the exception never contains its value."""


class RedactionGuard:
    """Reject, rather than rewrite, events containing registered secrets."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = RLock()

    def register(self, secret: str) -> None:
        if not secret:
            raise ValueError("Registered secrets must not be empty")
        with self._lock:
            self._secrets.add(secret)
            # Match JSON-escaped secrets as well as values appearing literally.
            for ensure_ascii in (False, True):
                self._secrets.add(json.dumps(secret, ensure_ascii=ensure_ascii)[1:-1])

    def validate(self, event: Event) -> None:
        serialized = event.model_dump_json()
        with self._lock:
            if any(secret in serialized for secret in self._secrets):
                raise SecretLeakError("Event rejected: contains a registered secret")
