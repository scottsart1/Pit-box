"""`pitwall-app` — what the packaged executable actually runs.

    python -m distribution.main        (dev smoke test: gate is inert here)
    YourPitBox.exe / Your Pit Box.app         (the shipped build)

Everything decision-shaped lives in `launcher.py`; this module only supplies
the real implementations of the callbacks and then hands control to the normal
Your Pit Box server. `src/pitwall` never imports anything from this package, so the
development app is unaffected by all of it.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# Absolute, not relative. PyInstaller runs this file as __main__, which has no
# parent package, so `from . import first_run` raises ImportError at startup in
# the packaged build while working fine under `python -m distribution.main`.
# Absolute imports work under both.
from distribution import first_run
from distribution.launcher import (
    ACTIVATION_ENDPOINT,
    LaunchOutcome,
    LaunchResult,
    launch,
)


class _NullStream(io.TextIOBase):
    """A stream that discards writes and is honest about not being a terminal.

    ``os.devnull`` is the obvious sink and the wrong one on Windows: NUL is a
    character device, so ``open(os.devnull).isatty()`` returns **True**. Binding
    the windowed build's streams to it would make every "am I attached to a
    terminal?" test in every dependency answer yes, and colour codes, progress
    bars and prompts would switch themselves on inside an app that has no
    console at all — re-creating, more quietly, the class of bug this guard
    exists to remove.
    """

    encoding = "utf-8"
    errors = "replace"

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def read(self, *args: object) -> str:
        return ""

    def readline(self, *args: object) -> str:  # type: ignore[override]
        return ""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def _ensure_standard_streams() -> None:
    """Give the windowed build usable stdio objects.

    PyInstaller's ``console=False`` build leaves stdout, stderr and stdin set
    to None. `print` is safe (CPython drops it silently), but anything touching
    a stream object is not: uvicorn's log formatter called
    ``sys.stdout.isatty()`` and killed the app at startup, right after a
    successful activation, with a traceback no buyer could act on.

    Replacing them once, before anything else runs, closes that whole class of
    failure rather than the single instance of it. Each stream gets its own
    object so that closing one cannot take the others with it, and the
    ``sys.__stdout__`` originals are filled in too because parts of the standard
    library reach for those rather than the current values. Real diagnostics go
    to pitwall.log, which depends on none of this.
    """
    for name in ("stdout", "stderr", "stdin"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullStream())
        if getattr(sys, f"__{name}__", None) is None:
            setattr(sys, f"__{name}__", getattr(sys, name))


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
    """Hand over to the ordinary Your Pit Box server.

    `run` configures logging, opens the dashboard in the default browser, and
    serves until the process is closed — so from the buyer's side, launching
    the app is the whole of it.
    """
    from pitwall.main import run

    run()


def _prompt_first_run():  # type: ignore[no-untyped-def]
    """First-run dialogs need the screen: drop the bootloader splash first."""
    from pitwall.main import close_startup_splash

    close_startup_splash()
    return first_run.prompt_first_run()


def _show_error(*args, **kwargs):  # type: ignore[no-untyped-def]
    from pitwall.main import close_startup_splash

    close_startup_splash()
    return first_run.show_message(*args, **kwargs)


def main(argv: list[str] | None = None) -> int:
    del argv
    _ensure_standard_streams()
    result: LaunchResult = launch(
        config_dir(),
        endpoint=os.environ.get("PITWALL_ACTIVATION_ENDPOINT", ACTIVATION_ENDPOINT),
        prompt=_prompt_first_run,
        show_error=_show_error,
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
