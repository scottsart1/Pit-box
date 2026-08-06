"""`pitwall-app` — what the packaged executable actually runs.

    python -m distribution.main        (dev smoke test: gate is inert here)
    PitWall.exe / Pit Wall.app         (the shipped build)

Everything decision-shaped lives in `launcher.py`; this module only supplies
the real implementations of the callbacks and then hands control to the normal
Pit Wall server. `src/pitwall` never imports anything from this package, so the
development app is unaffected by all of it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import first_run
from .launcher import ACTIVATION_ENDPOINT, LaunchOutcome, LaunchResult, launch


def config_dir() -> Path:
    """Where the licence lives: beside the driver's telemetry, not in the app.

    Keeping it out of the install directory means reinstalling or updating Pit
    Wall does not require re-activating, and an uninstall that removes the
    program folder does not silently burn the user's one activation.
    """
    override = os.environ.get("PITWALL_DATA_DIR")
    root = Path(override) if override else Path(os.path.expanduser("~")) / "PitWallData"
    return root / "license"


def _save_api_key(key: str) -> None:
    """Persist the first-run key through the app's own credential store."""
    from pitwall import credentials

    credentials.save_key(key)


def _start_app() -> None:
    """Hand over to the ordinary Pit Wall server."""
    from pitwall.main import main as pitwall_main

    pitwall_main()


def main(argv: list[str] | None = None) -> int:
    del argv
    result: LaunchResult = launch(
        config_dir(),
        endpoint=os.environ.get("PITWALL_ACTIVATION_ENDPOINT", ACTIVATION_ENDPOINT),
        prompt=first_run.prompt_first_run,
        show_error=first_run.show_message,
        start_app=_start_app,
        save_api_key=_save_api_key,
    )
    if result.outcome is LaunchOutcome.BLOCKED:
        return 2
    if result.outcome is LaunchOutcome.CANCELLED:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
