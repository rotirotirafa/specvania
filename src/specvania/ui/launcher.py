"""UI shells: URLs carry only the view token; desktop argv carries control."""

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlencode

from pydantic import BaseModel, ConfigDict

type UIMode = Literal["auto", "browser", "desktop"]

MACOS_APP = Path("/Applications/specvania.app")


class BrowserLaunchError(Exception):
    """The player can fix the browser installation or local port configuration."""


class DesktopLaunchError(Exception):
    """The player can install or configure the optional desktop app."""


class LaunchResult(BaseModel):
    """Non-secret shell selection and optional notice for the CLI to print."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["browser", "desktop"]
    notice: str | None = None


def _usable_app(path: Path) -> bool:
    return (path.is_file() and os.access(path, os.X_OK)) or (
        sys.platform == "darwin" and path.suffix == ".app" and path.is_dir()
    )


def find_desktop(config_path: str | None = None) -> Path | None:
    """Find the first usable configured, environment, PATH or macOS app."""
    for value in (config_path, os.environ.get("SPECVANIA_DESKTOP")):
        if value:
            path = Path(value).expanduser()
            if _usable_app(path):
                return path.resolve()
    value = shutil.which("specvania-desktop")
    if value:
        path = Path(value)
        if _usable_app(path):
            return path.resolve()
    if sys.platform == "darwin" and _usable_app(MACOS_APP):
        return MACOS_APP
    return None


def browser_url(port: int, view_token: str) -> str:
    if not 1 <= port <= 65535:
        raise BrowserLaunchError("The UI needs an assigned local port.")
    return f"http://127.0.0.1:{port}/?{urlencode({'view_token': view_token})}"


def open_browser(port: int, view_token: str) -> None:
    """Open the watch-only shell; no control-token argument is accepted."""
    try:
        webbrowser.open_new_tab(browser_url(port, view_token))
    except webbrowser.Error:
        raise BrowserLaunchError(
            "Cannot open the UI; check the default browser."
        ) from None


def open_ui(
    port: int,
    view_token: str,
    control_token: str,
    *,
    mode: UIMode = "auto",
    desktop_path: str | None = None,
) -> LaunchResult:
    """Launch after the server has assigned its port; never print or emit events.

    The CLI prints the returned notice and handles typed errors. Launch failure
    details can contain argv, so they are replaced with a fixed diagnostic.
    """
    if mode not in ("auto", "browser", "desktop"):
        raise DesktopLaunchError("Choose UI mode auto, browser or desktop.")
    url = browser_url(port, view_token)
    if control_token and control_token in unquote(url):
        raise DesktopLaunchError("The view and control tokens must stay separate.")
    if mode == "browser":
        open_browser(port, view_token)
        return LaunchResult(mode="browser")
    app = find_desktop(desktop_path)
    if app is not None:
        command = (
            ["open", "-n", "-a", str(app), "--args"]
            if sys.platform == "darwin" and app.suffix == ".app"
            else [str(app)]
        )
        command.extend(["--url", url, "--control-token", control_token])
        try:
            subprocess.Popen(command)
        except (OSError, ValueError):
            if mode == "desktop":
                raise DesktopLaunchError(
                    "Cannot open the desktop app; check desktop.path or "
                    "SPECVANIA_DESKTOP, or reinstall specvania desktop."
                ) from None
        else:
            return LaunchResult(mode="desktop")
    elif mode == "desktop":
        raise DesktopLaunchError(
            "Install specvania desktop, then set desktop.path or SPECVANIA_DESKTOP "
            "to the app, or put specvania-desktop on PATH "
            "(macOS: /Applications/specvania.app)."
        )
    open_browser(port, view_token)
    return LaunchResult(
        mode="browser", notice="Desktop app unavailable; opening a browser tab."
    )
