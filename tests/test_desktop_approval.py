"""T20 real broker, tools and in-process UI transport contracts."""

import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event as ThreadEvent

import pytest
from starlette.testclient import TestClient

from specvania.agent.approval import ApprovalBroker, TerminalResponder
from specvania.agent.sandbox import Sandbox
from specvania.agent.tools import Tools
from specvania.clock import FakeClock
from specvania.ledger import Ledger
from specvania.run.bus import EventBus
from specvania.run.events import (
    ApprovalRequested,
    Event,
    RedactionGuard,
)
from specvania.ui.server import UIServer

ROOT = Path(__file__).resolve().parents[1]


class CooperativeClock(FakeClock):
    """Virtual time with a scheduler yield for the real shell/UI worker threads."""

    def __init__(self) -> None:
        super().__init__()
        self._yield = ThreadEvent()

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self._yield.wait(timeout=0.001)


@pytest.fixture
def desktop_ui(tmp_path: Path) -> Iterator[UIServer]:
    guard = RedactionGuard()
    clock = CooperativeClock()
    with Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=clock, guard=guard
    ) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.add_feature(project.id, "desktop", "Desktop")
        version = ledger.add_spec_version(feature.id, "spec", "rooms")
        run = ledger.create_run(feature.id, version.id, "specvania/desktop")
        yield UIServer(
            guard=guard,
            clock=clock,
            run_id=run.id,
            ledger=ledger,
            bus=EventBus(guard),
            port=8765,
        )


def publish(ui: UIServer, event: Event) -> None:
    assert ui.ledger is not None and ui.bus is not None
    ui.bus.publish(ui.ledger.append_event(event))


@pytest.mark.parametrize("allowed, word", [(True, "approved"), (False, "denied")])
def test_desktop_answer_runs_or_denies_command_and_closes_terminal(
    desktop_ui: UIServer,
    tmp_path: Path,
    allowed: bool,
    word: str,
) -> None:
    ui = desktop_ui
    reader, writer = os.pipe()
    messages: list[str] = []
    try:
        broker = ApprovalBroker(
            [TerminalResponder(reader, messages.append), ui.desktop_responder],
            clock=ui.clock,
            emit=lambda event: publish(ui, event),
            run_id=ui.run_id,
        )
        tools = Tools(
            Sandbox(tmp_path),
            broker=broker,
            clock=ui.clock,
            emit=lambda event: publish(ui, event),
            run_id=ui.run_id,
            room_id="T20",
            observation_kb=8,
            shell_timeout=5,
        )
        with TestClient(ui.app, base_url=ui.origin) as client:
            with client.websocket_connect(
                f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
                headers={"Origin": ui.origin},
            ) as ws:
                ws.receive_json()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    running = pool.submit(
                        tools.run_shell, "printf allowed > approved.txt"
                    )
                    requested = ws.receive_json()["event"]
                    assert requested["command"] == "printf allowed > approved.txt"
                    assert requested["cwd"] == str(tmp_path)
                    ws.send_json(
                        {
                            "kind": "approval_answer",
                            "request_id": requested["request_id"],
                            "answer": allowed,
                            "control_token": ui.control_token,
                        }
                    )
                    answered = ws.receive_json()["event"]
                    result = running.result(timeout=5)
                assert answered["responder"] == "desktop window"
                assert answered["answer"] is allowed
                assert messages[-1] == f"{word} in desktop window"
                assert os.get_blocking(reader)
                assert (tmp_path / "approved.txt").exists() is allowed
                assert result.exit_code == (0 if allowed else None)
                if not allowed:
                    assert result.output == "denied by user"
                assert ui.ledger is not None
                assert ui.control_token not in repr(ui.ledger.list_events(ui.run_id))
    finally:
        os.close(reader)
        os.close(writer)


def test_terminal_first_rejects_late_desktop_and_next_request_is_independent(
    desktop_ui: UIServer,
    tmp_path: Path,
) -> None:
    ui = desktop_ui
    reader, writer = os.pipe()
    try:
        broker = ApprovalBroker(
            [TerminalResponder(reader, lambda _: None), ui.desktop_responder],
            clock=ui.clock,
            emit=lambda event: publish(ui, event),
            run_id=ui.run_id,
        )
        with TestClient(ui.app, base_url=ui.origin) as client:
            with client.websocket_connect(
                f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
                headers={"Origin": ui.origin},
            ) as ws:
                ws.receive_json()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    first = pool.submit(broker.request, "first", tmp_path)
                    request_id = ws.receive_json()["event"]["request_id"]
                    os.write(writer, b"n\n")
                    answered = ws.receive_json()["event"]
                    assert first.result(timeout=5).responder == "terminal"
                    assert answered["answer"] is False
                    ws.send_json(
                        {
                            "kind": "approval_answer",
                            "request_id": request_id,
                            "answer": True,
                            "control_token": ui.control_token,
                        }
                    )
                    assert ws.receive_json()["kind"] == "approval_rejected"
                    second = pool.submit(broker.request, "second", tmp_path)
                    next_id = ws.receive_json()["event"]["request_id"]
                    assert next_id != request_id
                    ws.send_json(
                        {
                            "kind": "approval_answer",
                            "request_id": next_id,
                            "answer": True,
                            "control_token": ui.control_token,
                        }
                    )
                    assert ws.receive_json()["event"]["responder"] == "desktop window"
                    assert second.result(timeout=5).answer is True
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize(
    "message",
    [
        {"kind": "approval_answer", "request_id": "pending", "answer": True},
        {
            "kind": "approval_answer",
            "request_id": "pending",
            "answer": True,
            "control_token": "wrong",
        },
        {"kind": "approval_answer", "request_id": "pending", "answer": "yes"},
        {"kind": "unknown"},
    ],
)
def test_rejected_answer_keeps_browser_watching_and_logs_no_secrets(
    desktop_ui: UIServer,
    message: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    ui = desktop_ui
    with (
        caplog.at_level(logging.WARNING),
        TestClient(ui.app, base_url=ui.origin) as client,
    ):
        with client.websocket_connect(
            f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
            headers={"Origin": ui.origin},
        ) as ws:
            ws.receive_json()
            ws.send_json(message)
            assert ws.receive_json()["kind"] == "approval_rejected"
            publish(
                ui,
                ApprovalRequested(
                    ts=ui.clock.now(),
                    run_id=ui.run_id,
                    seq=0,
                    request_id="pending",
                    command="pytest -q",
                    cwd=".",
                ),
            )
            assert ws.receive_json()["event"]["type"] == "approval_requested"
    assert "rejected" in caplog.text
    assert ui.view_token not in caplog.text and ui.control_token not in caplog.text


def test_view_cookie_is_port_specific_and_retains_strict_security(
    desktop_ui: UIServer,
) -> None:
    ui = desktop_ui
    other = UIServer(
        guard=RedactionGuard(), clock=FakeClock(), run_id="other", replay=(), port=8766
    )
    with TestClient(ui.app, base_url=ui.origin) as client:
        response = client.get("/", params={"view_token": ui.view_token})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("specvania_view_8765=")
        assert (
            "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
        )
        assert ui.control_token not in cookie
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.get("/pixel/palette.js").status_code == 200
        with TestClient(other.app, base_url=other.origin) as other_client:
            # Cookies ignore ports; even the other token must not authenticate
            # under this server's cookie name or the old shared cookie name.
            for name in ("specvania_view_8765", "specvania_view"):
                other_client.cookies.set(name, other.view_token)
            assert other_client.get("/").status_code == 401
            other_client.cookies.set("specvania_view_8766", other.view_token)
            assert other_client.get("/").status_code == 200
    ui.port = (
        43210  # OS-assigned ports must use the final port, not constructor port 0.
    )
    with TestClient(ui.app, base_url=ui.origin) as client:
        assert (
            client.get("/", params={"view_token": ui.view_token})
            .headers["set-cookie"]
            .startswith("specvania_view_43210=")
        )


def test_immediate_desktop_answer_at_publication_is_not_lost(tmp_path: Path) -> None:
    ui = UIServer(guard=RedactionGuard(), clock=FakeClock(), run_id="run", replay=())

    def emit(event: Event) -> None:
        if isinstance(event, ApprovalRequested):
            assert ui.desktop_responder.answer(event.request_id, True)

    broker = ApprovalBroker(
        [ui.desktop_responder], clock=FakeClock(), emit=emit, run_id="run"
    )
    assert broker.request("printf ok", tmp_path).answer is True


def run_web_check(name: str) -> None:
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [
            node,
            "--test",
            f"--test-name-pattern={name}",
            str(ROOT / "tests/desktop/approval.test.cjs"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_rpg_choice_box_sends_control_token_and_command_details() -> None:
    run_web_check("rpg_choice")


def test_terminal_answer_closes_desktop_choice_box() -> None:
    run_web_check("terminal_answer")


def test_browser_request_is_read_only_without_control_token() -> None:
    run_web_check("browser_read_only")


def test_notification_event_filter_and_unfocused_main_process() -> None:
    run_web_check("notification")


def test_pending_approval_reconnect_shows_full_command_without_expanding_events(
    desktop_ui: UIServer,
    tmp_path: Path,
) -> None:
    ui = desktop_ui
    broker = ApprovalBroker(
        [ui.desktop_responder],
        clock=ui.clock,
        emit=lambda event: publish(ui, event),
        run_id=ui.run_id,
    )
    command = "printf " + "x" * 500
    assert ui.bus is not None
    subscription = ui.bus.subscribe(ui.run_id)
    pool = ThreadPoolExecutor(max_workers=1)
    request_id = ""
    try:
        pending = pool.submit(broker.request, command, tmp_path)
        requested = subscription.get(timeout=5)
        assert isinstance(requested, ApprovalRequested)
        request_id = requested.request_id
        assert len(requested.command) == 200
        with TestClient(ui.app, base_url=ui.origin) as client:
            with client.websocket_connect(
                f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
                headers={"Origin": ui.origin},
            ) as ws:
                state = ws.receive_json()
                assert state["approval_request"]["command"] == command
                assert state["approval_request"]["cwd"] == str(tmp_path)
                assert ui.control_token not in repr(state)
                ws.send_json(
                    {
                        "kind": "approval_answer",
                        "request_id": request_id,
                        "answer": False,
                        "control_token": ui.control_token,
                    }
                )
                assert ws.receive_json()["event"]["type"] == "approval_answered"
            assert pending.result(timeout=5).answer is False
            with client.websocket_connect(
                f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
                headers={"Origin": ui.origin},
            ) as ws:
                assert ws.receive_json()["approval_request"] is None
    finally:
        ui.desktop_responder.answer(request_id, False)
        pool.shutdown(wait=True)
        subscription.close()


def test_full_transient_prompt_is_checked_for_secrets_beyond_event_summary(
    desktop_ui: UIServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from starlette.websockets import WebSocketDisconnect

    from specvania.agent.approval import ApprovalRequest

    ui = desktop_ui
    command = "printf " + "x" * 250 + ui.control_token
    ui.desktop_responder.start(
        ApprovalRequest(request_id="secret-tail", command=command, cwd="."),
        lambda _: None,
    )
    with TestClient(ui.app, base_url=ui.origin) as client:
        with client.websocket_connect(
            f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
            headers={"Origin": ui.origin},
        ) as ws:
            ws.receive_json()
            publish(
                ui,
                ApprovalRequested(
                    ts=ui.clock.now(),
                    run_id=ui.run_id,
                    seq=0,
                    request_id="secret-tail",
                    command=command[:200],
                    cwd=".",
                ),
            )
            with pytest.raises(WebSocketDisconnect) as rejected:
                ws.receive_json()
            assert rejected.value.code == 1011
    ui.desktop_responder.cancel("secret-tail", None)
    assert ui.control_token not in caplog.text


def test_replay_rejects_even_valid_control_and_keeps_watching() -> None:
    ui = UIServer(
        guard=RedactionGuard(),
        clock=FakeClock(),
        run_id="preview",
        replay=(),
        port=8765,
    )
    answers = []
    ui.approval_responder = answers.append
    with TestClient(ui.app, base_url=ui.origin) as client:
        with client.websocket_connect(
            f"{ui.origin.replace('http:', 'ws:')}/ws?view_token={ui.view_token}",
            headers={"Origin": ui.origin},
        ) as ws:
            ws.receive_json()
            for _ in range(2):
                ws.send_json(
                    {
                        "kind": "approval_answer",
                        "request_id": "preview",
                        "answer": True,
                        "control_token": ui.control_token,
                    }
                )
                assert ws.receive_json() == {
                    "kind": "approval_rejected",
                    "reason": "control unavailable",
                }
    assert not answers
