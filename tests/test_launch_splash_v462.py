"""The launch splash must live in the bootloader, never in this process.

4.6.1 shipped a splash as a Tk window in a daemon thread. tcl86t.dll aborted
the whole frozen app seconds after startup (Windows Event Log 2026-08-12,
exception 0x80000003, faulting module tcl86t.dll) — and the smoke test
missed it because it disabled the browser-opener, which also gated the
splash. 4.6.2 replaces it with PyInstaller's bootloader splash, closed via
pyi_splash's pipe message. These tests pin that design.
"""

import socket
import threading
import time
from pathlib import Path

from pitwall.main import close_splash_when_ready, close_startup_splash

MAIN_PY = (
    Path(__file__).resolve().parents[1] / "src" / "pitwall" / "main.py"
).read_text(encoding="utf-8")
SPEC = (
    Path(__file__).resolve().parents[1]
    / "distribution"
    / "packaging"
    / "pitwall.spec"
).read_text(encoding="utf-8")


def test_no_in_process_tk_window_in_the_server():
    assert "tkinter" not in MAIN_PY, (
        "the splash crashed the frozen build when it ran as in-process Tk; "
        "it must stay in the bootloader (pyi_splash)"
    )
    assert "pyi_splash" in MAIN_PY


def test_the_frozen_build_declares_a_bootloader_splash():
    assert "Splash(" in SPEC
    assert "splash.png" in SPEC
    assert (
        Path(__file__).resolve().parents[1]
        / "distribution"
        / "packaging"
        / "splash.png"
    ).exists()


def test_close_is_a_no_op_outside_a_frozen_build():
    # pyi_splash does not exist in dev; closing must never raise.
    close_startup_splash()


def test_splash_closer_returns_when_the_server_accepts():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        start = time.monotonic()
        worker = threading.Thread(
            target=close_splash_when_ready,
            args=("127.0.0.1", port),
            kwargs={"timeout_s": 10.0},
            daemon=True,
        )
        worker.start()
        worker.join(timeout=5.0)
        assert not worker.is_alive()
        assert time.monotonic() - start < 5.0
    finally:
        listener.close()


def test_splash_closer_gives_up_rather_than_hang():
    # A port nothing listens on: the closer must time out, not spin forever.
    start = time.monotonic()
    close_splash_when_ready("127.0.0.1", 1, timeout_s=1.0)
    assert time.monotonic() - start < 4.0
