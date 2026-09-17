"""Small, advisory progress events: never change a transfer's outcome."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

ProgressCallback = Callable[[dict[str, Any]], None]
PHASES = frozenset({"snapshot", "records", "packing", "finalizing", "checksum",
                    "extracting", "checking_rows", "checking_links", "importing_rows",
                    "saving_files", "committing"})
UNITS = frozenset({"bytes", "files", "rows", "pages"})


def public_progress(value: Any) -> dict[str, Any] | None:
    """Whitelist optional peer metadata; never relay paths or arbitrary strings."""
    if not isinstance(value, dict) or not isinstance(value.get("phase"), str) or value["phase"] not in PHASES:
        return None
    result = {"phase": value["phase"]}
    if isinstance(value.get("unit"), str) and value["unit"] in UNITS:
        result["unit"] = value["unit"]
        for key in ("done", "total"):
            count = value.get(key)
            if type(count) is int and 0 <= count <= 2**53 - 1:
                result[key] = count
        if result.get("done", 0) > result.get("total", 2**53 - 1):
            result.pop("total", None)
    return result


class TransferProgress:
    """Throttle in-memory updates, but always report phase starts and finishes."""

    def __init__(self, callback: ProgressCallback | None = None):
        self.callback = callback
        self._phase = None
        self._last = 0.0

    def __call__(self, phase: str, done: int | None = None,
                 total: int | None = None, unit: str | None = None) -> None:
        if self.callback is None:
            return
        now = time.monotonic()
        if phase == self._phase and now - self._last < .25 and not (total is not None and done == total):
            return
        self._phase, self._last = phase, now
        event = public_progress({"phase": phase, "done": done, "total": total, "unit": unit})
        if event is not None:
            try:
                self.callback(event)
            except Exception:  # noqa: BLE001 -- reporting cannot change transaction outcomes
                # An observer must not abort an export or roll back committed data.
                self.callback = None
