"""Entry point for the packaged, licensed build of Pit Wall.

The dev app is started with `python -m pitwall.main` and never comes through
here, so nothing in this file affects development. Only the frozen build runs
it.

The orchestration is deliberately free of any UI toolkit: `launch()` takes the
callables it needs, so the whole decision tree is testable without opening a
window. `first_run.py` supplies the real Tk implementations.

Flow:

    integrity check ─┬─ tampered ──────► show message, exit (delete nothing)
                     │
                     └─ ok ─┬─ valid license ─────────────► start the app
                            │
                            └─ no license ──► first-run screen
                                                 ├─ cancelled ──► exit quietly
                                                 └─ code + key ─► activate,
                                                                  save key,
                                                                  start the app
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .licensing import gate
from .licensing.activation_client import ActivationError
from .licensing.license_store import LicenseInvalid

# Overridden at build time to the deployed Worker. Left as a placeholder so a
# packaged build that was never configured fails loudly at activation rather
# than silently pointing somewhere wrong.
ACTIVATION_ENDPOINT = "https://activation.example.invalid/activate"


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


def launch(
    config_dir: Path,
    *,
    endpoint: str = ACTIVATION_ENDPOINT,
    prompt: Callable[[str], FirstRunInput | None],
    show_error: Callable[[str], None],
    start_app: Callable[[], None],
    save_api_key: Callable[[str], None] | None = None,
) -> LaunchResult:
    """Run the launch gate and, if allowed, start Pit Wall.

    `prompt` receives a message to display above the form (empty on the first
    attempt, an error on a retry) and returns the entered values, or None if
    the user closed the window.
    """
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

        if save_api_key is not None and entered.api_key.strip():
            try:
                save_api_key(entered.api_key.strip())
            except Exception as exc:  # noqa: BLE001 - never block a valid launch
                # The key can be set later from the Connection Center, so a
                # failure here is reported but not fatal.
                show_error(
                    "Pit Wall is activated, but the OpenAI key could not be "
                    f"saved: {exc}. Add it from the Connection tab."
                )
        start_app()
        return LaunchResult(LaunchOutcome.ACTIVATED)


__all__ = [
    "ACTIVATION_ENDPOINT",
    "FirstRunInput",
    "LaunchOutcome",
    "LaunchResult",
    "launch",
]
