"""Quitting Your Pit Box from the dashboard.

Closing the browser tab only closes the dashboard; the server keeps running.
The packaged build is windowed, so there is no console to interrupt and no
window to close, and Task Manager used to be the only way out.

These exercise the endpoint directly rather than starting a server: the
interesting behaviour is who is allowed to call it and what it does to the
server object, and neither needs a real socket.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall.app import app  # noqa: E402


class _FakeServer:
    """Stands in for uvicorn.Server, which only needs one flag set."""

    def __init__(self) -> None:
        self.should_exit = False


@pytest.fixture
def server() -> _FakeServer:
    fake = _FakeServer()
    app.state.server = fake
    yield fake
    app.state.server = None


def _lan_client() -> TestClient:
    # TestClient's default host counts as loopback, so a caller from the
    # network has to be simulated with a real routable address.
    return TestClient(app, client=("192.168.1.50", 51000))


def test_the_dashboard_can_stop_pit_wall(server: _FakeServer) -> None:
    response = TestClient(app).post("/api/shutdown")
    assert response.status_code == 200
    assert response.json() == {"stopping": True}
    # uvicorn's own exit flag, so the lifespan teardown still runs and the
    # session being recorded is finalised rather than truncated.
    assert server.should_exit is True


def test_a_lan_client_cannot_stop_pit_wall(server: _FakeServer) -> None:
    # With LAN access enabled the dashboard is reachable from the console and
    # every other device on the network. None of them may end a session that
    # is being recorded.
    response = _lan_client().post("/api/shutdown")
    assert response.status_code == 403
    assert server.should_exit is False


def test_shutdown_reports_honestly_when_there_is_no_server() -> None:
    # Reachable when the app is hosted by something other than pitwall.main.
    # Claiming success while nothing stops would be worse than refusing.
    app.state.server = None
    response = TestClient(app).post("/api/shutdown")
    assert response.status_code == 503
