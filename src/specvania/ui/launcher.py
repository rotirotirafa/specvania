"""Browser shell: only the view token travels in the loopback URL."""

import webbrowser
from urllib.parse import urlencode


class BrowserLaunchError(Exception):
    """The player can fix the browser installation or local port configuration."""


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
