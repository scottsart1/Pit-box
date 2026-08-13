from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler

import uvicorn

from .config import settings

log = logging.getLogger(__name__)


def local_dashboard_url(host: str, port: int) -> str:
    """Return a browser destination, never the server-only wildcard address."""

    normalized = host.strip().casefold()
    if normalized == "0.0.0.0":
        normalized = "127.0.0.1"
    elif normalized in {"::", "[::]"}:
        normalized = "[::1]"
    elif ":" in normalized and not normalized.startswith("["):
        normalized = f"[{normalized}]"
    return f"http://{normalized}:{int(port)}"


def configure_logging() -> None:
    """Send Your Pit Box logs to a rotating file as well as the console.

    Without this, output only ever reaches the console window that
    ``start_pitwall.bat`` opens, so nothing survives once it is closed and a
    problem reported after a session cannot be investigated.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # The console half of the promise above. uvicorn used to supply the only
    # console handler through its own dictConfig; `run` now disables that
    # because it crashed the packaged build, so it is added here instead —
    # and only when there is somewhere to write. The shipped build is windowed
    # (console=False), where sys.stderr is None and a handler bound to it
    # silently discards every record.
    has_console = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if sys.stderr is not None and not has_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    if any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        return
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            settings.data_dir / "pitwall.log",
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    except OSError:
        # Logging to disk is a diagnostic aid, never a startup dependency.
        return
    handler.setFormatter(formatter)
    root.addHandler(handler)


def close_startup_splash() -> None:
    """Close the PyInstaller bootloader splash, if one is showing.

    The splash itself is drawn by the BOOTLOADER, in its own process space
    on its own main thread — never by this process's Python code. A first
    attempt ran a Tk window in a daemon thread here instead, and tcl86t.dll
    aborted the whole frozen app seconds after startup (Windows Event Log,
    2026-08-12, exception 0x80000003) on the developer's own machine while
    the smoke test — which had disabled the browser-opener and with it the
    splash — stayed green. pyi_splash.close() only writes a message down the
    bootloader's pipe, so it is safe from any thread; outside a frozen build
    the import simply fails and there is nothing to close.
    """
    try:
        import pyi_splash  # type: ignore[import-not-found]

        pyi_splash.close()
    except Exception:  # noqa: BLE001 - cosmetic; never block the launch
        pass


def close_splash_when_ready(
    host: str,
    port: int,
    *,
    timeout_s: float = 90.0,
) -> None:
    """Dismiss the bootloader splash once the server accepts a connection.

    On timeout it closes anyway: a splash that outlives a failed launch would
    hide the failure. Runs regardless of open_browser — the splash exists in
    every frozen launch.
    """
    target = "127.0.0.1" if host.strip() in {"0.0.0.0", "", "::", "[::]"} else host
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((target, port), timeout=0.25):
                break
        except OSError:
            time.sleep(0.3)
    close_startup_splash()


def open_dashboard_when_ready(
    host: str,
    port: int,
    *,
    timeout_s: float = 180.0,
    poll_s: float = 0.25,
) -> None:
    """Open the dashboard once the server answers, rather than after a guess.

    This used to fire on a fixed 1.5 second timer. Application startup takes
    considerably longer than that — the database, capture recovery and the
    audio device all have to come up first — so the browser reliably arrived
    before the server did and the buyer's first sight of Your Pit Box was their
    browser's own "cannot reach this page". The dashboard was fine; it only
    appeared if they happened to refresh.

    Waiting for an accepted connection means the tab opens on a working page
    the moment there is one. The timeout only bounds the wait: if the server
    never comes up there is nothing useful to show anyway, and the failure is
    in pitwall.log.
    """
    target = "127.0.0.1" if host.strip() in {"0.0.0.0", "", "::", "[::]"} else host
    url = local_dashboard_url(host, port)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((target, int(port)), timeout=1.0):
                break
        except OSError:
            time.sleep(poll_s)
    else:
        log.warning("Dashboard did not become reachable within %.0fs", timeout_s)
        return
    webbrowser.open(url)


def another_instance_is_serving(host: str, port: int, timeout_s: float = 2.0) -> bool:
    """True when a healthy Your Pit Box already answers on this host/port.

    Launching a second copy used to half-start: application startup ran far
    enough to leave a dead 0-byte capture stub, the port bind then failed with
    WinError 10048, and the new process died — while its browser thread opened
    the OLD instance's dashboard, so it looked like the launch had worked.
    When an instance already answers, the honest move is to open its dashboard
    and start nothing.
    """
    target = "127.0.0.1" if host.strip() in {"0.0.0.0", "", "::", "[::]"} else host
    try:
        with urllib.request.urlopen(
            f"http://{target}:{int(port)}/api/health", timeout=timeout_s
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read())
    except (OSError, ValueError, TypeError):
        # Nothing answering, still shutting down, or not speaking HTTP:
        # start normally and let the bind itself be the arbiter.
        return False
    return isinstance(payload, dict) and payload.get("ok") is True


def run() -> None:
    configure_logging()
    # Importing the app applies saved dashboard settings over the .env
    # defaults (settings_service.apply_saved_overrides at module scope), and
    # a saved web_port must be final BEFORE the single-instance probe and the
    # browser-opener read it — otherwise both aim at the old port while the
    # server binds the new one. The import lives inside run() on purpose: a
    # frozen build cannot resolve uvicorn's "pitwall.app:app" string form,
    # and importing here surfaces real tracebacks (see the Config note below).
    from .app import app

    if another_instance_is_serving(settings.web_host, settings.web_port):
        url = local_dashboard_url(settings.web_host, settings.web_port)
        log.info(
            "Your Pit Box is already running at %s; opening its dashboard "
            "instead of starting a second copy",
            url,
        )
        close_startup_splash()
        if settings.open_browser:
            webbrowser.open(url)
        return
    threading.Thread(
        target=close_splash_when_ready,
        args=(settings.web_host, settings.web_port),
        name="pitwall-splash-close",
        daemon=True,
    ).start()
    if settings.open_browser:
        threading.Thread(
            target=open_dashboard_when_ready,
            args=(settings.web_host, settings.web_port),
            name="pitwall-open-dashboard",
            daemon=True,
        ).start()
    # The app object was imported at the top of run(): uvicorn gets the
    # object, never the "pitwall.app:app" string a frozen build cannot
    # resolve ("Error loading ASGI app. Could not import module"), and a
    # failing import shows its real traceback instead of uvicorn's
    # single-line summary. Reload is off, so nothing needs the string form.
    # Built explicitly rather than via uvicorn.run() so the Server object can be
    # reached again: there is no other way to ask it to stop from inside a
    # request, and the packaged build is windowed, so there is no console to
    # press Ctrl+C in and no window to close. Without this the only way to quit
    # Your Pit Box was Task Manager.
    config = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
        # LAN pairing credentials must never appear in raw request-line logs.
        access_log=False,
        # Never let uvicorn install its own logging. Its default config builds a
        # ColourizedFormatter whose __init__ calls sys.stdout.isatty(), and the
        # shipped build is windowed, so sys.stdout is None: the packaged app
        # died at startup with "Unable to configure formatter 'default'"
        # immediately after a successful activation, while dev was unaffected.
        #
        # None rather than a de-coloured copy of uvicorn's config, because that
        # config also binds its handlers to sys.stderr and keeps propagate off:
        # it survives the windowed build but throws every uvicorn record into a
        # dead stream. With no config at all, uvicorn's loggers propagate to the
        # root logger configured above and land in pitwall.log, which is the
        # only place a buyer's startup failure can be read after the fact.
        log_config=None,
    )
    server = uvicorn.Server(config)
    # Published on the app rather than held in a module global, so the shutdown
    # endpoint can reach it without importing this module. Under
    # `python -m pitwall.main` this file is ALSO loaded as __main__, so an
    # import from the endpoint would bind a second, separate copy whose global
    # was never assigned — and quitting would fail with "nothing to stop" in
    # development while appearing to work in the packaged build.
    app.state.server = server
    try:
        server.run()
    finally:
        # Cleared so a shutdown request after the server has stopped reports
        # honestly instead of pretending to act on a server that is gone.
        app.state.server = None


if __name__ == "__main__":
    run()
