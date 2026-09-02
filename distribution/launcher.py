"""Entry point for the packaged, licensed build of Your Pit Box.

The dev app is started with `python -m pitwall.main` and never comes through
here, so nothing in this file affects development. Only the frozen build runs
it.

The orchestration is deliberately free of any UI toolkit: `launch()` takes the
callables it needs, so the whole decision tree is testable without opening a
window. `first_run.py` supplies the real Tk implementations.

Flow (paid edition):

    integrity check ─┬─ tampered ──────► show message, exit (delete nothing)
                     │
                     └─ ok ─┬─ valid license ─────────────► start the app
                            │
                            └─ no license ──► first-run screen
                                                 ├─ cancelled ──► exit quietly
                                                 └─ code + key ─► activate,
                                                                  save key,
                                                                  start the app

Flow (free edition, ``edition.FREE_EDITION``):

    integrity check ─┬─ tampered ──────► show message, exit (delete nothing)
                     │
                     └─ ok ─┬─ welcomed before ───────────► start the app
                            │
                            └─ first launch ──► welcome screen (API key,
                                                optional) ► save key if given,
                                                start the app — closing the
                                                window just skips the key
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .edition import FREE_EDITION
from .licensing import gate
from .licensing.activation_client import ActivationError
from .licensing.license_store import LicenseInvalid

# The deployed activation Worker. Verified live end to end: a real code
# activates, its entitlement verifies against the embedded public key, a second
# device is refused, and the same device may re-activate after a reinstall.
ACTIVATION_ENDPOINT = "https://pitwall-activation.sarthakvij123450.workers.dev/activate"

# The free edition shows its welcome screen exactly once per data directory.
# A marker file rather than "is a key saved?": a driver who chose to skip the
# key must not be asked again on every launch.
WELCOME_MARKER = "welcome_shown"


class LaunchOutcome(Enum):
    RUN = "run"
    ACTIVATED = "activated"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class FirstRunInput:
    """What the first-run screen collects."""

    activation_code: str
    api_key: str


@dataclass(frozen=True, slots=True)
class LaunchResult:
    outcome: LaunchOutcome
    detail: str = ""


def welcome_shown(config_dir: Path) -> bool:
    return (config_dir / WELCOME_MARKER).exists()


def mark_welcome_shown(config_dir: Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / WELCOME_MARKER).write_text("shown\n", encoding="utf-8")


def _save_key_or_explain(
    key: str,
    save_api_key: Callable[[str], None] | None,
    show_error: Callable[[str], None],
) -> None:
    if save_api_key is None or not key.strip():
        return
    try:
        save_api_key(key.strip())
    except Exception as exc:  # noqa: BLE001 - never block a launch on a settings write
        # The key can be set later from the Connection Center, so a failure
        # here is reported but not fatal.
        show_error(
            "Your Pit Box will start, but the API key could not be saved: "
            f"{exc}. Add it from the Connection tab."
        )


def _launch_free(
    config_dir: Path,
    *,
    prompt: Callable[[str], FirstRunInput | None],
    show_error: Callable[[str], None],
    start_app: Callable[[], None],
    save_api_key: Callable[[str], None] | None,
) -> LaunchResult:
    """The free edition: welcome once, never gate."""
    if not gate.integrity_ok():
        show_error(gate.TAMPER_MESSAGE)
        return LaunchResult(LaunchOutcome.BLOCKED, gate.TAMPER_MESSAGE)

    if not welcome_shown(config_dir):
        entered = prompt("")
        # Recorded before the key is saved: a crash inside the save must not
        # turn into a welcome screen on every launch.
        mark_welcome_shown(config_dir)
        if entered is not None:
            _save_key_or_explain(entered.api_key, save_api_key, show_error)
    start_app()
    return LaunchResult(LaunchOutcome.RUN)


def launch(
    config_dir: Path,
    *,
    endpoint: str = ACTIVATION_ENDPOINT,
    prompt: Callable[[str], FirstRunInput | None],
    show_error: Callable[[str], None],
    start_app: Callable[[], None],
    save_api_key: Callable[[str], None] | None = None,
    free: bool | None = None,
) -> LaunchResult:
    """Run the launch gate and, if allowed, start Your Pit Box.

    `prompt` receives a message to display above the form (empty on the first
    attempt, an error on a retry) and returns the entered values, or None if
    the user closed the window. `free` overrides the built edition, which is
    what lets both flows be tested from one suite.
    """
    if free is None:
        free = FREE_EDITION
    if free:
        return _launch_free(
            config_dir,
            prompt=prompt,
            show_error=show_error,
            start_app=start_app,
            save_api_key=save_api_key,
        )

    result = gate.check(config_dir)

    if result.status is gate.GateStatus.TAMPERED:
        show_error(result.detail)
        return LaunchResult(LaunchOutcome.BLOCKED, result.detail)

    if result.status is gate.GateStatus.LICENSED:
        start_app()
        return LaunchResult(LaunchOutcome.RUN)

    message = ""
    if result.status is gate.GateStatus.WRONG_DEVICE:
        # Explain it up front, then still offer the form: someone who has been
        # issued a replacement code should be able to use it right now rather
        # than being locked out until they find support.
        show_error(result.detail)
        message = result.detail
    while True:
        entered = prompt(message)
        if entered is None:
            return LaunchResult(LaunchOutcome.CANCELLED)
        try:
            gate.complete_activation(config_dir, endpoint, entered.activation_code)
        except (ActivationError, LicenseInvalid) as exc:
            # Stay on the form with the reason. A typo should not mean
            # relaunching the app.
            message = str(exc)
            continue

        _save_key_or_explain(entered.api_key, save_api_key, show_error)
        start_app()
        return LaunchResult(LaunchOutcome.ACTIVATED)


__all__ = [
    "ACTIVATION_ENDPOINT",
    "WELCOME_MARKER",
    "FirstRunInput",
    "LaunchOutcome",
    "LaunchResult",
    "launch",
    "mark_welcome_shown",
    "welcome_shown",
]
