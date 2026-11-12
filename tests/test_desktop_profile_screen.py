"""The desktop driver-profile screen and its hand-over to the dashboard.

The screen is served before the backend reads any settings, from a
loopback-only server; these exercise its decisions, its HTTP boundary, how
it points the process at a profile's folder, and the dashboard endpoints
that show the open driver and switch drivers.
"""
from __future__ import annotations

import http.client
import json
import os
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from pitwall import desktop
from pitwall import driver_profiles as profiles


@pytest.fixture
def gate(tmp_path):
    return desktop.ProfileGate(tmp_path / "PitWallData", legacy_env=tmp_path / "install" / ".env", version="5.7.1-windows")


@pytest.fixture
def served(gate):
    server = desktop.serve(gate, 0)
    yield gate, server.server_address[1]
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    sent = {"Host": f"127.0.0.1:{port}"}
    if body is not None:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    try:
        return response.status, json.loads(data), response
    except ValueError:
        return response.status, data.decode("utf-8"), response


def test_a_fresh_install_offers_setup_and_records_the_first_opening(gate):
    assert gate.state() == (200, {"profiles": [], "existing_data": False})
    status, body = gate.create({"name": "Driver", "method": "pin", "pin": "2468"})
    assert (status, body) == (200, {"ok": True})
    assert gate.opened.is_set() and gate.profile["legacy"] is True
    events = gate.journal.pending()
    assert len(events) == 1 and events[0]["version"] == "5.7.1-windows"
    # Once a profile is opening, nothing else can be opened in this process.
    assert gate.create({"name": "Other", "method": "pin", "pin": "1357"})[0] == 409
    assert gate.state() == (200, {"opening": True})


def test_an_update_is_told_its_data_will_be_attached(gate):
    gate.root.mkdir(parents=True)
    (gate.root / "pitwall.db").write_text("history")
    assert gate.state()[1]["existing_data"] is True


def test_a_wrong_pin_is_refused_and_not_recorded(gate):
    driver = gate.store.create("Driver", "pin", pin="2468")
    assert gate.unlock({"id": driver["id"], "pin": "1111"}) == (422, {"reason": "wrong_pin"})
    assert not gate.opened.is_set() and gate.journal.pending() == []
    assert gate.unlock({"id": driver["id"], "pin": "2468"}) == (200, {"ok": True})
    assert gate.profile["id"] == driver["id"]


def test_an_email_profile_opens_with_its_address(gate):
    gate.store.create("First", "pin", pin="2468")
    driver = gate.store.create("Mail", "email", email="mail@example.test")
    assert gate.unlock({"id": driver["id"], "email": "nope@example.test"}) == (422, {"reason": "wrong_email"})
    assert gate.unlock({"id": driver["id"], "email": "MAIL@example.test"}) == (200, {"ok": True})
    assert gate.profile["legacy"] is False


def test_only_an_unreadable_list_can_be_reset(gate):
    gate.store.create("Driver", "pin", pin="2468")
    assert gate.reset() == (409, {"reason": "readable"})
    gate.store.path.write_text("[broken")
    assert gate.state()[1].get("unavailable") is True
    assert gate.reset() == (200, {"ok": True})
    assert gate.state()[1]["profiles"] == []


def test_the_screen_is_served_on_loopback_in_the_browsers_language(served):
    _gate, port = served
    status, html, response = request(port, "GET", "/", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    assert status == 200 and 'data-ui-language="de"' in html and "pitbox-i18n-catalog" in html
    assert response.getheader("Cache-Control") == "no-store"
    status, english, _ = request(port, "GET", "/")
    assert status == 200 and "data-ui-language" not in english and "Opening driver profiles" in english
    assert request(port, "GET", "/static/js/i18n.js")[0] == 200
    assert request(port, "GET", "/static/../src/pitwall/desktop.py")[0] == 404
    status, health, _ = request(port, "GET", "/api/health")
    assert status == 200 and health["ok"] is True and health["profile_screen"] is True


def test_the_screen_refuses_other_hosts_origins_and_bodies(served):
    gate, port = served
    body = {"name": "Driver", "method": "pin", "pin": "2468"}
    # A page on another site, or a DNS name rebound to 127.0.0.1, cannot use it.
    assert request(port, "GET", "/gate/state", headers={"Host": "attacker.example:80"})[0] == 421
    assert request(port, "POST", "/gate/create", body, headers={"Origin": "https://attacker.example"})[0] == 403
    assert request(port, "POST", "/gate/create", body, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert request(port, "POST", "/gate/create", body, headers={"Content-Type": "text/plain"})[0] == 415
    assert request(port, "POST", "/gate/create", b"x" * (desktop.MAX_BODY + 1))[0] == 413
    assert request(port, "POST", "/gate/create", b"[1]")[0] == 400
    assert not gate.opened.is_set()
    status, reply, _ = request(port, "POST", "/gate/create", body, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert (status, reply) == (200, {"ok": True}) and gate.opened.is_set()


def test_the_screen_tells_its_tab_where_the_dashboard_will_be(served):
    gate, port = served
    assert request(port, "GET", "/gate/next")[1] == {"pending": True}
    assert not gate.next_delivered.is_set()
    gate.next_url = "http://127.0.0.1:8123"
    assert request(port, "GET", "/gate/next")[1] == {"url": "http://127.0.0.1:8123"}
    assert gate.next_delivered.is_set()


def test_hand_over_waits_for_the_tab_then_frees_the_address(gate):
    server = desktop.serve(gate, 0)
    port = server.server_address[1]
    fetched = []

    def page() -> None:
        # The page polls until it learns the dashboard's address.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            reply = request(port, "GET", "/gate/next")[1]
            if "url" in reply:
                fetched.append(reply["url"])
                return
            time.sleep(0.01)

    tab = threading.Thread(target=page)
    tab.start()
    desktop.hand_over(gate, server, "http://127.0.0.1:8123", wait_s=5)
    tab.join(5)
    assert fetched == ["http://127.0.0.1:8123"]
    # The address is free for the dashboard's own server.
    with pytest.raises(OSError):
        request(port, "GET", "/api/health")


def test_opening_a_profile_points_this_process_at_its_folder(tmp_path, monkeypatch):
    root = tmp_path / "PitWallData"
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.chdir(install)
    for name in ("PITWALL_DATA_DIR", "PITWALL_PROFILE_NAME", "PITWALL_PROFILE_SWITCH", "PITWALL_OPEN_BROWSER"):
        monkeypatch.delenv(name, raising=False)
    store = profiles.ProfileStore(root)
    first = store.create("First", "pin", pin="2468")
    second = store.create("Second", "pin", pin="1357")
    assert desktop.activate(root, first) == root
    # The first driver keeps the installation's .env in the start folder.
    assert os.environ["PITWALL_DATA_DIR"] == str(root) and Path(os.getcwd()).samefile(install)
    assert os.environ["PITWALL_PROFILE_SWITCH"] == "restart" and os.environ["PITWALL_OPEN_BROWSER"] == "false"
    folder = desktop.activate(root, second)
    # Every other driver reads and writes .env in their own folder.
    assert folder == root / "driver-profiles" / second["id"] and Path(os.getcwd()).samefile(folder)
    assert os.environ["PITWALL_PROFILE_NAME"] == "Second"


def test_importing_the_profile_screen_reads_no_settings():
    import subprocess
    import sys

    probe = "import sys, pitwall.desktop; print('pitwall.config' in sys.modules)"
    output = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert output.strip() == "False"


@pytest.mark.parametrize(("header", "expected"), [
    ("fr-CA,fr;q=0.9", "fr"), ("en-GB,fr;q=0.5", "en"), ("pt-BR,es;q=0.5", "es"), ("", None),
])
def test_the_screen_speaks_the_first_supported_browser_language(header, expected):
    assert desktop._browser_language(header) == expected


# ----- The dashboard's side -------------------------------------------------

class _FakeServer:
    def __init__(self) -> None:
        self.should_exit = False


@pytest.fixture
def dashboard(monkeypatch):
    from pitwall.app import app

    server = _FakeServer()
    app.state.server = server
    monkeypatch.setattr(profiles, "_switch_requested", threading.Event())
    yield app, server
    app.state.server = None


def test_connection_shows_the_open_driver(dashboard, monkeypatch):
    app, _server = dashboard
    monkeypatch.delenv("PITWALL_PROFILE_NAME", raising=False)
    assert TestClient(app).get("/api/v1/driver-profile").json() == {"active": False}
    monkeypatch.setenv("PITWALL_PROFILE_NAME", "Driver")
    monkeypatch.setenv("PITWALL_PROFILE_SWITCH", "native")
    assert TestClient(app).get("/api/v1/driver-profile").json() == {"active": True, "name": "Driver", "switch": "native"}


def test_switching_driver_saves_and_restarts_only_on_the_desktop(dashboard, monkeypatch):
    app, server = dashboard
    monkeypatch.setenv("PITWALL_PROFILE_NAME", "Driver")
    monkeypatch.setenv("PITWALL_PROFILE_SWITCH", "native")
    assert TestClient(app).post("/api/v1/driver-profile/switch").status_code == 409
    monkeypatch.setenv("PITWALL_PROFILE_SWITCH", "restart")
    lan = TestClient(app, client=("192.168.1.50", 51000))
    assert lan.post("/api/v1/driver-profile/switch").status_code == 403
    assert not server.should_exit and not profiles.switch_requested()
    response = TestClient(app).post("/api/v1/driver-profile/switch")
    assert response.json() == {"stopping": True}
    # The same graceful stop as Quit, then the launcher starts over.
    assert server.should_exit and profiles.switch_requested()
