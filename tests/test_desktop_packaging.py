"""Installed T21 artifacts are discoverable without running Electron."""

from pathlib import Path

import pytest

from specvania.ui import launcher


def make_executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("fake installed app; never executed")
    path.chmod(0o755)
    return path


def test_installed_macos_app_at_default_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert launcher.MACOS_APP == Path("/Applications/specvania.app")
    installed = tmp_path / "Applications/specvania.app"
    installed.mkdir(parents=True)
    monkeypatch.setattr(launcher, "MACOS_APP", installed)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.delenv("SPECVANIA_DESKTOP", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert launcher.find_desktop() == installed


@pytest.mark.parametrize("name", ["specvania-0.1.0-x64.AppImage", "specvania-desktop"])
def test_installed_linux_artifact_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    installed = make_executable(tmp_path, name)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.delenv("SPECVANIA_DESKTOP", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert launcher.find_desktop() == installed
    commands: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", commands.append)
    result = launcher.open_ui(8765, "view", "control", mode="desktop")
    assert result.mode == "desktop"
    assert commands == [
        [
            str(installed),
            "--url",
            "http://127.0.0.1:8765/?view_token=view",
            "--control-token",
            "control",
        ]
    ]


def test_linux_appimage_discovery_respects_path_order_and_executable_bit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    plain = first / "specvania-0.1.0-arm64.AppImage"
    plain.write_text("not executable")
    (first / "specvania-directory.AppImage").mkdir()
    make_executable(first, "other-0.1.0-x64.AppImage")
    expected = make_executable(second, "specvania-0.1.0-x64.AppImage")
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.delenv("SPECVANIA_DESKTOP", raising=False)
    monkeypatch.setenv("PATH", f"{first}:{second}")
    assert launcher.find_desktop() == expected
    earlier = make_executable(first, "specvania-0.2.0-x64.AppImage")
    assert launcher.find_desktop() == earlier
    named = make_executable(second, "specvania-desktop")
    assert launcher.find_desktop() == named
    monkeypatch.setenv("SPECVANIA_DESKTOP", str(expected))
    assert launcher.find_desktop() == expected
    assert launcher.find_desktop(str(earlier)) == earlier


def test_appimage_discovery_is_linux_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_executable(tmp_path, "specvania-0.1.0-x64.AppImage")
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "MACOS_APP", tmp_path / "missing.app")
    monkeypatch.delenv("SPECVANIA_DESKTOP", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert launcher.find_desktop() is None
