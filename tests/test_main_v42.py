from __future__ import annotations

import socket
import threading
import time

import pitwall.main as main_module
from pitwall.main import local_dashboard_url, open_dashboard_when_ready


def test_local_dashboard_url_never_uses_bind_wildcard() -> None:
    assert local_dashboard_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"
    assert local_dashboard_url("::", 8000) == "http://[::1]:8000"
    assert local_dashboard_url("192.168.1.42", 9000) == "http://192.168.1.42:9000"


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_the_dashboard_is_not_opened_before_the_server_answers(monkeypatch) -> None:
    """The buyer must never meet "cannot reach this page" as their first screen.

    This ran on a fixed 1.5s timer while application startup takes far longer,
    so the browser reliably beat the server and the working dashboard only
    appeared for anyone who thought to refresh.
    """
    opened: list[str] = []
    monkeypatch.setattr(main_module.webbrowser, "open", lambda url: opened.append(url))
    port = _free_port()

    waiter = threading.Thread(
        target=open_dashboard_when_ready,
        args=("0.0.0.0", port),
        kwargs={"timeout_s": 20.0, "poll_s": 0.05},
        daemon=True,
    )
    waiter.start()
    time.sleep(0.6)
    assert opened == [], "the browser was opened before anything was listening"

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    try:
        waiter.join(timeout=10)
    finally:
        listener.close()

    assert opened == [f"http://127.0.0.1:{port}"]


def test_a_server_that_never_starts_opens_nothing(monkeypatch) -> None:
    # Bounded, and it must not open a tab onto a page that cannot load.
    opened: list[str] = []
    monkeypatch.setattr(main_module.webbrowser, "open", lambda url: opened.append(url))

    started = time.monotonic()
    open_dashboard_when_ready("0.0.0.0", _free_port(), timeout_s=1.0, poll_s=0.05)
    elapsed = time.monotonic() - started

    assert opened == []
    assert elapsed < 8.0, "the wait is not bounded by timeout_s"
