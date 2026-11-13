"""Start Your Pit Box on a computer, opening a driver profile first.

    python -m pitwall.desktop        (start_pitwall.bat; the installed app via
                                      distribution/main.py runs the same steps)

Each launch serves the driver-profile screen in the browser from a small
loopback-only server, before anything that reads settings is imported: the
backend's settings, database and caches are created once per process from the
chosen profile's folder and never switched in place. Once a driver opens
their profile the screen hands its address over to the dashboard, the backend
starts in that profile's folder, and the browser tab follows it there.

Switch driver on CONNECTION saves the session, stops the backend and starts
the app again, which shows this screen. ``python -m pitwall.main`` remains the
development entry point and opens no profile screen.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from . import driver_profiles as profiles
from .ui_i18n import SUPPORTED_LANGUAGES, render_page

DEFAULT_PORT = 8000
MAX_BODY = 4096
STATIC_FILES = {"/static/js/i18n.js": ("js/i18n.js", "text/javascript; charset=utf-8")}


def data_root() -> Path:
    """The installation's data folder; profiles other than the first live inside it."""
    override = os.environ.get("PITWALL_DATA_DIR")
    return Path(override) if override else Path.home() / "PitWallData"


def static_root() -> Path:
    override = os.environ.get("PITWALL_STATIC_DIR")
    if override:
        return Path(override)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "static"
    return Path(__file__).resolve().parents[2] / "static"


def _browser_language(header: str) -> str | None:
    """The first supported language in an Accept-Language header."""
    for part in header.split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in SUPPORTED_LANGUAGES:
            return code
    return None


class ProfileGate:
    """The profile screen's decisions, independent of HTTP so they can be tested."""

    def __init__(self, root: Path, *, legacy_env: Path | None = None,
                 version: str | None = None, clock=time.time) -> None:
        self.root = Path(root)
        self.store = profiles.ProfileStore(root, clock=clock)
        self.journal = profiles.SignInJournal(root)
        self.legacy_env = legacy_env
        self.version = version or profiles.version_label(__version__, sys.platform)
        self._clock = clock
        self._lock = threading.Lock()
        self.opened = threading.Event()
        self.profile: dict[str, Any] | None = None
        self.next_url: str | None = None
        self.next_delivered = threading.Event()

    def state(self) -> tuple[int, dict[str, Any]]:
        if self.opened.is_set():
            return 200, {"opening": True}
        try:
            listed = self.store.list()
        except profiles.IndexUnavailable:
            return 200, {"unavailable": True, "profiles": []}
        return 200, {
            "profiles": [{"id": p.get("id"), "name": p.get("name"), "method": p.get("method")} for p in listed],
            "existing_data": not listed and profiles.has_existing_data(self.root, self.legacy_env),
        }

    def _open(self, profile: dict[str, Any]) -> None:
        # Every successful opening is queued before the profile opens.
        try:
            self.journal.enqueue(profile, int(self._clock()), self.version)
        except OSError:
            pass
        self.profile = profile
        self.opened.set()

    def create(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if self.opened.is_set():
                return 409, {"reason": "opening"}
            method = body.get("method")
            try:
                profile = self.store.create(str(body.get("name", "")), str(method),
                                            email=str(body.get("email", "")), pin=str(body.get("pin", "")))
            except profiles.Rejected as rejected:
                return 422, {"reason": rejected.reason}
            except profiles.IndexUnavailable:
                return 503, {"reason": "unavailable"}
            self._open(profile)
            return 200, {"ok": True}

    def unlock(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if self.opened.is_set():
                return 409, {"reason": "opening"}
            identifier = str(body.get("id", ""))
            try:
                profile = self.store.find(identifier)
                if profile.get("method") == "email":
                    if not self.store.check_email(identifier, str(body.get("email", ""))):
                        return 422, {"reason": "wrong_email"}
                elif not self.store.check_pin(identifier, str(body.get("pin", ""))):
                    return 422, {"reason": "wrong_pin"}
                profile = self.store.find(identifier)
            except profiles.Rejected as rejected:
                return 422, {"reason": rejected.reason}
            except profiles.IndexUnavailable:
                return 503, {"reason": "unavailable"}
            self._open(profile)
            return 200, {"ok": True}

    def reset(self) -> tuple[int, dict[str, Any]]:
        with self._lock:
            try:
                self.store.list()
                return 409, {"reason": "readable"}   # Only an unreadable list is set aside.
            except profiles.IndexUnavailable:
                pass
            try:
                self.store.reset()
            except OSError:
                return 503, {"reason": "unavailable"}
            return 200, {"ok": True}

    def next(self) -> tuple[int, dict[str, Any]]:
        if self.next_url is None:
            return 200, {"pending": True}
        self.next_delivered.set()
        return 200, {"url": self.next_url}


class _Handler(BaseHTTPRequestHandler):
    server_version = "YourPitBoxProfiles"
    gate: ProfileGate
    port: int

    def log_message(self, format: str, *args: object) -> None:
        return  # Never log request lines: they can carry a typed email address.

    def _allowed_host(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}:
            return False
        return (self.headers.get("Sec-Fetch-Site") or "same-origin").lower() in {"same-origin", "none"}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        if not self._allowed_host():
            return self._json(421, {"reason": "host"})
        url = urlsplit(self.path)
        if url.path == "/":
            requested = (parse_qs(url.query).get("lang") or [None])[0]
            html = render_page(static_root(), "profiles.html", requested,
                               _browser_language(self.headers.get("Accept-Language") or ""))
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if url.path in STATIC_FILES:
            relative, content_type = STATIC_FILES[url.path]
            return self._send(200, (static_root() / relative).read_bytes(), content_type)
        if url.path == "/api/health":
            # A second launch finds this screen as "already running" and opens it.
            return self._json(200, {"ok": True, "profile_screen": True, "version": __version__})
        if url.path == "/gate/state":
            return self._json(*self.gate.state())
        if url.path == "/gate/next":
            return self._json(*self.gate.next())
        return self._json(404, {"reason": "not_found"})

    def do_POST(self) -> None:
        if not self._allowed_host():
            return self._json(421, {"reason": "host"})
        if not self._same_origin():
            return self._json(403, {"reason": "origin"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"reason": "content_type"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._json(413, {"reason": "size"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"reason": "json"})
        if not isinstance(body, dict):
            return self._json(400, {"reason": "json"})
        routes = {"/gate/create": self.gate.create, "/gate/unlock": self.gate.unlock}
        path = urlsplit(self.path).path
        if path in routes:
            return self._json(*routes[path](body))
        if path == "/gate/reset":
            return self._json(*self.gate.reset())
        return self._json(404, {"reason": "not_found"})


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket bind a port another app is
    # listening on; the screen must fail over to a free port instead.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def serve(gate: ProfileGate, port: int) -> ThreadingHTTPServer:
    """Bind the profile screen to loopback, on ``port`` or else any free port."""
    handler = type("ProfileGateHandler", (_Handler,), {"gate": gate})
    try:
        server = _Server(("127.0.0.1", port), handler)
    except OSError:
        server = _Server(("127.0.0.1", 0), handler)
    handler.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, name="pitwall-profile-screen", daemon=True).start()
    return server


def _running_dashboard(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            return response.status == 200 and json.loads(response.read()).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return False


def close_splash() -> None:
    """Close the PyInstaller bootloader splash; cosmetic, never blocks a launch."""
    with contextlib.suppress(Exception):
        import pyi_splash  # type: ignore[import-not-found]

        pyi_splash.close()


def _wants_browser() -> bool:
    return os.environ.get("PITWALL_OPEN_BROWSER", "true").strip().lower() not in {"0", "false", "no", "off"}


def start_warmup() -> threading.Thread:
    """Import the backend's libraries while the driver is on the profile screen.

    Reads no settings or keys (see pitwall.warmup), so whichever profile
    opens, nothing needs undoing; the import after opening finds them loaded.
    """
    from .warmup import preload

    thread = threading.Thread(target=preload, name="pitwall-warmup", daemon=True)
    thread.start()
    return thread


def activate(root: Path, profile: dict[str, Any]) -> Path:
    """Point this process at the profile's folder before settings are read."""
    folder = profiles.data_dir(root, profile)
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["PITWALL_DATA_DIR"] = str(folder)
    if not profile.get("legacy"):
        # Settings and keys are read from ".env" in the working directory;
        # each additional driver keeps theirs in their own folder.
        os.chdir(folder)
    os.environ["PITWALL_PROFILE_NAME"] = str(profile.get("name", ""))
    os.environ["PITWALL_PROFILE_SWITCH"] = "restart"
    # The profile screen's tab follows the dashboard; no second tab opens.
    os.environ["PITWALL_OPEN_BROWSER"] = "false"
    return folder


def relaunch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "pitwall.desktop"]


def _relaunch(environment: dict[str, str], cwd: str, dashboard_port: int) -> None:
    child = dict(environment)
    # The dashboard tab reloads into the profile screen when it comes back on
    # the same address, so the new launch does not open another tab.
    child["PITWALL_PROFILE_SCREEN_REUSE_PORT"] = str(dashboard_port)
    options: dict[str, Any] = {"cwd": cwd, "env": child, "close_fds": True}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    subprocess.Popen(relaunch_command(), **options)


def open_profile(root: Path | None = None) -> tuple[dict[str, Any], ProfileGate, ThreadingHTTPServer] | None:
    """Show the profile screen and wait for a driver. None: another launch is serving."""
    root = root or data_root()
    try:
        port = int(os.environ.get("PITWALL_WEB_PORT", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT
    if _running_dashboard(port):
        close_splash()
        if _wants_browser():
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return None
    legacy_env = Path(os.environ.get("PITWALL_ENV_FILE", ".env")).resolve()
    gate = ProfileGate(root, legacy_env=legacy_env)
    server = serve(gate, port)
    start_warmup()
    bound = server.server_address[1]
    close_splash()
    reuse = os.environ.pop("PITWALL_PROFILE_SCREEN_REUSE_PORT", "")
    if _wants_browser() and reuse != str(bound):
        webbrowser.open(f"http://127.0.0.1:{bound}/")
    gate.opened.wait()
    return gate.profile, gate, server  # type: ignore[return-value]


def hand_over(gate: ProfileGate, server: ThreadingHTTPServer, url: str, *, wait_s: float = 5.0) -> None:
    """Tell the screen where the dashboard will be, then free the address."""
    gate.next_url = url
    gate.next_delivered.wait(wait_s)
    server.shutdown()
    server.server_close()


def run(root: Path | None = None, *, before_run=None) -> int:
    """Profile screen, then the backend in that profile's folder, then relaunch on switch."""
    root = root or data_root()
    launch_environment, launch_cwd = dict(os.environ), os.getcwd()
    opened = open_profile(root)
    if opened is None:
        return 0
    profile, gate, server = opened
    if "pitwall.config" in sys.modules and not profile.get("legacy"):
        # Settings are created once per process; read before a profile was
        # chosen they would point at the first driver's folder.
        raise RuntimeError("Settings were loaded before a driver profile was opened")
    activate(root, profile)
    if before_run is not None:
        before_run()
    # Importing the app reads the profile's settings, including a saved port.
    import pitwall.app  # noqa: F401

    from .config import settings
    from .main import local_dashboard_url
    from .main import run as run_backend

    hand_over(gate, server, local_dashboard_url(settings.web_host, settings.web_port))
    profiles.start_sender(root)
    run_backend()
    if profiles.switch_requested():
        _relaunch(launch_environment, launch_cwd, settings.web_port)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
