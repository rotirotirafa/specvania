"""T24 studio transport, process lifetime and normal engine abort evidence."""

from threading import Event as ThreadEvent
from threading import Thread
from typing import cast

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient, WebSocketDenialResponse
from test_engine import Harness, write_world

from specvania.clock import FakeClock
from specvania.provider.client import Completion
from specvania.run.engine import RunCancelled, rebuild_run_state
from specvania.run.events import RedactionGuard
from specvania.ui.server import Shutdown, StudioPhase, TokenLogFormatter
from specvania.ui.studio import Studio, StudioError

pytest_plugins = ["test_engine"]

ORIGIN = "http://127.0.0.1:8765"


def studio() -> Studio:
    return Studio(guard=RedactionGuard(), clock=FakeClock(), port=8765)


def test_idle_studio_serves_game_under_existing_token_rules() -> None:
    instance = studio()
    server = instance.server
    with TestClient(server.app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 401
        page = client.get("/", params={"view_token": server.view_token})
        assert page.status_code == 200
        assert server.view_token not in page.text
        assert server.control_token not in page.text
        assert client.get("/main.js").status_code == 200
        with pytest.raises(WebSocketDenialResponse):
            with client.websocket_connect("/ws", headers={"Origin": ORIGIN}):
                pytest.fail("Missing view token accepted")
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            message = ws.receive_json()
            assert message["phase"] == "idle"
            assert message["state"]["run_id"] is None


def test_phases_broadcast_reconnect_and_reject_invalid_transitions() -> None:
    instance = studio()
    server = instance.server
    with TestClient(server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json()["phase"] == "idle"
            for phase in [
                "project_open",
                "planning",
                "world_ready",
                "running",
                "finished",
                "project_open",
            ]:
                instance.transition(cast(StudioPhase, phase))
                assert ws.receive_json()["phase"] == phase
            with pytest.raises(StudioError):
                instance.transition("finished")
            assert instance.phase == "project_open"
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json()["phase"] == "project_open"


@pytest.mark.parametrize("token", [None, "wrong", "view"])
def test_control_rejection_preserves_connection_for_later_valid_shutdown(
    token: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    instance = studio()
    server = instance.server
    with TestClient(server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}&control_token={server.control_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            message = {"kind": "shutdown"}
            if token is not None:
                message["control_token"] = (
                    server.view_token if token == "view" else token
                )
            ws.send_json(message)
            assert ws.receive_json() == {
                "kind": "approval_rejected",
                "reason": "control unavailable",
            }
            assert not instance.stopped.is_set()
            ws.send_json({"kind": "shutdown", "control_token": server.control_token})
            assert instance.stopped.wait(2)
    assert server.view_token not in caplog.text
    assert server.control_token not in caplog.text


def test_shutdown_envelope_is_typed_and_secret_excluded() -> None:
    message = Shutdown.model_validate({"kind": "shutdown", "control_token": "secret"})
    assert "secret" not in repr(message)
    assert message.model_dump() == {"kind": "shutdown"}
    with pytest.raises(ValidationError):
        Shutdown.model_validate({"kind": "shutdown", "unexpected": True})


def test_shutdown_active_worker_finishes_through_engine_and_ledger(
    harness: Harness,
) -> None:
    instance = Studio(guard=harness.guard, clock=harness.clock, port=8765)
    instance.transition("project_open")
    instance.transition("planning")
    instance.transition("world_ready")
    entered, cancelled = ThreadEvent(), ThreadEvent()
    directory = write_world(harness.root, [("T01", "A1", ())])

    def provider_call() -> Completion:
        entered.set()
        assert cancelled.wait(5)
        raise RunCancelled()

    engine = harness.engine(
        [provider_call], decide=lambda pause: "abort" if cancelled.is_set() else None
    )
    assert instance.server.bus is not None
    engine.bus = instance.server.bus
    failures: list[BaseException] = []

    def on_started(run_id: str) -> None:
        instance.attach_active_run(
            worker=worker,
            abort=cancelled.set,
            run_id=run_id,
            state=lambda: rebuild_run_state(harness.ledger, run_id),
        )

    def execute() -> None:
        try:
            engine.run(directory, on_started=on_started)
        except BaseException as error:
            failures.append(error)

    worker = Thread(target=execute)
    worker.start()
    try:
        assert entered.wait(5)
        server = instance.server
        with TestClient(server.app, base_url=ORIGIN) as client:
            with client.websocket_connect(
                f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
                headers={"Origin": ORIGIN},
            ) as ws:
                assert ws.receive_json()["phase"] == "running"
                ws.send_json(
                    {"kind": "shutdown", "control_token": server.control_token}
                )
                assert instance.stopped.wait(5)
        assert not failures
        assert not worker.is_alive()
        events = harness.ledger.list_events(engine.run_id)
        assert events[-1].type == "run_finished" and events[-1].status == "aborted"
        assert sum(event.type == "run_finished" for event in events) == 1
        assert not any(event.type in ("run_paused", "model_call") for event in events)
        assert harness.ledger.get_run(engine.run_id).status == "aborted"
        assert instance.phase == "finished"
        summary = (
            harness.root / ".specvania/runs" / engine.run_id / "summary.md"
        ).read_text()
        assert "aborted" in summary
    finally:
        cancelled.set()
        worker.join(5)


@pytest.mark.parametrize("active", [False, True])
def test_cli_handshake_once_after_real_requests_and_shutdown_exit_zero(
    harness: Harness,
    active: bool,
) -> None:
    """Real uvicorn HTTP/WS protocols over AF_UNIX without a socket-guard bypass.

    The sandbox forbids bind(). An inherited connected socket replaces only the
    accepting listener; production startup callback, ASGI app, protocol logging,
    CLI handshake and graceful shutdown run in a separate process.
    """
    import json
    import os
    import selectors
    import socket
    import subprocess
    import sys
    from pathlib import Path

    from websockets.sync.client import connect
    from websockets.typing import Origin

    if active:
        write_world(harness.root, [("T01", "A1", ())])
    client, connected = socket.socketpair()
    script = r"""
import socket
import sys
from types import SimpleNamespace
from specvania import cli
import specvania.ui.server as transport
import uvicorn

connected = socket.socket(fileno=int(sys.argv[1]))
class Listener:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def bind(self, address): assert address == ("127.0.0.1", 0)
    def getsockname(self): return ("127.0.0.1", 8765)
    def close(self): pass
transport.socket = SimpleNamespace(
    AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
    socket=lambda *args: Listener(),
)
async def startup(self, sockets=None):
    import asyncio
    self.servers = []
    await self.lifespan.startup()
    loop = asyncio.get_running_loop()
    await loop.create_connection(
        lambda: self.config.http_protocol_class(
            config=self.config, server_state=self.server_state,
            app_state=self.lifespan.state,
        ), sock=connected,
    )
    self.started = True
uvicorn.Server.startup = startup
if sys.argv[4] == "True":
    from pathlib import Path
    from threading import Event, Thread
    from specvania.clock import FakeClock
    from specvania.ledger import Ledger
    from specvania.provider.fake import ScriptedProvider
    from specvania.run.engine import Engine, RunCancelled, rebuild_run_state
    from specvania.run.events import RedactionGuard
    from test_engine import config
    root = Path(sys.argv[2])
    clock, guard = FakeClock(), RedactionGuard()
    ledger = Ledger.open(sys.argv[3], clock=clock, guard=guard)
    entered, cancelled = Event(), Event()
    original_studio = cli.Studio
    def make_studio(**kwargs):
        instance = original_studio(guard=guard, clock=clock)
        for phase in ("project_open", "planning", "world_ready"):
            instance.transition(phase)
        def blocked_call():
            entered.set()
            assert cancelled.wait(10)
            raise RunCancelled()
        engine = Engine(root=root, config=config(),
            provider=ScriptedProvider([blocked_call]), ledger=ledger,
            bus=instance.server.bus, clock=clock,
            decide=lambda pause: "abort" if cancelled.is_set() else None)
        def started(run_id):
            instance.attach_active_run(worker=worker, abort=cancelled.set,
                run_id=run_id, state=lambda: rebuild_run_state(ledger, run_id))
        worker = Thread(target=lambda: engine.run(root / "world", on_started=started))
        worker.start()
        assert entered.wait(5)
        make_studio.engine, make_studio.worker = engine, worker
        return instance
    cli.Studio = make_studio
cli.create_app()(["studio"], standalone_mode=False)
if sys.argv[4] == "True":
    assert not make_studio.worker.is_alive()
    events = ledger.list_events(make_studio.engine.run_id)
    assert events[-1].type == "run_finished" and events[-1].status == "aborted"
    assert sum(event.type == "run_finished" for event in events) == 1
    assert not any(event.type in ("run_paused", "model_call") for event in events)
    assert ledger.get_run(make_studio.engine.run_id).status == "aborted"
    ledger.close()

"""
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            [str(Path("src").resolve()), str(Path("tests").resolve())]
        ),
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(connected.fileno()),
            str(harness.root),
            harness.ledger_url,
            str(active),
        ],
        pass_fds=(connected.fileno(),),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    connected.close()
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), "No handshake before startup deadline"
        first = process.stdout.readline()
        assert first, process.stderr.read() if process.stderr else "Missing stderr"
        handshake = json.loads(first)
        assert set(handshake) == {"port", "view_token", "control_token"}
        assert type(handshake["port"]) is int and 0 < handshake["port"] <= 65535
        assert handshake["view_token"] and handshake["control_token"]
        assert handshake["view_token"] != handshake["control_token"]
        client.settimeout(5)
        for route, expected in [
            ("/", b"401"),
            ("/?view_token=" + handshake["view_token"], b"200"),
            ("/main.js?view_token=" + handshake["view_token"], b"200"),
        ]:
            client.sendall(
                f"GET {route} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n".encode()
            )
            response = b""
            while b"\r\n\r\n" not in response:
                response += client.recv(65536)
            headers, body = response.split(b"\r\n\r\n", 1)
            assert expected in headers.split(b"\r\n", 1)[0]
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            while len(body) < length:
                body += client.recv(65536)
        with connect(
            "ws://127.0.0.1:8765/ws?view_token=" + handshake["view_token"],
            sock=client,
            origin=Origin(ORIGIN),
            open_timeout=5,
        ) as ws:
            assert json.loads(ws.recv())["phase"] == ("running" if active else "idle")
            ws.send(json.dumps({"kind": "shutdown", "control_token": "wrong"}))
            assert json.loads(ws.recv())["kind"] == "approval_rejected"
            ws.send(
                json.dumps(
                    {"kind": "shutdown", "control_token": handshake["control_token"]}
                )
            )
        rest, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert rest == "", "stdout must contain exactly one handshake line"
        assert "rejected" in stderr
        for token in (handshake["view_token"], handshake["control_token"]):
            assert token not in stderr
    finally:
        client.close()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_log_formatter_redacts_tokens_in_messages_and_exceptions() -> None:
    import logging

    formatter = TokenLogFormatter(("view-secret", "control-secret"))
    try:
        raise ValueError("control-secret")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "studio",
            logging.WARNING,
            "",
            0,
            "request ?view_token=%s",
            ("view-secret",),
            sys.exc_info(),
        )
    rendered = formatter.format(record)
    assert "view-secret" not in rendered and "control-secret" not in rendered
    assert "[redacted]" in rendered


def test_shutdown_accepts_worker_that_finishes_before_abort_takes_effect(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_engine import action, boss, review

    instance = Studio(guard=harness.guard, clock=harness.clock, port=8765)
    for phase in ("project_open", "planning", "world_ready"):
        instance.transition(cast(StudioPhase, phase))
    entered, released = ThreadEvent(), ThreadEvent()
    directory = write_world(harness.root, [("T01", "A1", ())])

    def provider_call() -> Completion:
        entered.set()
        assert released.wait(5)
        return action()

    engine = harness.engine([provider_call, review(), boss()])
    assert instance.server.bus is not None
    engine.bus = instance.server.bus
    failures: list[BaseException] = []
    server_stopped = ThreadEvent()
    monkeypatch.setattr(instance.server, "stop", server_stopped.set)

    def abort() -> None:
        # Deterministically let normal completion win the race after control()
        # observes a live worker but before its cancellation can take effect.
        released.set()
        worker.join(5)
        assert not worker.is_alive()
        assert harness.ledger.get_run(engine.run_id).status == "finished"

    def on_started(run_id: str) -> None:
        instance.attach_active_run(
            worker=worker,
            abort=abort,
            run_id=run_id,
            state=lambda: rebuild_run_state(harness.ledger, run_id),
        )

    def execute() -> None:
        try:
            engine.run(directory, on_started=on_started)
        except BaseException as error:
            failures.append(error)

    worker = Thread(target=execute)
    worker.start()
    try:
        assert entered.wait(5)
        assert instance.control(Shutdown(kind="shutdown"))
        assert instance.stopped.is_set() and server_stopped.is_set()
        assert instance.phase == "finished"
        assert not failures
        finished = [
            event
            for event in harness.ledger.list_events(engine.run_id)
            if event.type == "run_finished"
        ]
        assert len(finished) == 1 and finished[0].status == "finished"
    finally:
        released.set()
        worker.join(5)
