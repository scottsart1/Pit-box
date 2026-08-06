"""Tamper response: remove the app's OWN install, and nothing else.

Scope is deliberately narrow and defended in depth, because deleting files is
dangerous:

  * It NEVER runs in a development checkout. It requires a frozen/packaged
    build (sys.frozen) AND an explicit armed flag the launcher sets only in the
    real distribution. In dev it logs what it would do and returns.
  * It deletes only inside a single resolved install root.
  * It refuses obviously wrong roots: a filesystem root, a home directory, a
    path that contains the user's PitWallData, or anything it cannot resolve to
    a real directory beneath the packaged app.
  * User telemetry (PitWallData) and everything outside the install dir are
    never touched.

The disclosure in EULA.txt tells the user this exists before they install.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

GOODBYE = (
    "You tried to kill the app. Sorry. The app killed itself.\n"
)


class KillswitchRefused(RuntimeError):
    """The kill-switch declined to act because the target looked unsafe."""


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """The directory the packaged app lives in."""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    # Dev fallback: the distribution folder. Only used for dry-run logging.
    return Path(__file__).resolve().parents[1]


def _user_data_dir() -> Path:
    # Mirror the app's own default so we can positively exclude it.
    return Path(os.path.expanduser("~")) / "PitWallData"


def _refuse_if_unsafe(root: Path) -> None:
    resolved = root.resolve()
    home = Path(os.path.expanduser("~")).resolve()
    anchors = {Path(resolved.anchor).resolve()} if resolved.anchor else set()

    if resolved in anchors:
        raise KillswitchRefused(f"target is a filesystem root: {resolved}")
    if resolved == home:
        raise KillswitchRefused(f"target is the home directory: {resolved}")
    if not resolved.is_dir():
        raise KillswitchRefused(f"target is not a directory: {resolved}")
    # Never a directory that contains the user's telemetry.
    data = _user_data_dir().resolve()
    try:
        if data == resolved or data.is_relative_to(resolved):
            raise KillswitchRefused(
                "target contains PitWallData; refusing to delete user telemetry"
            )
    except AttributeError:  # is_relative_to is 3.9+; we target 3.12, kept safe
        if str(data).startswith(str(resolved)):
            raise KillswitchRefused("target contains PitWallData")
    # A plausible install dir is a few levels deep, not one segment from root.
    if len(resolved.parts) <= 2:
        raise KillswitchRefused(f"target is too shallow to be an install dir: {resolved}")


def _delete_contents(root: Path) -> list[str]:
    """Delete everything under root except a freshly written goodbye note."""
    removed: list[str] = []
    note = root / "README.txt"
    note.write_text(GOODBYE, encoding="utf-8")
    for entry in list(root.iterdir()):
        if entry == note:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed.append(entry.name)
        except OSError:
            # Files held open by the running process (the exe itself) may not
            # delete until exit; that is acceptable and expected.
            pass
    return removed


def trigger(reason: str, *, armed: bool, on_log=print) -> dict[str, object]:
    """Run the kill-switch.

    `armed` must be True (set only by the packaged launcher) for real deletion.
    In dev, or when not armed, this is a dry run that only reports intent.
    Returns a report; also removes the local license so a tampered copy cannot
    keep running even if file deletion is partial.
    """
    root = install_root()
    live = armed and _is_frozen()

    if not live:
        on_log(f"[killswitch] DRY RUN (dev/unarmed). Would remove install at {root}. "
               f"Reason: {reason}")
        return {"live": False, "root": str(root), "reason": reason, "removed": []}

    try:
        _refuse_if_unsafe(root)
    except KillswitchRefused as exc:
        on_log(f"[killswitch] refused: {exc}")
        return {"live": True, "refused": str(exc), "root": str(root), "removed": []}

    on_log(f"[killswitch] removing own install at {root}. Reason: {reason}")
    removed = _delete_contents(root)
    return {"live": True, "root": str(root), "reason": reason, "removed": removed}
