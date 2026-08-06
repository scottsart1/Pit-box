"""The launch gate.

Decides, on every start of the packaged app, whether to run, to show first-run
activation, or to disable the app because it was tampered with.

Flow:
  1. Integrity self-check of the licensing modules. If the verification code
     was patched, refuse to start.
  2. Try to load and validate a cached license (offline). If valid, run.
  3. Otherwise return NEEDS_ACTIVATION; the UI collects a code + API key and
     calls complete_activation().

The tamper response is to decline to launch, never to delete anything. The
same detection deters casual patching, but the cost of a false positive (a
damaged install, a quarantined file, a bad disk sector) is one reinstall
rather than a customer losing their copy of the app.

The dev app never imports this module, so `python -m pitwall.main` is ungated.
"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .activation_client import ActivationError, activate
from .device import device_hash
from .entitlement import Entitlement
from .license_store import (
    License,
    LicenseForAnotherDevice,
    LicenseInvalid,
    load_and_validate,
    save_license,
)

# Files whose bytes are hashed into the integrity manifest. Patching any of them
# changes the hash. device.py is guarded because it computes the machine hash
# that license_store checks against: patching it to return a constant would
# otherwise defeat device binding without altering any other guarded file.
_GUARDED = (
    "entitlement.py",
    "verify.py",
    "keys.py",
    "license_store.py",
    "device.py",
    "gate.py",
)
_MANIFEST = Path(__file__).with_name("integrity_manifest.txt")

# Shown to the user when the self-check fails. Written for the innocent case,
# which is the far more likely one.
TAMPER_MESSAGE = (
    "Pit Wall could not verify its own program files, so it has not started.\n\n"
    "This usually means the installation is damaged - an interrupted update, a "
    "disk error, or antivirus quarantining a file. Reinstalling from your "
    "original download normally fixes it.\n\n"
    "Your saved sessions in PitWallData are untouched, and your activation code "
    "is still valid. You will not need a new one."
)


class GateStatus(Enum):
    LICENSED = "licensed"
    NEEDS_ACTIVATION = "needs_activation"
    WRONG_DEVICE = "wrong_device"
    TAMPERED = "tampered"


@dataclass(frozen=True, slots=True)
class GateResult:
    status: GateStatus
    license: License | None = None
    detail: str = ""


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _digest_of(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _module_digest() -> str:
    """Hash whatever actually carries the licensing logic on this install.

    A packaged build has no `.py` files: PyInstaller compiles them into an
    archive inside the executable. Hashing source paths therefore crashed the
    shipped app on every launch with FileNotFoundError, while working
    perfectly in development where the files do exist. So the frozen build
    hashes the executable, which is both what exists and what an attacker
    would actually have to modify.
    """
    if is_frozen():
        return _digest_of([Path(sys.executable).resolve()])
    here = Path(__file__).parent
    return _digest_of([here / name for name in _GUARDED])


def digest_for_packaged_executable(executable: Path) -> str:
    """The digest the shipped app will compute for itself at launch.

    The build driver calls this on the freshly built executable so the
    recorded value and the runtime check are produced by the same code and
    cannot drift into permanently disagreeing.
    """
    return _digest_of([Path(executable).resolve()])


MANIFEST_NAME = "integrity_manifest.txt"


def write_integrity_manifest(destination: Path | None = None) -> str:
    """Record the expected digest of this install.

    For a frozen build this must be called *after* the executable exists — its
    own hash cannot be inside it — so the build driver writes it beside the
    packaged app once PyInstaller has finished.
    """
    value = _module_digest()
    target = destination or _MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value + "\n", encoding="ascii")
    return value


def integrity_ok() -> bool:
    """True unless this install differs from what the build recorded.

    Absence of the manifest (a development checkout) is treated as OK:
    integrity is only enforced in a build that shipped one.
    """
    if not _MANIFEST.exists():
        return True
    try:
        expected = _MANIFEST.read_text(encoding="ascii").strip()
        return _module_digest() == expected
    except OSError:
        # A manifest we cannot read is a damaged install, not proof of
        # tampering — but it is also not proof of integrity. Refusing to run
        # is the safe answer, and the message tells the user to reinstall.
        return False


def check(config_dir: Path) -> GateResult:
    """Evaluate the license state at launch.

    Never deletes or modifies anything. TAMPERED means "do not start and show
    `detail`"; the caller is responsible for displaying it and exiting.
    """
    if not integrity_ok():
        return GateResult(GateStatus.TAMPERED, detail=TAMPER_MESSAGE)

    try:
        lic = load_and_validate(config_dir)
        return GateResult(GateStatus.LICENSED, license=lic)
    except LicenseForAnotherDevice as exc:
        # Distinct from "no licence": the user needs telling why a working
        # install stopped working after they copied it.
        return GateResult(GateStatus.WRONG_DEVICE, detail=str(exc))
    except LicenseInvalid as exc:
        return GateResult(GateStatus.NEEDS_ACTIVATION, detail=str(exc))


def complete_activation(
    config_dir: Path,
    endpoint: str,
    code: str,
) -> License:
    """Perform first activation and persist a device-bound license.

    Raises ActivationError (network/claim problems) or LicenseInvalid (the
    server returned something that does not verify against the public key).
    """
    this_device = device_hash()
    result = activate(endpoint, code, this_device)

    # Trust nothing the server said until the signature verifies locally.
    from .verify import VerificationError, verify_entitlement

    try:
        verify_entitlement(result.entitlement, result.signature_b64)
    except VerificationError as exc:
        raise LicenseInvalid(
            f"activation server returned an entitlement that does not verify: {exc}"
        ) from exc

    lic = License(
        entitlement=result.entitlement,
        signature_b64=result.signature_b64,
        device_hash=this_device,
        activated_at=_utc_stamp(),
    )
    save_license(config_dir, lic)
    return lic


def _utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = [
    "MANIFEST_NAME",
    "TAMPER_MESSAGE",
    "ActivationError",
    "Entitlement",
    "GateResult",
    "GateStatus",
    "check",
    "complete_activation",
    "digest_for_packaged_executable",
    "integrity_ok",
    "is_frozen",
    "write_integrity_manifest",
]
