"""The warm-up that runs while a driver is on the profile screen.

It must load only what every profile shares: settings are created once per
process from the chosen profile's folder, so loading them early would tie
the process to the wrong driver.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from pitwall import desktop, warmup

ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import json, os, sys
before = {k for k in os.environ if k.startswith("PITWALL_")}
CALL
print(json.dumps({
    "config": "pitwall.config" in sys.modules,
    "app": "pitwall.app" in sys.modules,
    "env": sorted({k for k in os.environ if k.startswith("PITWALL_")} - before),
    "openai": "openai" in sys.modules,
    "fastapi": "fastapi" in sys.modules,
}))
"""


def _probe(call: str, *, path: Path | None = None) -> dict:
    code = PROBE.replace("CALL", call)
    if path is not None:
        code = f"import sys; sys.path.insert(0, {str(path)!r})\n" + code
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    return json.loads(output.stdout.strip().splitlines()[-1])


def test_the_warm_up_loads_libraries_but_no_profile_settings():
    result = _probe("from pitwall.warmup import preload; preload()")
    assert result == {"config": False, "app": False, "env": [], "openai": True, "fastapi": True}


def test_the_android_warm_up_reads_no_settings_and_sets_no_environment():
    result = _probe("import pitbox_android; pitbox_android.warm_up()", path=ROOT / "android/app/src/main/python")
    assert result == {"config": False, "app": False, "env": [], "openai": True, "fastapi": True}


def test_a_library_that_fails_is_skipped_not_fatal():
    timings = warmup.preload(("json", "pitwall_module_that_does_not_exist"))
    assert list(timings) == ["json"]


def test_the_heaviest_libraries_load_first():
    assert warmup.MODULES[:2] == ("openai", "fastapi")


def test_the_desktop_profile_screen_warms_in_the_background(monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(warmup, "preload", lambda: ran.set())
    thread = desktop.start_warmup()
    thread.join(5)
    assert ran.is_set() and thread.daemon
