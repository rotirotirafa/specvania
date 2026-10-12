"""T19 shell discovery and CLI output without launching a real process."""

import logging
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import pytest
import typer
from typer.testing import CliRunner

from specvania import cli
from specvania.clock import FakeClock
from specvania.config import Config, DesktopConfig, UIConfig
from specvania.ledger import Ledger
from specvania.run.bus import EventBus
from specvania.run.events import RedactionGuard, RoomEntered, SecretLeakError
from specvania.ui import launcher
from specvania.ui.server import UIServer


def executable(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("fake desktop; never executed")
    path.chmod(0o755)
    return path


def test_discovery_uses_config_env_path_then_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = executable(tmp_path, "configured")
    environment = executable(tmp_path, "environment")
    on_path = executable(tmp_path, "specvania-desktop")
    macos = tmp_path / "specvania.app"
    macos.mkdir()
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "MACOS_APP", macos)
    monkeypatch.setenv("SPECVANIA_DESKTOP", str(environment))
    searched: list[str] = []

    def which(name: str) -> str | None:
        searched.append(name)
        return str(on_path) if on_path.exists() else None

    monkeypatch.setattr(launcher.shutil, "which", which)
    assert launcher.find_desktop(str(configured)) == configured
    assert searched == []
    configured.unlink()
    assert launcher.find_desktop(str(configured)) == environment
    environment.unlink()
    assert launcher.find_desktop(str(configured)) == on_path
    assert searched == ["specvania-desktop"]
    on_path.unlink()
    assert launcher.find_desktop(str(configured)) == macos
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    assert launcher.find_desktop(str(configured)) is None


def test_discovery_ignores_nonexecutables_and_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.write_text("not executable")
    directory = tmp_path / "directory"
    directory.mkdir()
    monkeypatch.setenv("SPECVANIA_DESKTOP", str(directory))
    monkeypatch.setattr(launcher.shutil, "which", lambda _: None)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    assert launcher.find_desktop(str(plain)) is None


@pytest.mark.parametrize("mode", ["auto", "desktop"])
@pytest.mark.parametrize("macos", [False, True])
def test_desktop_receives_control_token_only_as_separate_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["auto", "desktop"],
    macos: bool,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = tmp_path / "specvania.app" if macos else executable(tmp_path, "desktop")
    if macos:
        app.mkdir()
    monkeypatch.setattr(launcher.sys, "platform", "darwin" if macos else "linux")
    monkeypatch.setattr(launcher, "find_desktop", lambda _: app)
    launched: list[list[str]] = []

    def spawn(command: list[str]) -> None:
        launched.append(command)

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        launcher.webbrowser, "open_new_tab", lambda _: pytest.fail("browser launched")
    )
    result = launcher.open_ui(8765, "watch & only", "fake-control-secret", mode=mode)
    assert result.mode == "desktop" and result.notice is None
    assert len(launched) == 1
    command = launched[0]
    prefix = ["open", "-n", "-a", str(app), "--args"] if macos else [str(app)]
    assert command[: len(prefix)] == prefix
    assert command[-2:] == ["--control-token", "fake-control-secret"]
    url = command[command.index("--url") + 1]
    parsed = urlsplit(url)
    assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:8765"
    assert parse_qs(parsed.query) == {"view_token": ["watch & only"]}
    assert "fake-control-secret" not in url
    assert "fake-control-secret" not in repr(result) + result.model_dump_json()
    output = capsys.readouterr()
    assert "fake-control-secret" not in caplog.text + output.out + output.err


def test_browser_mode_does_not_discover_or_launch_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        launcher, "find_desktop", lambda _: pytest.fail("desktop discovery attempted")
    )
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", opened.append)
    result = launcher.open_ui(8765, "watch", "fake-control", mode="browser")
    assert result.mode == "browser" and result.notice is None
    assert opened == ["http://127.0.0.1:8765/?view_token=watch"]


def invoke_ui(config: Config) -> tuple[int, str]:
    # Later command rooms call this same CLI wiring once the server has a port.
    # Do not implement plan/run/replay merely to exercise a launcher contract.
    app = typer.Typer()

    @app.command()
    def show() -> None:
        cli.launch_ui(8765, "watch", "fake-control", config=config)

    result = CliRunner().invoke(app, [])
    return result.exit_code, result.output


def test_auto_without_app_opens_browser_and_prints_one_line_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher, "find_desktop", lambda _: None)
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", opened.append)
    exit_code, output = invoke_ui(Config())
    assert exit_code == 0
    assert opened == ["http://127.0.0.1:8765/?view_token=watch"]
    assert len(output.splitlines()) == 1
    assert "desktop" in output.lower() and "browser" in output.lower()


def test_desktop_without_app_exits_nonzero_with_install_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher, "find_desktop", lambda _: None)
    monkeypatch.setattr(
        launcher.webbrowser, "open_new_tab", lambda _: pytest.fail("browser launched")
    )
    with pytest.raises(launcher.DesktopLaunchError, match="[Ii]nstall"):
        launcher.open_ui(8765, "watch", "fake-control", mode="desktop")
    exit_code, output = invoke_ui(Config(ui=UIConfig(mode="desktop")))
    assert exit_code == 1
    assert "install" in output.lower()
    assert "SPECVANIA_DESKTOP" in output
    assert "Traceback" not in output and "fake-control" not in output


@pytest.mark.parametrize("mode", ["auto", "desktop"])
def test_process_errors_are_typed_and_do_not_echo_secret_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["auto", "desktop"],
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = executable(tmp_path, "desktop")
    monkeypatch.setattr(launcher, "find_desktop", lambda _: app)
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open_new_tab", opened.append)

    def failing_spawn(command: list[str]) -> None:
        raise OSError(f"bad launch: {command}")

    monkeypatch.setattr(launcher.subprocess, "Popen", failing_spawn)
    with caplog.at_level(logging.DEBUG):
        if mode == "desktop":
            with pytest.raises(launcher.DesktopLaunchError) as caught:
                launcher.open_ui(8765, "watch", "fake-control", mode=mode)
            assert "fake-control" not in str(caught.value)
            assert caught.value.__suppress_context__
            assert opened == []
        else:
            result = launcher.open_ui(8765, "watch", "fake-control", mode=mode)
            assert result.mode == "browser" and result.notice
            assert "fake-control" not in result.model_dump_json()
            assert opened == ["http://127.0.0.1:8765/?view_token=watch"]
    assert "fake-control" not in caplog.text


def test_cli_passes_desktop_config_and_explicit_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = executable(tmp_path, "configured")
    monkeypatch.delenv("SPECVANIA_DESKTOP", raising=False)
    launched: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", launched.append)
    config = Config(ui=UIConfig(mode="browser"), desktop=DesktopConfig(path=str(app)))
    assert cli.launch_ui(8765, "watch", "fake-control", config=config, mode="auto") == (
        "desktop"
    )
    assert launched[0][0] == str(app)


def test_control_token_stays_out_of_launch_urls_logs_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    guard = RedactionGuard()
    clock = FakeClock()
    with Ledger.open(
        f"sqlite:///{tmp_path / 'ledger.db'}", clock=clock, guard=guard
    ) as ledger:
        project = ledger.add_project(tmp_path)
        feature = ledger.add_feature(project.id, "desktop", "Desktop")
        version = ledger.add_spec_version(feature.id, "spec", "rooms")
        run = ledger.create_run(feature.id, version.id, "specvania/desktop")
        server = UIServer(
            guard=guard,
            clock=clock,
            run_id=run.id,
            ledger=ledger,
            bus=EventBus(guard),
            port=8765,
        )
        app = executable(tmp_path, "desktop")
        monkeypatch.setattr(launcher, "find_desktop", lambda _: app)
        launched: list[list[str]] = []
        monkeypatch.setattr(launcher.subprocess, "Popen", launched.append)
        launcher.open_ui(server.port, server.view_token, server.control_token)
        assert server.control_token not in launched[0][2]
        event = RoomEntered(ts=clock.now(), run_id=run.id, seq=0, room_id="T19")
        ledger.append_event(event)
        with pytest.raises(SecretLeakError):
            ledger.append_event(
                event.model_copy(update={"room_id": server.control_token})
            )
        assert len(ledger.list_events(run.id)) == 1
        assert (
            server.control_token not in repr(ledger.list_events(run.id)) + caplog.text
        )


def test_mistaken_control_token_as_view_token_is_rejected_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        launcher.subprocess, "Popen", lambda _: pytest.fail("process launched")
    )
    monkeypatch.setattr(
        launcher.webbrowser, "open_new_tab", lambda _: pytest.fail("browser launched")
    )
    with pytest.raises(launcher.DesktopLaunchError):
        launcher.open_ui(8765, "fake-control", "fake-control", mode="auto")


def test_plain_node_desktop_check_runs_without_electron() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["npm", "--prefix", str(root / "apps/desktop"), "run", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
