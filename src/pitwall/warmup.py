"""Load what every driver profile's backend needs while the profile screen is open.

Most of a cold start is importing third-party libraries (the OpenAI client and
FastAPI alone are most of it), and those are the same whoever drives. The
profile screens on Android and the desktop import them in the background
while a driver reads the screen and types their PIN, so only the chosen
profile's own settings, keys and history are left to load afterwards.

Nothing here reads settings, opens a database or touches a key: the
backend's settings are created once per process from the chosen profile's
folder, so ``pitwall.config`` must not be imported until a profile is open.
Adding a new driver therefore needs nothing undone; the same libraries
serve whichever profile opens.
"""
from __future__ import annotations

import importlib
import logging
import time

log = logging.getLogger(__name__)

# Heaviest first: a driver who types quickly still gets the biggest share.
MODULES = (
    "openai",
    "fastapi",
    "starlette.staticfiles",
    "uvicorn",
    "numpy",
    "httpx",
    "anyio",
    "jsonschema",
    "pydantic",
    "pydantic_settings",
    "dotenv",
    "f1.packets",
)


def preload(modules: tuple[str, ...] = MODULES) -> dict[str, float]:
    """Import ``modules``; returns the seconds each took. Never raises.

    A library that fails to import here is skipped: the backend imports it
    again when it starts and reports the failure where it always has.
    """
    timings: dict[str, float] = {}
    for name in modules:
        started = time.perf_counter()
        try:
            importlib.import_module(name)
        except Exception:  # Best effort: the backend's own import reports it.
            log.debug("Warm-up skipped %s", name, exc_info=True)
            continue
        timings[name] = time.perf_counter() - started
    return timings


__all__ = ["MODULES", "preload"]
