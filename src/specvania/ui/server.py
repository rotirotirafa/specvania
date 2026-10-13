"""Loopback UI transport over the T03 bus and T04 ledger, with no engine logic."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import secrets
import socket
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import RLock, Thread
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import Field, ValidationError
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from specvania.agent.approval import ApprovalRequest, DesktopResponder
from specvania.clock import Clock
from specvania.ledger import Ledger, LedgerError
from specvania.run.bus import EventBus, Subscription
from specvania.run.events import (
    ApprovalRequested,
    Event,
    FrozenModel,
    RedactionGuard,
    SecretLeakError,
    ShortText,
)
from specvania.run.jsonl import EventStreamError, decode_events
from specvania.run.state import RunState, StateFoldError, fold_events
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


type StudioPhase = Literal[
    "idle", "project_open", "planning", "world_ready", "running", "finished"
]


class StateMessage(FrozenModel):
    kind: Literal["state"] = "state"
    state: RunState
    phase: StudioPhase | None = Field(
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


class Shutdown(FrozenModel):
    kind: Literal["shutdown"]
    control_token: str = Field(default="", repr=False, exclude=True)


class ApprovalRejected(FrozenModel):
    kind: Literal["approval_rejected"] = "approval_rejected"
    reason: Literal["invalid message", "control unavailable", "request unavailable"]


def _matches(candidate: str | None, expected: str) -> bool:
    return candidate is not None and hmac.compare_digest(
        candidate.encode("utf-8"), expected.encode("utf-8")
    )


def load_replay(
    source: str | Path, *, guard: RedactionGuard, ledger: Ledger | None = None
) -> tuple[Event, ...]:
    """Read a portable JSONL stream or a run through the public Ledger API."""
    try:
        if isinstance(source, Path) or source.endswith(".jsonl"):
            events = decode_events(Path(source).read_text(encoding="utf-8"), guard)
        elif ledger is not None:
            events = tuple(ledger.list_events(source))
        else:
            raise UIServerError("Replay of a run id requires a ledger.")
        for event in events:
            guard.validate(event)
        fold_events(events)
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
        control_responder: Callable[[Shutdown], bool] | None = None,
    ) -> None:
        if speed not in (1, 2, 4) or not 0 <= port <= 65535:
            raise UIServerError("Use replay speed 1, 2 or 4 and a valid local port.")
        if (
            studio_snapshot is None
            and replay is None
            and (bus is None or (ledger is None and live_snapshot is None))
        ):
            raise UIServerError("Live UI requires an event bus and a run snapshot.")
        self.studio_snapshot = studio_snapshot
        self.control_responder = control_responder
        self._state_lock = RLock()
        self._state_queues: list[Queue[StateMessage]] = []
        self._serving: uvicorn.Server | None = None
        self.guard = guard
        self.clock = clock
        self.run_id = run_id
        self.ledger = ledger
        self.bus = bus
        self.live_snapshot = live_snapshot
        self.replay = tuple(replay) if replay is not None else None
        self.speed = speed
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
                if event.run_id != run_id:
                    raise UIServerError(
                        "Replay events must belong to the selected run."
                    )
            try:
                fold_events(self.replay)
            except StateFoldError:
                raise UIServerError(
                    "Replay sequences must be strictly increasing."
                ) from None
        self.app = self._create_app()

    def publish_studio_state(self) -> None:
        if self.studio_snapshot is not None:
            message = self.studio_snapshot()
            with self._state_lock:
                for queue in self._state_queues:
                    queue.put(message)

    def stop(self) -> None:
        if self._serving is not None:
            self._serving.should_exit = True

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def view_cookie_name(self) -> str:
        return f"specvania_view_{self.port}"

    def _answer_approval(self, message: ApprovalAnswer) -> bool:
        return self.desktop_responder.answer(message.request_id, message.answer)

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
                return JSONResponse({"detail": "View token required"}, status_code=401)
            if request.headers.get("host") != f"127.0.0.1:{self.port}":
                return JSONResponse(
                    {"detail": "Loopback host required"}, status_code=403
                )
            response = await call_next(request)
            # Relative ES modules and font requests carry the view token in a
            # same-site cookie; the control token is never persisted in a cookie.
            response.set_cookie(
                self.view_cookie_name, self.view_token, httponly=True, samesite="strict"
            )
            response.headers["referrer-policy"] = "no-referrer"
            response.headers["cache-control"] = "no-store"
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["content-security-policy"] = (
                "default-src 'self'; img-src 'self' data: blob:; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                "frame-ancestors 'none'; connect-src 'self' "
                f"ws://127.0.0.1:{self.port}"
            )
            return response

        app.websocket("/ws")(self._websocket)
        app.mount(
            "/", StaticFiles(directory=Path(__file__).parent / "static", html=True)
        )
        return app

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
        state_queue: Queue[StateMessage] | None = None
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
                        approval_request=self._approval_prompt(pending)
                        if pending
                        else None,
                    )
                ).model_dump(mode="json")
            )
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
        state_queue: Queue[StateMessage] | None = None,
    ) -> None:
        if subscription is None:
            previous: Event | None = None
            for event in self.replay or ():
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
        queue: asyncio.Queue[Event | StateMessage] = asyncio.Queue()
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
        async def reject(
            reason: Literal[
                "invalid message", "control unavailable", "request unavailable"
            ],
        ) -> None:
            logging.getLogger(__name__).warning(
                "UI approval message rejected: %s", reason
            )
            await websocket.send_json(ApprovalRejected(reason=reason).model_dump())

        while True:
            try:
                payload = await websocket.receive_json()
                message = (
                    Shutdown.model_validate(payload)
                    if isinstance(payload, dict) and payload.get("kind") == "shutdown"
                    else ApprovalAnswer.model_validate(payload)
                )
            except (ValidationError, ValueError):
                await reject("invalid message")
                continue
            if (
                not _matches(message.control_token, self.control_token)
                or self.replay is not None
                or self.approval_responder is None
            ):
                await reject("control unavailable")
                continue
            if isinstance(message, Shutdown):
                if self.control_responder is None:
                    await reject("control unavailable")
                elif not await asyncio.to_thread(
                    self.control_responder,
                    message.model_copy(update={"control_token": ""}),
                ):
                    await reject("request unavailable")
                continue
            # Pass only non-secret fields across the responder boundary (T20).
            answer = message.model_copy(update={"control_token": ""})
            accepted = await asyncio.to_thread(self.approval_responder, answer)
            if accepted is False:
                await reject("request unavailable")

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
