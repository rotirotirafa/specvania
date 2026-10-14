"""Loopback UI transport over the T03 bus and T04 ledger, with no engine logic."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import hmac
import logging
import re
import secrets
import socket
from collections.abc import Awaitable, Callable, Iterator, Sequence
from pathlib import Path
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import RLock, Thread
from typing import Annotated, Literal

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import (
    AfterValidator,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from specvania.agent.approval import ApprovalRequest, DesktopResponder
from specvania.agent.sandbox import SandboxRefusal
from specvania.clock import Clock
from specvania.config import Language
from specvania.ledger import Ledger, LedgerError
from specvania.provider.client import ProbeModelResult
from specvania.run.bus import EventBus, Subscription
from specvania.run.events import (
    ApprovalRequested,
    Event,
    FrozenModel,
    GateResult,
    RedactionGuard,
    Review,
    SecretLeakError,
    ShortText,
    WorldPayload,
)
from specvania.run.gitops import GitError, GitOps
from specvania.run.jsonl import EventStreamError, decode_events
from specvania.run.state import RoomState, RunState, StateFoldError, fold_events
from specvania.sdd.world import WorldParseError, parse_world
from specvania.ui.launcher import open_browser


class TokenLogFormatter(logging.Formatter):
    """Redact even exception text before a log handler writes to stderr."""

    def __init__(self, tokens: tuple[str, ...]) -> None:
        super().__init__()
        self.tokens = tokens

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for token in self.tokens:
            rendered = rendered.replace(token, "[redacted]")
        return rendered


class UIServerError(Exception):
    """An invalid replay or unavailable local UI that the player can fix."""


class RoomCodeError(UIServerError):
    """A repository or room is unavailable; never carries Git diagnostics."""


class RoomCodeFile(FrozenModel):
    path: str
    status: str
    content: str | None = Field(default=None, exclude_if=lambda value: value is None)
    diff: str | None = Field(default=None, exclude_if=lambda value: value is None)
    truncated: bool = False


class RoomChecklist(FrozenModel):
    item: str
    ok: bool | None = None
    note: ShortText = ""


class RoomCode(FrozenModel):
    room_id: str
    checklist: tuple[RoomChecklist, ...] = ()
    review: Review | None = None
    gates: tuple[GateResult, ...] = ()
    kind: Literal["commit", "working", "files"]
    commit: str | None = None
    files: tuple[RoomCodeFile, ...] = ()
    truncated: bool = False
    reason: str | None = None


def _cap_body(body: str, limit: int, marker: str) -> str:
    # Reserve the marker inside the cap, and never cut a UTF-8 code point.
    suffix = f"\n… truncated by specvania ({marker} cap)\n"
    prefix = body.encode("utf-8")[: max(0, limit - len(suffix.encode("utf-8")))]
    return prefix.decode("utf-8", errors="ignore") + suffix


def _cap_room_code(response: RoomCode, candidates: Iterator[RoomCodeFile]) -> RoomCode:
    """Bound the encoded JSON, including escaping, metadata and the marker."""
    # Mission/review metadata shares the existing response cap. Remove the
    # latest appended metadata only when necessary and expose the same marker.
    while len(response.model_dump_json().encode("utf-8")) > 900_000:
        updates: dict[str, object] = {
            "truncated": True,
            "reason": _cap_body("", 0, "1 MB"),
        }
        if response.gates:
            updates["gates"] = response.gates[:-1]
        elif response.review is not None:
            updates["review"] = None
        elif response.checklist:
            updates["checklist"] = response.checklist[:-1]
        else:
            break
        response = response.model_copy(update=updates)
    files: list[RoomCodeFile] = []
    for file in candidates:
        key = "diff" if file.diff is not None else "content"
        body = file.diff if file.diff is not None else file.content or ""
        if len(body.encode("utf-8")) > 200_000:
            body = _cap_body(body, 200_000, "200 kB")
            file = file.model_copy(update={key: body, "truncated": True})
        proposed = response.model_copy(
            update={
                "files": tuple([*files, file]),
                "truncated": response.truncated or file.truncated,
            }
        )
        if len(proposed.model_dump_json().encode("utf-8")) <= 1_000_000:
            files.append(file)
            response = proposed
            continue
        # Binary search the prefix using actual JSON size (quotes/newlines escape).
        low, high = 0, len(body.encode("utf-8"))
        best: RoomCode | None = None
        while low <= high:
            limit = (low + high) // 2
            shortened = file.model_copy(
                update={key: _cap_body(body, limit, "1 MB"), "truncated": True}
            )
            proposed = response.model_copy(
                update={"files": tuple([*files, shortened]), "truncated": True}
            )
            if len(proposed.model_dump_json().encode("utf-8")) <= 1_000_000:
                best, low = proposed, limit + 1
            else:
                high = limit - 1
        if best is not None:
            return best
        # Metadata alone can fill the response: put the marker on the last file.
        while files:
            last = files.pop()
            last_key = "diff" if last.diff is not None else "content"
            marked = last.model_copy(
                update={last_key: _cap_body("", 0, "1 MB"), "truncated": True}
            )
            proposed = response.model_copy(
                update={"files": tuple([*files, marked]), "truncated": True}
            )
            if len(proposed.model_dump_json().encode("utf-8")) <= 1_000_000:
                return proposed
        return response.model_copy(
            update={"files": (), "truncated": True, "reason": _cap_body("", 0, "1 MB")}
        )
    return response


type StudioPhase = Literal[
    "idle",
    "project_open",
    "planning",
    "world_ready",
    "running",
    "finished",
    "replaying",
]


class ClarifyPrompt(FrozenModel):
    question_id: ShortText
    text: ShortText
    options: tuple[ShortText, ...] = ()
    default: ShortText


class StudioProject(FrozenModel):
    path: str
    initialized: bool


class ReplayStatus(FrozenModel):
    name: str
    feature: str = ""
    speed: Literal[1, 2, 4] = 1
    paused: bool = False
    finished: bool = False
    stream: Literal["events", "plan", "run"] = "events"
    dialogue: Event | None = None


class StudioState(FrozenModel):
    kind: Literal["studio_state"] = "studio_state"
    phase: StudioPhase
    project: StudioProject | None
    language: Language = "en"
    worlds: tuple[ShortText, ...] = ()
    world: WorldPayload | None = None
    world_slug: ShortText | None = None
    open_bugs: int = Field(default=0, ge=0)
    clarify_request: ClarifyPrompt | None = None
    pause_request: ShortText | None = None
    error: ShortText | None = None
    replay: ReplayStatus | None = None


class ControlError(UIServerError):
    def __init__(self, reason: str, *, dirty_paths: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.dirty_paths = dirty_paths


class StateMessage(FrozenModel):
    kind: Literal["state"] = "state"
    state: RunState
    phase: StudioPhase | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    replay: ReplayStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    approval_request: ApprovalRequest | None = None


class EventMessage(FrozenModel):
    kind: Literal["event"] = "event"
    event: Event
    approval_request: ApprovalRequest | None = None


class ApprovalAnswer(FrozenModel):
    kind: Literal["approval_answer"]
    request_id: ShortText
    answer: bool = Field(strict=True)
    control_token: str = Field(default="", repr=False, exclude=True)


class OpenReplay(FrozenModel):
    kind: Literal["open_replay"]
    name: Literal["demo", "real-run"] | None = None
    control_token: str = Field(default="", repr=False, exclude=True)


class ReplaySpeed(FrozenModel):
    kind: Literal["replay_speed"]
    speed: Literal[1, 2, 4]

    @field_validator("speed", mode="before")
    @classmethod
    def integer_speed(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Use replay speed 1, 2 or 4.")
        return value

    control_token: str = Field(default="", repr=False, exclude=True)


class ReplayPause(FrozenModel):
    kind: Literal["replay_pause"]
    paused: bool = Field(strict=True)
    control_token: str = Field(default="", repr=False, exclude=True)


class CloseReplay(FrozenModel):
    kind: Literal["close_replay"]
    control_token: str = Field(default="", repr=False, exclude=True)


class Shutdown(FrozenModel):
    kind: Literal["shutdown"]
    control_token: str = Field(default="", repr=False, exclude=True)


class OpenProject(FrozenModel):
    kind: Literal["open_project"]
    path: str = Field(min_length=1)
    control_token: str = Field(default="", repr=False, exclude=True)


class InitProject(FrozenModel):
    kind: Literal["init_project"]
    control_token: str = Field(default="", repr=False, exclude=True)


class StartPlan(FrozenModel):
    kind: Literal["start_plan"]
    idea: str = Field(min_length=1)
    no_clarify: bool = Field(default=False, strict=True)
    control_token: str = Field(default="", repr=False, exclude=True)


class ClarifyReply(FrozenModel):
    kind: Literal["clarify_answer"]
    question_id: str = Field(min_length=1)
    answer: str
    control_token: str = Field(default="", repr=False, exclude=True)


def _world_slug(value: str) -> str:
    if value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError("Choose a world folder name.")
    return value


type WorldSlug = Annotated[
    str, Field(min_length=1, max_length=200), AfterValidator(_world_slug)
]


class SaveWorld(FrozenModel):
    kind: Literal["save_world"]
    slug: WorldSlug
    control_token: str = Field(default="", repr=False, exclude=True)


class StartRun(FrozenModel):
    kind: Literal["start_run"]
    slug: WorldSlug
    allow_dirty: bool = Field(default=False, strict=True)
    token_budget: int | None = Field(default=None, strict=True, gt=0)

    @field_validator("token_budget", mode="before")
    @classmethod
    def positive_budget(cls, value: object) -> object:
        if value is None:
            raise ValueError("Token budget must be a positive integer.")
        return value

    control_token: str = Field(default="", repr=False, exclude=True)


class ProbeParty(FrozenModel):
    kind: Literal["probe_party"]
    control_token: str = Field(default="", repr=False, exclude=True)


class ProbeResult(FrozenModel):
    kind: Literal["probe_result"] = "probe_result"
    results: tuple[ProbeModelResult, ...]


class PauseReply(FrozenModel):
    kind: Literal["pause_decision"]
    choice: Literal["retry", "skip", "abort"]
    control_token: str = Field(default="", repr=False, exclude=True)


class SetLanguage(FrozenModel):
    kind: Literal["set_language"]
    language: Language
    control_token: str = Field(default="", repr=False, exclude=True)


type StudioControl = (
    Shutdown
    | OpenReplay
    | ReplaySpeed
    | ReplayPause
    | CloseReplay
    | OpenProject
    | InitProject
    | StartPlan
    | ClarifyReply
    | SaveWorld
    | StartRun
    | PauseReply
    | SetLanguage
    | ProbeParty
)
type ControlMessage = Annotated[
    ApprovalAnswer | StudioControl, Field(discriminator="kind")
]


class ApprovalRejected(FrozenModel):
    kind: Literal["approval_rejected"] = "approval_rejected"
    reason: ShortText
    dirty_paths: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)


def _matches(candidate: str | None, expected: str) -> bool:
    return candidate is not None and hmac.compare_digest(
        candidate.encode("utf-8"), expected.encode("utf-8")
    )


def load_recording(
    directory: Path, *, guard: RedactionGuard
) -> tuple[tuple[Literal["events", "plan", "run"], tuple[Event, ...]], ...]:
    """Two independent projections, in plan/run order, or one legacy stream."""
    names: tuple[Literal["events", "plan", "run"], ...] = (
        ("plan", "run") if (directory / "plan.jsonl").exists() else ("events",)
    )
    return tuple(
        (name, load_replay(directory / f"{name}.jsonl", guard=guard)) for name in names
    )


def validate_replay(events: Sequence[Event]) -> None:
    # A run boundary resets seq; do not rewrite recorded ids or event payloads.
    groups: dict[str, list[Event]] = {}
    previous = ""
    for event in events:
        if event.run_id != previous and event.run_id in groups:
            raise StateFoldError("Replay run streams must be consecutive.")
        groups.setdefault(event.run_id, []).append(event)
        previous = event.run_id
    for group in groups.values():
        fold_events(group)


def load_replay(
    source: str | Path, *, guard: RedactionGuard, ledger: Ledger | None = None
) -> tuple[Event, ...]:
    """Read a portable stream/directory or a run through the Ledger API."""
    try:
        if isinstance(source, Path) and source.is_dir():
            return tuple(
                event
                for _, stream in load_recording(source, guard=guard)
                for event in stream
            )
        if isinstance(source, Path) or source.endswith(".jsonl"):
            events = decode_events(Path(source).read_text(encoding="utf-8"), guard)
        elif ledger is not None:
            events = tuple(ledger.list_events(source))
        else:
            raise UIServerError("Replay of a run id requires a ledger.")
        if not events:
            raise UIServerError("Cannot replay an empty event stream.")
        for event in events:
            guard.validate(event)
        validate_replay(events)
        return events
    except (OSError, UnicodeError, EventStreamError, LedgerError, StateFoldError):
        raise UIServerError(
            "Cannot replay; check the run id or JSONL event stream."
        ) from None


class UIServer:
    """One process owns its tokens; dependencies and projections stay instance-owned.

    Live connections subscribe before reading the ledger. Events that overlap the
    snapshot are discarded by seq. An append not yet published is in the snapshot;
    a later append is queued. Publishers must append before publishing (T03/T04).
    Replay connections each start with an empty projection and play the same stream.
    Planning can instead supply an in-memory snapshot while its ledger row does
    not yet exist; its publisher serializes snapshot updates and bus publication.
    """

    def __init__(
        self,
        *,
        guard: RedactionGuard,
        clock: Clock,
        run_id: str = "",
        ledger: Ledger | None = None,
        bus: EventBus | None = None,
        live_snapshot: Callable[[], Sequence[Event]] | None = None,
        replay: Sequence[Event] | None = None,
        speed: Literal[1, 2, 4] = 1,
        port: int = 0,
        approval_responder: Callable[[ApprovalAnswer], bool | None] | None = None,
        studio_snapshot: Callable[[], StateMessage] | None = None,
        control_responder: Callable[[StudioControl], bool | ProbeResult] | None = None,
        studio_info: Callable[[], StudioState | None] | None = None,
        repository_root: Path | None = None,
        world_directory: Path | None = None,
        code_repository: Callable[[], tuple[Path | None, Path | None]] | None = None,
    ) -> None:
        if speed not in (1, 2, 4) or not 0 <= port <= 65535:
            raise UIServerError("Use replay speed 1, 2 or 4 and a valid local port.")
        if (
            studio_snapshot is None
            and replay is None
            and (bus is None or (ledger is None and live_snapshot is None))
        ):
            raise UIServerError("Live UI requires an event bus and a run snapshot.")
        self.studio_info = studio_info
        self.repository_root = repository_root
        self.world_directory = world_directory
        self.code_repository = code_repository
        self.studio_snapshot = studio_snapshot
        self.control_responder = control_responder
        self._state_lock = RLock()
        self._state_queues: list[Queue[StateMessage | StudioState | EventMessage]] = []
        self._serving: uvicorn.Server | None = None
        self.guard = guard
        self.clock = clock
        self.run_id = run_id
        self.ledger = ledger
        self.bus = bus
        self.live_snapshot = live_snapshot
        self.replay = tuple(replay) if replay is not None else None
        self.speed: Literal[1, 2, 4] = speed
        self.port = port
        # The live caller registers this instance in its ApprovalBroker alongside
        # TerminalResponder; replay never accepts answers, even with a token.
        self.desktop_responder = DesktopResponder()
        self.approval_responder = approval_responder or self._answer_approval
        self.view_token = secrets.token_urlsafe(32)
        self.control_token = secrets.token_urlsafe(32)
        guard.register(self.view_token)
        guard.register(self.control_token)
        if self.replay is not None:
            for event in self.replay:
                guard.validate(event)
            try:
                validate_replay(self.replay)
            except StateFoldError:
                raise UIServerError(
                    "Replay sequences must be strictly increasing."
                ) from None
        # Studio has no Ledger attached to the UI server. Keep only the latest
        # per-room review/gates, drained from its local bus when code is opened.
        self._panel_subscription = (
            bus.subscribe() if studio_snapshot is not None and bus else None
        )
        self._panel_run = ""
        self._panel_reviews: dict[str, Review] = {}
        self._panel_gates: dict[str, list[GateResult]] = {}
        self.app = self._create_app()

    def publish_studio_state(self) -> None:
        if self.studio_snapshot is not None:
            message = self.studio_snapshot()
            info = self.studio_info() if self.studio_info else None
            with self._state_lock:
                for queue in self._state_queues:
                    queue.put(message)
                    if info is not None:
                        queue.put(info)

    def publish_replay_event(self, event: Event) -> None:
        self.guard.validate(event)
        message = EventMessage(event=event)
        with self._state_lock:
            for queue in self._state_queues:
                queue.put(message)

    def publish_studio_info(self) -> None:
        if self.studio_info is not None and (message := self.studio_info()) is not None:
            with self._state_lock:
                for queue in self._state_queues:
                    queue.put(message)

    def stop(self) -> None:
        if self._panel_subscription is not None:
            self._panel_subscription.close()
        if self._serving is not None:
            self._serving.should_exit = True

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def view_cookie_name(self) -> str:
        return f"specvania_view_{self.port}"

    def _answer_approval(self, message: ApprovalAnswer) -> bool:
        accepted = self.desktop_responder.answer(message.request_id, message.answer)
        if accepted:
            self.publish_studio_info()
        return accepted

    def _approval_prompt(self, event: ApprovalRequested) -> ApprovalRequest:
        pending = self.desktop_responder.pending
        if pending is not None and pending.request_id == event.request_id:
            # Full command/cwd are transient prompt data, never ledger payloads.
            # Validate the full transient text against all registered secrets
            # without appending it to the bus/ledger or changing event limits.
            self.guard.validate(
                event.model_copy(
                    update={"command": pending.command, "cwd": pending.cwd}
                )
            )
            return pending
        return ApprovalRequest(
            request_id=event.request_id, command=event.command, cwd=event.cwd
        )

    def _event_message(self, event: Event) -> dict[str, object]:
        message = EventMessage(
            event=event,
            approval_request=self._approval_prompt(event)
            if isinstance(event, ApprovalRequested)
            else None,
        )
        # Preserve T14's exact envelope for events with no approval prompt.
        return message.model_dump(
            mode="json",
            exclude={"approval_request"} if message.approval_request is None else None,
        )

    def _create_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def authenticate(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            token = request.query_params.get("view_token")
            if token is None:
                token = request.cookies.get(self.view_cookie_name)
            if not _matches(token, self.view_token):
                response = JSONResponse(
                    {"detail": "View token required"}, status_code=401
                )
            elif request.headers.get("host") != f"127.0.0.1:{self.port}":
                response = JSONResponse(
                    {"detail": "Loopback host required"}, status_code=403
                )
            else:
                response = await call_next(request)
                # Only the view token goes in the same-site asset cookie.
                response.set_cookie(
                    self.view_cookie_name,
                    self.view_token,
                    httponly=True,
                    samesite="strict",
                )
            response.headers["referrer-policy"] = "no-referrer"
            response.headers["cache-control"] = "no-store"
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["content-security-policy"] = (
                "default-src 'self'; img-src 'self' data: blob:; "
                "script-src 'self' 'unsafe-inline'; "
                "font-src 'self'; worker-src 'self'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; connect-src 'self' "
                f"ws://127.0.0.1:{self.port}"
            )
            return response

        @app.get("/api/rooms/{room_id}/code")
        def room_code(room_id: str) -> Response:
            try:
                result = self.room_code(room_id)
            except RoomCodeError as error:
                return JSONResponse({"detail": str(error)}, status_code=404)
            return JSONResponse(result.model_dump(mode="json"))

        app.websocket("/ws")(self._websocket)
        app.mount(
            "/", StaticFiles(directory=Path(__file__).parent / "static", html=True)
        )
        return app

    def _redact_code(self, text: str) -> str:
        return self.guard.redact(text)

    def room_code(self, room_id: str) -> RoomCode:
        """Read repository bodies only here, never through events or the ledger."""
        root, directory = (
            self.code_repository()
            if self.code_repository is not None
            else (self.repository_root, self.world_directory)
        )
        if root is None:
            raise RoomCodeError("Repository unavailable for this view.")
        if re.fullmatch(r"T\d{2,}", room_id, flags=re.ASCII) is None:
            raise RoomCodeError("Room unavailable in the current world.")
        try:
            snapshot: Sequence[Event] = ()
            if self.studio_snapshot is not None:
                state = self.studio_snapshot().state
                with self._state_lock:
                    if self._panel_run != (state.run_id or ""):
                        self._panel_reviews.clear()
                        self._panel_gates.clear()
                        self._panel_run = state.run_id or ""
                    if self._panel_subscription is not None:
                        while True:
                            try:
                                event = self._panel_subscription.get(timeout=0)
                            except Empty:
                                break
                            if event.run_id != state.run_id:
                                continue
                            if event.type == "attempt_started":
                                self._panel_gates[event.room_id] = []
                                self._panel_reviews.pop(event.room_id, None)
                            elif isinstance(event, Review):
                                self._panel_reviews[event.room_id] = event
                            elif isinstance(event, GateResult):
                                self._panel_gates.setdefault(event.room_id, []).append(
                                    event
                                )
                    snapshot = tuple(self._panel_reviews.values()) + tuple(
                        gate for gates in self._panel_gates.values() for gate in gates
                    )
            else:
                snapshot = (
                    self.replay
                    if self.replay is not None
                    else self.live_snapshot()
                    if self.live_snapshot is not None
                    else self.ledger.list_events(self.run_id)
                    if self.ledger
                    else ()
                )
                state = fold_events(snapshot)
            git = GitOps(root)
            progress = next((room for room in state.rooms if room.id == room_id), None)
            if state.rooms and progress is None:
                raise RoomCodeError("Room unavailable in the current world.")
            commit = (
                progress.commit
                if progress and progress.state == RoomState.CLEARED
                else None
            )
            kind: Literal["commit", "working", "files"] = (
                "commit"
                if commit
                else "working"
                if progress and progress.state == RoomState.ACTIVE
                else "files"
            )
            directory = directory or (
                git.root / ".specvania/specs" / state.feature if state.feature else None
            )
            untracked = git._paths("--others") if kind == "working" else ()
            available = git.code_files() if kind == "files" else ()
            paths = (
                tuple((name, "file") for name in available)
                if kind == "files"
                else git.code_paths(commit, untracked=untracked)
            )
            document_names = (
                tuple(
                    str(directory.absolute() / name) for name in ("spec.md", "tasks.md")
                )
                if directory is not None
                else ()
            )
            resolve = git.code_resolver((*document_names, *(name for name, _ in paths)))
            world = None
            if directory is not None and (
                kind == "files"
                or (
                    (directory / "spec.md").is_file()
                    and (directory / "tasks.md").is_file()
                )
            ):
                # Validate the documents before parsing: they too can be symlinks.
                safe_directory = resolve(str(directory.absolute()))
                for name in ("spec.md", "tasks.md"):
                    resolve(str(safe_directory / name))
                world = parse_world(safe_directory)
            room = (
                next((room for room in world.rooms if room.id == room_id), None)
                if world
                else None
            )
            if not state.rooms and room is None:
                raise RoomCodeError("Room unavailable in the current world.")
            review = next(
                (
                    event
                    for event in reversed(snapshot)
                    if isinstance(event, Review) and event.room_id == room_id
                ),
                None,
            )
            gates = tuple(
                event
                for event in snapshot
                if isinstance(event, GateResult) and event.room_id == room_id
            )
            if self.studio_snapshot is None:
                attempt_seq = max(
                    (
                        event.seq
                        for event in snapshot
                        if event.type == "attempt_started" and event.room_id == room_id
                    ),
                    default=0,
                )
                gates = tuple(gate for gate in gates if gate.seq > attempt_seq)
                if review and review.seq < attempt_seq:
                    review = None
            results = {item.item: item for item in review.checklist} if review else {}
            checklist = (
                tuple(
                    RoomChecklist(
                        item=self._redact_code(item.text),
                        ok=results[item.text].ok
                        if item.text in results
                        else True
                        if item.checked
                        else None,
                        note=results[item.text].note if item.text in results else "",
                    )
                    for item in room.checklist
                )
                if room
                else tuple(
                    RoomChecklist(item=item.item, ok=item.ok, note=item.note)
                    for item in review.checklist
                )
                if review
                else ()
            )
            response = RoomCode(
                room_id=room_id,
                kind=kind,
                commit=commit,
                checklist=checklist,
                review=review,
                gates=gates,
            )
            if kind == "files":
                if room is None:
                    raise RoomCodeError("Room files unavailable in the current world.")
                selected: set[str] = set()
                for entry in room.files:
                    try:
                        path = resolve(entry)
                    except SandboxRefusal:
                        continue
                    name = path.relative_to(git.root).as_posix()
                    if path.is_dir():
                        prefix = "" if name == "." else name + "/"
                        selected.update(
                            candidate
                            for candidate in available
                            if candidate.startswith(prefix)
                        )
                    elif path.is_file():
                        selected.add(name)
                paths = tuple((name, "file") for name in sorted(selected))

            approved: list[tuple[str, str]] = []
            for name, status in paths:
                try:
                    resolve(name)
                except SandboxRefusal:
                    continue
                approved.append((name, status))
            untracked_set = frozenset(untracked)

            def bodies() -> Iterator[RoomCodeFile]:
                diffs: dict[str, str] | None = None
                attributes: dict[str, str] | None = None
                for name, status in approved:
                    try:
                        path = resolve(name)
                    except SandboxRefusal:
                        continue
                    truncated = False
                    margin = 0
                    if kind == "files":
                        if not path.is_file():
                            continue
                        # Include secret lookahead and a UTF-8 code point/probe.
                        margin = self.guard.max_secret_bytes + 5
                        read_limit = 200_000 + margin
                        with path.open("rb") as stream:
                            data = stream.read(read_limit)
                        truncated = len(data) == read_limit
                        try:
                            if b"\0" in data:
                                body = "Binary file (content omitted).\n"
                                truncated = False
                            else:
                                body = codecs.getincrementaldecoder("utf-8")().decode(
                                    data, final=not truncated
                                )
                        except UnicodeError:
                            body = "Binary file (content omitted).\n"
                            truncated = False
                        key = "content"
                    else:
                        if name in untracked_set:
                            if attributes is None:
                                attributes = git.code_attributes(
                                    tuple(
                                        candidate
                                        for candidate, _ in approved
                                        if candidate in untracked_set
                                    )
                                )
                            margin = self.guard.max_secret_bytes + 5
                            body, truncated = git.code_addition(
                                name, path, attribute=attributes[name], margin=margin
                            )
                        else:
                            if diffs is None:
                                diffs = git.code_diffs(
                                    tuple(
                                        candidate
                                        for candidate, _ in approved
                                        if candidate not in untracked_set
                                    ),
                                    commit=commit,
                                )
                            body = diffs.get(name, "")
                        key = "diff"
                    redacted = self._redact_code(body)
                    if truncated:
                        # Redaction can shrink the body and bring an incomplete
                        # secret at the read boundary inside the output cap.
                        # Discard the lookahead after redaction, before capping.
                        encoded = redacted.encode("utf-8")
                        redacted = encoded[: max(0, len(encoded) - margin)].decode(
                            "utf-8", errors="ignore"
                        )
                        redacted = _cap_body(redacted, 200_000, "200 kB")
                    yield RoomCodeFile(
                        path=self._redact_code(name),
                        status=status,
                        content=redacted if key == "content" else None,
                        diff=redacted if key == "diff" else None,
                        truncated=truncated,
                    )

            return _cap_room_code(response, bodies())
        except GitError as error:
            raise RoomCodeError(str(error)) from None
        except (
            OSError,
            UnicodeError,
            SandboxRefusal,
            WorldParseError,
            LedgerError,
            StateFoldError,
            SecretLeakError,
        ):
            raise RoomCodeError(
                "Room code unavailable; check repository and world files."
            ) from None

    async def _websocket(self, websocket: WebSocket) -> None:
        if not _matches(websocket.query_params.get("view_token"), self.view_token):
            await websocket.send_denial_response(
                JSONResponse({"detail": "View token required"}, status_code=401)
            )
            return
        if (
            websocket.headers.get("origin") != self.origin
            or websocket.headers.get("host") != f"127.0.0.1:{self.port}"
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        subscription = (
            self.bus.subscribe(None if self.studio_snapshot else self.run_id)
            if self.replay is None and self.bus
            else None
        )
        state_queue: Queue[StateMessage | StudioState | EventMessage] | None = None
        if self.studio_snapshot is not None:
            state_queue = Queue()
            with self._state_lock:
                self._state_queues.append(state_queue)
        sender: asyncio.Task[None] | None = None
        receiver: asyncio.Task[None] | None = None
        try:
            initial: StateMessage | None = None
            pending: ApprovalRequested | None = None
            if self.studio_snapshot is not None:
                initial = self.studio_snapshot()
                state = initial.state
            elif subscription is not None:
                if self.ledger is not None:
                    snapshot = await asyncio.to_thread(
                        self.ledger.list_events, self.run_id
                    )
                else:
                    assert self.live_snapshot is not None
                    snapshot = await asyncio.to_thread(self.live_snapshot)
                    for event in snapshot:
                        self.guard.validate(event)
                state = fold_events(snapshot)
                for event in snapshot:
                    if event.type == "approval_requested":
                        pending = event
                    elif event.type == "run_finished":
                        pending = None
                    elif (
                        event.type == "approval_answered"
                        and pending is not None
                        and event.request_id == pending.request_id
                    ):
                        pending = None
            else:
                state = RunState()
            await websocket.send_json(
                (
                    initial
                    if initial is not None
                    else StateMessage(
                        state=state,
                        replay=ReplayStatus(name="recording", speed=self.speed)
                        if self.replay is not None
                        else None,
                        approval_request=self._approval_prompt(pending)
                        if pending
                        else None,
                    )
                ).model_dump(mode="json")
            )
            if self.studio_info is not None:
                info = self.studio_info()
                if info is not None:
                    await websocket.send_json(info.model_dump(mode="json"))
            sender = asyncio.create_task(
                self._send(websocket, subscription, state.seq, state_queue)
            )
            receiver = asyncio.create_task(self._receive(websocket))
            # In replay, the receiver stays available after the last event; closing
            # the tab cancels either a live bridge or an in-progress replay wait.
            completed, _ = await asyncio.wait(
                (sender, receiver), return_when=asyncio.FIRST_COMPLETED
            )
            if sender in completed:
                await sender
            await receiver
        except WebSocketDisconnect:
            pass
        except (LedgerError, StateFoldError, SecretLeakError):
            logging.getLogger(__name__).warning("UI state unavailable")
            await websocket.close(code=1011)
        finally:
            if state_queue is not None:
                with self._state_lock:
                    self._state_queues.remove(state_queue)
            for worker in (sender, receiver):
                if worker is not None:
                    worker.cancel()
                    with contextlib.suppress(
                        asyncio.CancelledError,
                        WebSocketDisconnect,
                        LedgerError,
                        StateFoldError,
                        SecretLeakError,
                    ):
                        await worker
            if subscription is not None:
                subscription.close()

    async def _send(
        self,
        websocket: WebSocket,
        subscription: Subscription | None,
        seq: int,
        state_queue: Queue[StateMessage | StudioState | EventMessage] | None = None,
    ) -> None:
        if subscription is None:
            previous: Event | None = None
            for event in self.replay or ():
                if previous is not None and event.run_id != previous.run_id:
                    await websocket.send_json(
                        StateMessage(
                            state=RunState(),
                            replay=ReplayStatus(
                                name="recording", speed=self.speed, stream="run"
                            ),
                        ).model_dump(mode="json")
                    )
                if previous is not None:
                    gap = max(0.0, (event.ts - previous.ts).total_seconds())
                    # Cap recorded idle gaps before applying playback speed.
                    await asyncio.to_thread(
                        self.clock.sleep, min(gap, 2.0) / self.speed
                    )
                await websocket.send_json(self._event_message(event))
                previous = event
            return
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event | StateMessage | StudioState | EventMessage] = (
            asyncio.Queue()
        )
        stopped = ThreadEvent()

        def bridge() -> None:
            while not stopped.is_set():
                if state_queue is not None:
                    while True:
                        try:
                            message = state_queue.get_nowait()
                        except Empty:
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, message)
                try:
                    event = subscription.get(timeout=0.05)
                except Empty:
                    continue
                loop.call_soon_threadsafe(queue.put_nowait, event)

        thread = Thread(target=bridge, name="specvania-ui-bus", daemon=True)
        thread.start()
        try:
            while True:
                event = await queue.get()
                if isinstance(event, StateMessage):
                    await websocket.send_json(event.model_dump(mode="json"))
                    seq = event.state.seq
                    continue
                if isinstance(event, (StudioState, EventMessage)):
                    await websocket.send_json(event.model_dump(mode="json"))
                    continue
                if self.studio_snapshot is not None and event.run_id != self.run_id:
                    continue
                if event.seq <= seq:
                    continue
                await websocket.send_json(self._event_message(event))
                seq = event.seq
        finally:
            stopped.set()
            await asyncio.to_thread(thread.join)

    async def _receive(self, websocket: WebSocket) -> None:
        async def reject(reason: str, dirty_paths: tuple[str, ...] = ()) -> None:
            logging.getLogger(__name__).warning("UI approval message rejected")
            await websocket.send_json(
                ApprovalRejected(reason=reason, dirty_paths=dirty_paths).model_dump()
            )

        adapter = TypeAdapter(ControlMessage)
        while True:
            try:
                message = adapter.validate_python(await websocket.receive_json())
            except (ValidationError, ValueError):
                await reject("invalid message")
                continue
            if (
                not _matches(message.control_token, self.control_token)
                or self.replay is not None
            ):
                await reject("control unavailable")
                continue
            # Never pass the token across either responder boundary.
            answer = message.model_copy(update={"control_token": ""})
            try:
                if isinstance(answer, ApprovalAnswer):
                    if (
                        self.studio_snapshot
                        and self.studio_snapshot().replay is not None
                    ):
                        await reject("control unavailable")
                        continue
                    accepted = await asyncio.to_thread(self.approval_responder, answer)
                elif self.control_responder is not None:
                    accepted = await asyncio.to_thread(self.control_responder, answer)
                else:
                    await reject("control unavailable")
                    continue
                if accepted is False:
                    await reject("request unavailable")
                elif isinstance(accepted, ProbeResult):
                    await websocket.send_json(accepted.model_dump(mode="json"))
            except ControlError as error:
                await reject(str(error), error.dirty_paths)

    def serve(
        self,
        *,
        browser: bool = False,
        on_started: Callable[[], None] | None = None,
    ) -> None:
        """Bind only loopback, including OS-assigned ports, then run until stopped.

        The optional callback runs after startup with the assigned port available,
        so command rooms can choose a UI shell through the shared CLI launcher.
        Uvicorn access logging and INFO WebSocket logging are disabled because
        request targets contain the view token. The browser launches after
        uvicorn has started accepting.
        """
        ui = self

        class LocalServer(uvicorn.Server):
            async def startup(self, sockets: list[socket.socket] | None = None) -> None:
                await super().startup(sockets=sockets)
                if self.started:
                    if on_started is not None:
                        on_started()
                    elif browser:
                        open_browser(ui.port, ui.view_token)

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", self.port))
                self.port = listener.getsockname()[1]
                config = uvicorn.Config(
                    self.app,
                    host="127.0.0.1",
                    port=self.port,
                    access_log=False,
                    log_level="warning",
                    log_config={
                        "version": 1,
                        "disable_existing_loggers": False,
                        "formatters": {
                            "safe": {
                                "()": lambda: TokenLogFormatter(
                                    (self.view_token, self.control_token)
                                )
                            },
                        },
                        "handlers": {
                            "stderr": {
                                "formatter": "safe",
                                "class": "logging.StreamHandler",
                                "stream": "ext://sys.stderr",
                            }
                        },
                        "loggers": {
                            "uvicorn": {
                                "handlers": ["stderr"],
                                "level": "WARNING",
                                "propagate": False,
                            },
                            "uvicorn.error": {"level": "WARNING", "propagate": True},
                            "uvicorn.access": {"handlers": [], "propagate": False},
                        },
                    },
                )
                self._serving = LocalServer(config)
                self._serving.run(sockets=[listener])
                self._serving = None
        except OSError:
            raise UIServerError(
                "Cannot start the loopback UI; check the local port."
            ) from None
