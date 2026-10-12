"""T18 portable replay, synthetic catalog and offline CLI evidence."""

import asyncio
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest
import uvicorn
from starlette.testclient import TestClient
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.ledger import Ledger, LedgerError
from specvania.run.events import Event, RedactionGuard
from specvania.run.jsonl import decode_events, encode_events
from specvania.run.state import RoomState, fold_events
from specvania.ui import launcher
from specvania.ui import server as ui_server
from specvania.ui.launcher import LaunchResult, UIMode
from specvania.ui.server import UIServer


def demo_events() -> tuple[Event, ...]:
    return decode_events(
        files("specvania").joinpath("examples/demo/events.jsonl").read_text(),
        RedactionGuard(),
    )


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[UIServer]:
    servers: list[UIServer] = []

    def serve(
        server: UIServer,
        *,
        browser: bool = False,
        on_started: Callable[[], None] | None = None,
    ) -> None:
        assert not browser, "Replay must use the shared launcher"
        assert on_started is not None
        server.port = 8765
        servers.append(server)
        on_started()

    def open_ui(
        port: int,
        view_token: str,
        control_token: str,
        *,
        mode: UIMode = "auto",
        desktop_path: str | None = None,
    ) -> LaunchResult:
        server = servers[-1]
        assert (port, view_token, control_token) == (
            server.port,
            server.view_token,
            server.control_token,
        )
        assert mode == "auto" and desktop_path is None
        return LaunchResult(mode="browser")

    def no_browser(*args: object, **kwargs: object) -> None:
        pytest.fail("A test must not open a browser")

    monkeypatch.setattr(UIServer, "serve", serve)
    monkeypatch.setattr(ui_server, "open_browser", no_browser)
    monkeypatch.setattr(cli, "open_ui", open_ui)
    monkeypatch.setattr(cli, "SystemClock", FakeClock)
    return servers


def forbid_config_and_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Portable replay must not load config or open the ledger")

    monkeypatch.setattr(cli, "load_config", forbidden)
    monkeypatch.setattr(Ledger, "from_config", forbidden)
    monkeypatch.setattr(Ledger, "open", forbidden)


def test_demo_works_offline_without_config_key_ledger_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: list[UIServer]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    forbid_config_and_ledger(monkeypatch)
    result = CliRunner().invoke(cli.create_app(), ["replay", "demo"])
    assert result.exit_code == 0, result.output
    assert list(tmp_path.iterdir()) == []
    assert len(served) == 1
    server = served[0]
    assert server.ledger is None and server.bus is None
    assert server.replay is not None
    assert server.replay == demo_events()
    assert server.speed == 1
    # Exercise the real replay transport in-process, under the socket guard.
    server.port = 8765
    with TestClient(server.app, base_url=server.origin) as client:
        assert client.get(f"/?view_token={server.view_token}").status_code == 200
        with client.websocket_connect(
            f"ws://127.0.0.1:8765/ws?view_token={server.view_token}",
            headers={"origin": server.origin},
        ) as socket:
            assert socket.receive_json()["kind"] == "state"
            received = [socket.receive_json() for _ in server.replay]
    assert received == [
        {"kind": "event", "event": event.model_dump(mode="json")}
        for event in server.replay
    ]
    assert list(tmp_path.iterdir()) == []


def test_demo_validates_folds_and_exercises_every_required_event_and_room_state() -> (
    None
):
    events = demo_events()
    assert events
    assert encode_events(events, RedactionGuard())
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert all(a.ts < b.ts for a, b in zip(events, events[1:], strict=False))
    gaps = [
        (b.ts - a.ts).total_seconds() for a, b in zip(events, events[1:], strict=False)
    ]
    assert min(gaps) >= 1 and max(gaps) >= 5
    assert {
        "cooldown",
        "fallback",
        "respawn",
        "boss_started",
        "boss_scenario",
        "boss_result",
        "bug_opened",
        "bug_closed",
    } <= {e.type for e in events}
    world = next(event for event in events if event.type == "world_loaded")
    assert 2 <= len(world.areas) <= 3
    room_ids = {room.id for room in world.rooms}
    assert world.doors and world.boss_ids
    assert {(door.src, door.dst) for door in world.doors} == {
        (source, room.id) for room in world.rooms for source in room.depends_on
    }
    assert all(room.area in {area.id for area in world.areas} for room in world.rooms)
    states: set[RoomState] = set()
    for index, event in enumerate(events, 1):
        state = fold_events(events[:index])
        states.update(room.state for room in state.rooms)
        room_id = getattr(event, "room_id", None)
        if room_id is not None:
            assert room_id in room_ids
        if event.type == "boss_started":
            assert event.scenarios == next(
                area.boss_ids for area in world.areas if area.id == event.area_id
            )
        if event.type == "bug_opened":
            assert any(bug.status == "open" and bug.room_id for bug in state.bugs)
        if event.type == "bug_closed":
            assert any(bug.status == event.status for bug in state.bugs)
    assert states == set(RoomState)
    final = fold_events(events)
    assert final.status == "aborted"  # Skipped rooms keep their dependents locked.
    assert any(boss.passed for boss in final.bosses)
    assert any(room.attempt >= 2 for room in final.rooms)


def test_demo_readme_clearly_labels_synthetic_data() -> None:
    readme = files("specvania").joinpath("examples/demo/README.md").read_text()
    assert "synthetic" in readme.lower()
    assert "hand-built" in readme.lower()


@pytest.mark.parametrize("speed", ["1", "2", "4"])
def test_replay_accepts_only_supported_speeds(
    speed: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: list[UIServer]
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.create_app(), ["replay", "demo", "--speed", speed])
    assert result.exit_code == 0, result.output
    assert served[0].speed == int(speed)


@pytest.mark.parametrize("speed", ["0", "3", "5", "-1", "1.0", "fast"])
def test_replay_rejects_other_speeds_with_usage_error(
    speed: str, monkeypatch: pytest.MonkeyPatch, served: list[UIServer]
) -> None:
    forbid_config_and_ledger(monkeypatch)
    result = CliRunner().invoke(
        cli.create_app(), ["replay", "demo", f"--speed={speed}"]
    )
    assert result.exit_code == 2
    assert "Usage:" in result.output and "1" in result.output
    assert "2" in result.output and "4" in result.output
    assert not served


def test_jsonl_path_replays_without_config_or_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: list[UIServer]
) -> None:
    source = tmp_path / "portable events.jsonl"
    source.write_text(encode_events(demo_events(), RedactionGuard()))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    forbid_config_and_ledger(monkeypatch)
    result = CliRunner().invoke(
        cli.create_app(), ["replay", str(source), "--speed", "2"]
    )
    assert result.exit_code == 0, result.output
    assert served[0].replay == demo_events()
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("mode", ["auto", "browser", "desktop"])
@pytest.mark.parametrize("installed", [False, True])
def test_run_id_reads_events_through_configured_ledger(
    mode: UIMode,
    installed: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    served: list[UIServer],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("SPECVANIA_DATABASE_URL", raising=False)
    config = tmp_path / ".specvania/config.toml"
    config.parent.mkdir()
    config.write_text(f'[ui]\nmode = "{mode}"\n[desktop]\npath = "configured-app"\n')
    launched: list[UIMode] = []
    commands: list[list[str]] = []
    urls: list[str] = []
    monkeypatch.setattr(
        launcher,
        "find_desktop",
        lambda _: Path("configured-app") if installed else None,
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", commands.append)
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", urls.append)

    def open_ui(
        port: int,
        view_token: str,
        control_token: str,
        *,
        mode: UIMode,
        desktop_path: str | None,
    ) -> LaunchResult:
        assert desktop_path == "configured-app"
        assert (port, view_token, control_token) == (
            served[0].port,
            served[0].view_token,
            served[0].control_token,
        )
        launched.append(mode)
        return launcher.open_ui(
            port, view_token, control_token, mode=mode, desktop_path=desktop_path
        )

    monkeypatch.setattr(cli, "open_ui", open_ui)
    with Ledger.from_config(
        tmp_path,
        cli.load_config(config).ledger,
        clock=FakeClock(),
        guard=RedactionGuard(),
    ) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.add_feature(project.id, "synthetic-demo", "Synthetic demo")
        version = ledger.add_spec_version(feature.id, "spec", "rooms")
        run = ledger.create_run(feature.id, version.id, "specvania/synthetic-demo")
        expected = tuple(
            ledger.append_event(event.model_copy(update={"run_id": run.id, "seq": 0}))
            for event in demo_events()
        )
    result = CliRunner().invoke(cli.create_app(), ["replay", run.id])
    unavailable = mode == "desktop" and not installed
    assert result.exit_code == (1 if unavailable else 0), result.output
    assert served[0].replay == expected and served[0].run_id == run.id
    assert launched == [mode]
    if unavailable:
        assert "Install specvania desktop" in result.output
        assert not commands and not urls
    elif installed and mode != "browser":
        assert commands[0][-2:] == ["--control-token", served[0].control_token]
        assert not urls
    else:
        assert not commands
        assert urls == [launcher.browser_url(served[0].port, served[0].view_token)]
    assert "Traceback" not in result.output
    assert served[0].control_token not in result.output


@pytest.mark.parametrize("source", ["demo", "events.jsonl"])
@pytest.mark.parametrize("installed", [False, True])
def test_portable_replay_uses_auto_launcher_and_keeps_control_out_of_urls(
    source: str,
    installed: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    served: list[UIServer],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    if source != "demo":
        (tmp_path / source).write_text(encode_events(demo_events(), RedactionGuard()))
    before = set(tmp_path.iterdir())
    forbid_config_and_ledger(monkeypatch)
    app = tmp_path / "fake-desktop"
    monkeypatch.setattr(launcher, "find_desktop", lambda _: app if installed else None)
    commands: list[list[str]] = []
    urls: list[str] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", commands.append)
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", urls.append)
    monkeypatch.setattr(cli, "open_ui", launcher.open_ui)
    result = CliRunner().invoke(cli.create_app(), ["replay", source])
    assert result.exit_code == 0, result.output
    server = served[0]
    if installed:
        assert not urls and len(commands) == 1
        command = commands[0]
        assert command[-2:] == ["--control-token", server.control_token]
        urls = [command[command.index("--url") + 1]]
        assert result.output == ""
    else:
        assert not commands and len(urls) == 1
        assert (
            result.output.strip() == "Desktop app unavailable; opening a browser tab."
        )
    assert urls == [launcher.browser_url(server.port, server.view_token)]
    assert server.control_token not in urls[0] + result.output + caplog.text
    assert server.control_token not in encode_events(
        server.replay or (), RedactionGuard()
    )
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("started", [False, True])
def test_ui_startup_callback_runs_only_after_listening_with_assigned_port(
    started: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = RedactionGuard()
    events = demo_events()
    ui = UIServer(
        guard=guard, clock=FakeClock(), run_id=events[0].run_id, replay=events
    )
    calls: list[int] = []

    class Listener:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def bind(self, address: tuple[str, int]) -> None:
            assert address == ("127.0.0.1", 0)

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", 43210)

    listener = Listener()
    monkeypatch.setattr(
        ui_server,
        "socket",
        SimpleNamespace(AF_INET=2, SOCK_STREAM=1, socket=lambda *args: listener),
    )

    async def startup(server: uvicorn.Server, sockets: object = None) -> None:
        assert not calls
        assert sockets == [listener]
        server.started = started

    def run(server: uvicorn.Server, sockets: object = None) -> None:
        asyncio.run(server.startup(sockets=sockets))  # type: ignore[arg-type]

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr(uvicorn.Server, "run", run)
    monkeypatch.setattr(
        ui_server, "open_browser", lambda *args: pytest.fail("Legacy browser launched")
    )
    ui.serve(on_started=lambda: calls.append(ui.port))
    assert calls == ([43210] if started else [])


def test_run_id_without_config_has_clear_error_and_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: list[UIServer]
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.create_app(), ["replay", "20261012-120000-abcd"])
    assert result.exit_code == 1
    assert "Cannot read config" in result.output and "config.toml" in result.output
    assert "Traceback" not in result.output and not served
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("contents", ["", "{}\n", "not json\n"])
def test_invalid_or_empty_jsonl_has_clear_error(
    contents: str, tmp_path: Path, served: list[UIServer]
) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(contents)
    result = CliRunner().invoke(cli.create_app(), ["replay", str(source)])
    assert result.exit_code == 1
    assert "replay" in result.output.lower() and "Traceback" not in result.output
    assert not served


def test_missing_jsonl_has_clear_error(tmp_path: Path, served: list[UIServer]) -> None:
    result = CliRunner().invoke(
        cli.create_app(), ["replay", str(tmp_path / "missing.jsonl")]
    )
    assert result.exit_code == 1
    assert "JSONL" in result.output and "Traceback" not in result.output
    assert not served


def test_ledger_error_has_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: list[UIServer]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".specvania").mkdir()
    (tmp_path / ".specvania/config.toml").write_text("")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise LedgerError(
            "Cannot open ledger; check its URL, connection and permissions."
        )

    monkeypatch.setattr(Ledger, "from_config", unavailable)
    result = CliRunner().invoke(cli.create_app(), ["replay", "unknown-run"])
    assert result.exit_code == 1
    assert "Cannot open ledger" in result.output and "Traceback" not in result.output
    assert not served
