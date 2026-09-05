"""Entry point the Android service calls into.

The desktop app configures itself from environment variables and a `.env`
file; on Android there is neither a home directory in the usual sense nor a
browser to open, so this module sets the environment the backend expects
before `pitwall` is imported, then hands over to the ordinary `run()`.

Everything in `pitwall` is shared with the desktop build unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

_configured = False


def configure(files_dir: str, static_dir: str) -> None:
    """Point the backend at the app's private storage. Call before start()."""
    global _configured
    files = Path(files_dir)
    data = files / "PitWallData"
    data.mkdir(parents=True, exist_ok=True)
    # Chaquopy does not set HOME; Path.home() would otherwise fail.
    os.environ.setdefault("HOME", str(files))
    os.environ["PITWALL_DATA_DIR"] = str(data)
    os.environ["PITWALL_STATIC_DIR"] = static_dir
    # The dashboard lives in the activity's WebView, not a browser tab.
    os.environ["PITWALL_OPEN_BROWSER"] = "false"
    os.environ["PITWALL_WEB_HOST"] = "127.0.0.1"
    # Voice needs the Android audio backend (phase 2); until then the
    # microphone is not opened, so nothing retries against a missing device.
    os.environ.setdefault("PITWALL_WAKE_ENABLED", "false")
    os.chdir(str(files))
    _configured = True


def dashboard_url() -> str:
    from pitwall.config import settings
    from pitwall.main import local_dashboard_url

    return local_dashboard_url(settings.web_host, settings.web_port)


def start() -> None:
    """Run the server on the calling thread until it is asked to stop."""
    if not _configured:
        raise RuntimeError("configure() must be called before start()")
    from pitwall.main import run

    run()


def stop() -> None:
    """Ask the running server to exit; start() then returns."""
    from pitwall.app import app

    server = getattr(app.state, "server", None)
    if server is not None:
        server.should_exit = True
