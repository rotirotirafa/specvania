"""T14 HTTP/WebSocket contracts, entirely in-process under the socket guard."""

import logging
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient, WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from specvania.clock import FakeClock
from specvania.ledger import Ledger
from specvania.run.bus import EventBus, Subscription
from specvania.run.events import (
    Event,
    RedactionGuard,
    RoomEntered,
    RunStarted,
    SecretLeakError,
)
from specvania.run.jsonl import encode_events
from specvania.ui.launcher import open_browser
from specvania.ui.server import ApprovalAnswer, UIServer, UIServerError, load_replay

STATIC = Path(__file__).resolve().parents[1] / "src/specvania/ui/static"
ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def ui(tmp_path: Path) -> Iterator[UIServer]:
    guard = RedactionGuard()
    clock = FakeClock()
    with Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=clock, guard=guard
    ) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.add_feature(project.id, "map", "Map")
        version = ledger.add_spec_version(feature.id, "spec", "rooms")
        run = ledger.create_run(feature.id, version.id, "specvania/map")
        server = UIServer(
            guard=guard,
            clock=clock,
            run_id=run.id,
            ledger=ledger,
            bus=EventBus(guard),
            port=8765,
        )
        yield server


def emit(ui: UIServer, room: str | None = None) -> Event:
    assert ui.ledger is not None and ui.bus is not None
    event = (
        RoomEntered(ts=ui.clock.now(), run_id=ui.run_id, seq=0, room_id=room)
        if room is not None
        else RunStarted(
            ts=ui.clock.now(),
            run_id=ui.run_id,
            seq=0,
            feature="map",
            branch="specvania/map",
        )
    )
    assigned = ui.ledger.append_event(event)
    ui.bus.publish(assigned)
    return assigned


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/main.js",
        "/pixel/palette.js",
        "/vendor/phaser.min.js",
        "/docs",
        "/openapi.json",
    ],
)
@pytest.mark.parametrize("token", [None, "wrong"])
def test_http_requires_view_token(ui: UIServer, path: str, token: str | None) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        response = client.get(
            path, params={} if token is None else {"view_token": token}
        )
        assert response.status_code == 401
        assert ui.view_token not in response.text
        assert ui.control_token not in response.text


def test_websocket_requires_view_token(ui: UIServer) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        for suffix in ("", "?view_token=wrong"):
            with pytest.raises(WebSocketDenialResponse) as denied:
                with client.websocket_connect(
                    "ws://127.0.0.1:8765/ws" + suffix, headers={"Origin": ORIGIN}
                ):
                    pytest.fail("Unauthorized WebSocket accepted")
            assert denied.value.status_code == 401


@pytest.mark.parametrize(
    "origin",
    [
        "http://foreign.invalid",
        "null",
        "http://localhost:8765",
        "http://127.0.0.1:8766",
        None,
    ],
)
def test_websocket_refuses_foreign_origin(ui: UIServer, origin: str | None) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect(
                f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
                headers={} if origin is None else {"Origin": origin},
            ):
                pytest.fail("Foreign Origin accepted")
        assert refused.value.code == 1008


@pytest.mark.parametrize("token", [None, "wrong", "view"])
def test_approval_without_control_token_is_rejected_and_logged(
    ui: UIServer,
    token: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answers: list[ApprovalAnswer] = []
    ui.approval_responder = answers.append
    with (
        caplog.at_level(logging.WARNING),
        TestClient(ui.app, base_url=ORIGIN) as client,
    ):
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            assert ws.receive_json()["kind"] == "state"
            message = {
                "kind": "approval_answer",
                "request_id": "request-1",
                "answer": True,
            }
            if token is not None:
                message["control_token"] = ui.view_token if token == "view" else token
            ws.send_json(message)
            assert ws.receive_json()["kind"] == "approval_rejected"
            emit(ui)
            assert ws.receive_json()["event"]["type"] == "run_started"
    assert not answers
    assert "approval" in caplog.text.lower() and "rejected" in caplog.text.lower()
    assert ui.view_token not in caplog.text and ui.control_token not in caplog.text


def test_control_token_in_message_delivers_validated_answer(ui: UIServer) -> None:
    answers: list[ApprovalAnswer] = []
    ui.approval_responder = answers.append
    with TestClient(ui.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ws.send_json(
                {
                    "kind": "approval_answer",
                    "request_id": "request-1",
                    "answer": False,
                    "control_token": ui.control_token,
                }
            )
            # Rejection replies give a deterministic barrier without dropping
            # this watching connection after the answer has been handled.
            ws.send_json({"kind": "invalid"})
            assert ws.receive_json()["kind"] == "approval_rejected"
    assert len(answers) == 1
    assert answers[0].request_id == "request-1" and answers[0].answer is False
    assert ui.control_token not in repr(answers[0])


def test_tokens_are_random_distinct_and_guarded_at_bus_and_ledger(ui: UIServer) -> None:
    other = UIServer(
        guard=RedactionGuard(), clock=FakeClock(), run_id="other", replay=()
    )
    assert (
        len({ui.view_token, ui.control_token, other.view_token, other.control_token})
        == 4
    )
    assert len(ui.view_token) >= 32 and len(ui.control_token) >= 32
    assert ui.ledger is not None and ui.bus is not None
    for token in (ui.view_token, ui.control_token):
        event = RoomEntered(ts=ui.clock.now(), run_id=ui.run_id, seq=1, room_id=token)
        with pytest.raises(SecretLeakError):
            ui.ledger.append_event(event)
        with pytest.raises(SecretLeakError):
            ui.bus.publish(event)


def test_mid_run_state_then_live_events_without_gaps_or_duplicates(
    ui: UIServer,
) -> None:
    emit(ui)
    emit(ui, "T01")
    with TestClient(ui.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            first = ws.receive_json()
            assert first["kind"] == "state"
            assert first["state"]["seq"] == 2
            assert first["state"]["active_room"] == "T01"
            with ThreadPoolExecutor(max_workers=1) as pool:
                for room in ("T02", "T03", "T04"):
                    pool.submit(emit, ui, room).result()
            messages = [ws.receive_json() for _ in range(3)]
            assert [m["kind"] for m in messages] == ["event"] * 3
            assert [m["event"]["seq"] for m in messages] == [3, 4, 5]
            assert [m["event"]["room_id"] for m in messages] == ["T02", "T03", "T04"]


def test_connect_race_deduplicates_snapshot_and_queued_events(
    ui: UIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emit(ui)
    assert ui.ledger is not None
    original = ui.ledger.list_events
    barrier = Barrier(2)

    def racing_snapshot(run_id: str) -> list[Event]:
        barrier.wait(timeout=5)
        barrier.wait(timeout=5)
        return original(run_id)

    monkeypatch.setattr(ui.ledger, "list_events", racing_snapshot)

    def during_snapshot() -> None:
        barrier.wait(timeout=5)
        emit(ui, "T01")
        barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher = pool.submit(during_snapshot)
        with TestClient(ui.app, base_url=ORIGIN) as client:
            with client.websocket_connect(
                f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
                headers={"Origin": ORIGIN},
            ) as ws:
                assert ws.receive_json()["state"]["seq"] == 2
                publisher.result()
                emit(ui, "T02")
                assert ws.receive_json()["event"]["seq"] == 3


def replay_stream(clock: FakeClock) -> tuple[Event, ...]:
    events: list[Event] = [
        RunStarted(
            ts=clock.now(),
            run_id="replay",
            seq=1,
            feature="map",
            branch="specvania/map",
        )
    ]
    for seq, gap in ((2, 1), (3, 2), (4, 20)):
        clock.sleep(gap)
        events.append(
            RoomEntered(ts=clock.now(), run_id="replay", seq=seq, room_id=f"T{seq:02}")
        )
    return tuple(events)


def test_replay_speed_four_is_four_times_faster_with_capped_gaps() -> None:
    events = replay_stream(FakeClock())
    elapsed: list[float] = []
    for speed in (1, 4):
        clock = FakeClock()
        server = UIServer(
            guard=RedactionGuard(),
            clock=clock,
            run_id="replay",
            replay=events,
            speed=speed,
            port=8765,
        )
        start = clock.now()
        with TestClient(server.app, base_url=ORIGIN) as client:
            with client.websocket_connect(
                f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
                headers={"Origin": ORIGIN},
            ) as ws:
                assert ws.receive_json()["state"]["seq"] == 0
                messages = [ws.receive_json() for _ in events]
                assert [m["event"] for m in messages] == [
                    e.model_dump(mode="json") for e in events
                ]
        elapsed.append((clock.now() - start).total_seconds())
    assert elapsed == [5, 1.25]


def test_replay_loads_identical_events_from_ledger_and_jsonl(
    ui: UIServer, tmp_path: Path
) -> None:
    events = (emit(ui), emit(ui, "T01"))
    assert ui.ledger is not None
    guard = RedactionGuard()
    path = tmp_path / "events.jsonl"
    path.write_text(encode_events(events, guard))
    assert load_replay(path, guard=guard) == events
    assert load_replay(ui.run_id, guard=guard, ledger=ui.ledger) == events


def test_bad_replay_sources_raise_typed_errors(tmp_path: Path) -> None:
    with pytest.raises(UIServerError):
        load_replay(tmp_path / "missing.jsonl", guard=RedactionGuard())
    with pytest.raises(UIServerError):
        load_replay("unknown-run", guard=RedactionGuard())


def test_bus_only_snapshot_overlap_delivers_live_events_without_duplicates() -> None:
    guard = RedactionGuard()
    clock = FakeClock()
    bus = EventBus(guard)
    events: list[Event] = [
        RunStarted(ts=clock.now(), run_id="plan", seq=1, feature="map", branch="")
    ]
    bus.publish(events[0])

    def snapshot() -> tuple[Event, ...]:
        # Emitted after subscription but before snapshot completion: seq 2 must
        # appear in the state, and its queued duplicate must be discarded.
        event = RoomEntered(ts=clock.now(), run_id="plan", seq=2, room_id="T01")
        events.append(event)
        bus.publish(event)
        return tuple(events)

    server = UIServer(
        guard=guard,
        clock=clock,
        run_id="plan",
        bus=bus,
        live_snapshot=snapshot,
        port=8765,
    )
    with TestClient(server.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"Origin": ORIGIN},
        ) as websocket:
            state = websocket.receive_json()["state"]
            assert state["run_id"] == "plan" and state["seq"] == 2
            assert state["active_room"] == "T01"
            event = RoomEntered(ts=clock.now(), run_id="plan", seq=3, room_id="T02")
            bus.publish(event)
            message = websocket.receive_json()
            assert message["kind"] == "event" and message["event"]["seq"] == 3
            assert message["event"]["room_id"] == "T02"


def test_static_files_are_local_and_all_served_with_view_token(ui: UIServer) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        for path in sorted(STATIC.rglob("*")):
            if not path.is_file():
                continue
            route = "/" + path.relative_to(STATIC).as_posix()
            response = client.get(route, params={"view_token": ui.view_token})
            assert response.status_code == 200, route
            assert response.content == path.read_bytes()
            assert response.headers["referrer-policy"] == "no-referrer"
            policy = response.headers["content-security-policy"]
            assert "'unsafe-eval'" not in policy
            assert "http:" not in policy and "https:" not in policy
            assert "worker-src 'self'" in policy
            assert "font-src 'self'" in policy
            # Upstream comments/license strings contain URLs, not requests.
            if path.suffix in (".html", ".js"):
                source = path.read_text()
                assert not re.search(
                    r"(?:src|href)\s*=\s*[\"\'](?:https?:)?//|"
                    r"(?:fetch|importScripts)\s*\(\s*[\"\'](?:https?:)?//|"
                    r"(?:from|import)\s+[\"\'](?:https?:)?//|"
                    r"url\(\s*[\"\']?(?:https?:)?//",
                    source,
                ), route
        page = client.get("/", params={"view_token": ui.view_token})
        assert page.status_code == 200 and "main.js" in page.text
        assert ui.view_token not in page.text and ui.control_token not in page.text


@pytest.mark.parametrize("path", ["/", "/map/scene.js", "/vendor/phaser.min.js"])
def test_served_csp_allows_phaser_images_and_keeps_other_sources_local(
    ui: UIServer, path: str
) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        response = client.get(path, params={"view_token": ui.view_token})
        assert response.status_code == 200
        directives = [
            directive.split()
            for directive in response.headers["content-security-policy"].split(";")
            if directive.strip()
        ]
        policy = {name: sources for name, *sources in directives}
        assert len(policy) == len(directives), "CSP directives must not be duplicated"
        # Phaser boots with data PNGs and may use blob images for local textures.
        # Exact source lists also reject wildcard or remote-origin regressions.
        assert policy == {
            "default-src": ["'self'"],
            "img-src": ["'self'", "data:", "blob:"],
            "script-src": ["'self'", "'unsafe-inline'"],
            "style-src": ["'self'", "'unsafe-inline'"],
            "font-src": ["'self'"],
            "worker-src": ["'self'"],
            "base-uri": ["'none'"],
            "object-src": ["'none'"],
            "frame-ancestors": ["'none'"],
            "connect-src": ["'self'", f"ws://127.0.0.1:{ui.port}"],
        }


def test_browser_launcher_opens_only_loopback_with_view_token(
    ui: UIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("specvania.ui.launcher.webbrowser.open_new_tab", opened.append)
    open_browser(ui.port, ui.view_token)
    parsed = urlsplit(opened[0])
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    assert parsed.port == 8765
    assert parse_qs(parsed.query) == {"view_token": [ui.view_token]}
    assert ui.control_token not in opened[0]


def test_view_cookie_loads_relative_assets_but_never_grants_control(
    ui: UIServer,
) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        response = client.get("/", params={"view_token": ui.view_token})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert ui.control_token not in cookie
        for route in (
            "/main.js",
            "/pixel/palette.js",
            "/vendor/fonts/PressStart2P-Regular.ttf",
        ):
            assert client.get(route).status_code == 200
        assert client.get("/main.js?view_token=wrong").status_code == 401
        # Cookie-only WebSocket access is deliberately refused.
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                "ws://127.0.0.1:8765/ws", headers={"Origin": ORIGIN}
            ):
                pytest.fail("WebSocket accepted without an explicit view token")
        assert denied.value.status_code == 401


@pytest.mark.parametrize(
    "message",
    [
        {"kind": "approval_answer", "request_id": "request-1", "answer": "yes"},
        {
            "kind": "approval_answer",
            "request_id": "request-1",
            "answer": True,
            "extra": True,
        },
        {"kind": "unknown"},
    ],
)
def test_invalid_approval_messages_do_not_reach_responder(
    ui: UIServer,
    message: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    answers: list[ApprovalAnswer] = []
    ui.approval_responder = answers.append
    with TestClient(ui.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ws.send_json(message | {"control_token": ui.control_token})
            assert ws.receive_json()["kind"] == "approval_rejected"
            emit(ui)
            assert ws.receive_json()["event"]["type"] == "run_started"
    assert not answers
    assert "rejected" in caplog.text
    assert ui.view_token not in caplog.text and ui.control_token not in caplog.text


def test_control_token_in_url_cannot_authorize_approval(ui: UIServer) -> None:
    answers: list[ApprovalAnswer] = []
    ui.approval_responder = answers.append
    with TestClient(ui.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}&control_token={ui.control_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ws.send_json(
                {"kind": "approval_answer", "request_id": "request-1", "answer": True}
            )
            assert ws.receive_json()["kind"] == "approval_rejected"
            emit(ui)
            assert ws.receive_json()["event"]["type"] == "run_started"
    assert not answers


def test_live_clients_get_independent_streams_and_cleanup_subscription(
    ui: UIServer,
) -> None:
    from queue import Empty

    emit(ui)
    assert ui.bus is not None
    original = ui.bus.subscribe
    subscriptions: list[Subscription] = []

    def track(run_id: str | None = None) -> Subscription:
        subscription = original(run_id)
        subscriptions.append(subscription)
        return subscription

    # Capture subscriptions through the public port to observe their delivery
    # after disconnect; this does not inspect EventBus internals.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ui.bus, "subscribe", track)
        with TestClient(ui.app, base_url=ORIGIN) as client:
            url = f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}"
            with client.websocket_connect(url, headers={"Origin": ORIGIN}) as first:
                assert first.receive_json()["state"]["seq"] == 1
                with client.websocket_connect(
                    url, headers={"Origin": ORIGIN}
                ) as second:
                    assert second.receive_json()["state"]["seq"] == 1
                    emit(ui, "T01")
                    assert first.receive_json()["event"]["seq"] == 2
                    assert second.receive_json()["event"]["seq"] == 2
                emit(ui, "T02")
                assert first.receive_json()["event"]["seq"] == 3
        emit(ui, "T03")
        assert len(subscriptions) == 2
        for subscription in subscriptions:
            with pytest.raises(Empty):
                subscription.get(timeout=0)


def test_live_stream_does_not_send_other_runs(ui: UIServer) -> None:
    assert ui.bus is not None
    with TestClient(ui.app, base_url=ORIGIN) as client:
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={ui.view_token}",
            headers={"Origin": ORIGIN},
        ) as ws:
            ws.receive_json()
            ui.bus.publish(
                RoomEntered(ts=ui.clock.now(), run_id="another", seq=1, room_id="T99")
            )
            emit(ui)
            message = ws.receive_json()
            assert message["event"]["run_id"] == ui.run_id
            assert message["event"]["seq"] == 1


def test_foreign_host_cannot_use_valid_view_token(ui: UIServer) -> None:
    with TestClient(ui.app, base_url="http://foreign.invalid:8765") as client:
        assert client.get("/", params={"view_token": ui.view_token}).status_code == 403


def test_serve_binds_loopback_and_launches_browser_after_startup(
    ui: UIServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import Self

    import uvicorn

    import specvania.ui.server as server_module

    bindings: list[tuple[str, int]] = []
    opened: list[str] = []
    configs: list[uvicorn.Config] = []

    class Listener:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def bind(self, address: tuple[str, int]) -> None:
            bindings.append(address)

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", 43210)

    listener = Listener()
    monkeypatch.setattr(
        server_module,
        "socket",
        SimpleNamespace(
            AF_INET=2,
            SOCK_STREAM=1,
            socket=lambda *args: listener,
        ),
    )
    monkeypatch.setattr("specvania.ui.launcher.webbrowser.open_new_tab", opened.append)

    async def startup(server: uvicorn.Server, sockets: object = None) -> None:
        assert not opened
        assert sockets == [listener]
        server.started = True

    def run(server: uvicorn.Server, sockets: object = None) -> None:
        configs.append(server.config)
        asyncio.run(server.startup(sockets=sockets))  # type: ignore[arg-type]

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr(uvicorn.Server, "run", run)
    ui.port = 0
    ui.serve(browser=True)
    assert bindings == [("127.0.0.1", 0)]
    assert configs[0].host == "127.0.0.1" and configs[0].port == 43210
    assert configs[0].access_log is False
    assert configs[0].log_level == "warning"
    assert opened == [f"http://127.0.0.1:43210/?view_token={ui.view_token}"]
    assert ui.control_token not in opened[0]


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/sprites.html",
        "/vendor/monaco/vs/loader.js",
        "/vendor/monaco/worker.js",
        "/missing.html",
    ],
)
def test_csp_on_html_static_missing_and_denied_responses(
    ui: UIServer, path: str
) -> None:
    with TestClient(ui.app, base_url=ORIGIN) as client:
        for token in ("wrong", ui.view_token):
            response = client.get(path, params={"view_token": token})
            policy = response.headers["content-security-policy"]
            assert "'unsafe-eval'" not in policy
            assert "https:" not in policy and "http:" not in policy
            assert "font-src 'self'" in policy and "worker-src 'self'" in policy
            assert "base-uri 'none'" in policy
            assert "ws://127.0.0.1:8765" in policy
            assert "blob:" not in policy.split("worker-src", 1)[1].split(";", 1)[0]


def test_room_panel_restores_world_checklist_review_and_gates_on_reopen(
    tmp_path: Path,
) -> None:
    from test_gitops import initialize_repo
    from test_room_code import server

    from specvania.run.events import ChecklistResult, GateResult, Review

    initialize_repo(tmp_path)
    ui = server(tmp_path, active=True)
    assert ui.live_snapshot is not None
    events = list(ui.live_snapshot())
    fields = {"ts": ui.clock.now(), "run_id": ui.run_id, "room_id": "T01"}
    events += [
        GateResult(
            **fields, seq=4, command="pytest", exit_code=1, output_summary="failed"
        ),
        Review(
            **fields,
            seq=5,
            verdict="fail",
            feedback="Fix the room",
            checklist=(ChecklistResult(item="Read files", ok=False, note="Missing"),),
        ),
    ]
    ui.live_snapshot = lambda: tuple(events)
    with TestClient(ui.app, base_url=ORIGIN) as client:
        for _ in range(2):
            result = client.get(
                "/api/rooms/T01/code", params={"view_token": ui.view_token}
            ).json()
            assert result["checklist"] == [
                {"item": "Read files", "ok": False, "note": "Missing"}
            ]
            assert result["review"]["feedback"] == "Fix the room"
            assert result["gates"][0]["exit_code"] == 1


def test_studio_room_panel_collects_reviews_before_viewer_connects_and_resets_attempt(
    tmp_path: Path,
) -> None:
    from test_gitops import initialize_repo
    from test_room_code import server

    from specvania.run.events import AttemptStarted, ChecklistResult, GateResult, Review
    from specvania.run.state import fold_events
    from specvania.ui.server import StateMessage

    initialize_repo(tmp_path)
    original = server(tmp_path, active=True)
    assert original.live_snapshot is not None
    events = list(original.live_snapshot())
    guard = RedactionGuard()
    bus = EventBus(guard)
    ui = UIServer(
        guard=guard,
        clock=original.clock,
        run_id="code",
        bus=bus,
        studio_snapshot=lambda: StateMessage(state=fold_events(events)),
        repository_root=tmp_path,
        world_directory=tmp_path / "world",
        port=8765,
    )
    fields = {"ts": ui.clock.now(), "run_id": "code", "room_id": "T01"}
    for event in (
        GateResult(
            **fields, seq=4, command="node", exit_code=0, output_summary="passed"
        ),
        Review(
            **fields,
            seq=5,
            verdict="pass",
            feedback="Covered",
            checklist=(ChecklistResult(item="Read files", ok=True, note="Verified"),),
        ),
    ):
        events.append(event)
        bus.publish(event)
    with TestClient(ui.app, base_url=ORIGIN) as client:
        result = client.get(
            "/api/rooms/T01/code", params={"view_token": ui.view_token}
        ).json()
        assert result["review"]["verdict"] == "pass"
        assert result["gates"][0]["command"] == "node"
        event = AttemptStarted(**fields, seq=6, attempt=2)
        events.append(event)
        bus.publish(event)
        result = client.get(
            "/api/rooms/T01/code", params={"view_token": ui.view_token}
        ).json()
        assert result["review"] is None and result["gates"] == []
        assert result["checklist"][0]["ok"] is None
    ui.stop()


def test_room_panel_metadata_preserves_long_items_and_shares_the_response_cap() -> None:
    from specvania.ui.server import RoomChecklist, RoomCode, _cap_room_code

    item = RoomChecklist(item="Checklist " + "x" * 1000)
    response = RoomCode(room_id="T01", kind="files", checklist=(item,))
    assert _cap_room_code(response, iter(())).checklist[0].item == item.item
    response = response.model_copy(update={"checklist": (item,) * 1200})
    capped = _cap_room_code(response, iter(()))
    assert len(capped.model_dump_json().encode("utf-8")) <= 1_000_000
    assert capped.truncated and capped.reason and "truncated" in capped.reason
