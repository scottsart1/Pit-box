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
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .activation_client import ActivationError, activate
from .device import device_hash
from .entitlement import Entitlement
from .license_store import License, LicenseInvalid, load_and_validate, save_license

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
    TAMPERED = "tampered"


@dataclass(frozen=True, slots=True)
class GateResult:
    status: GateStatus
    license: License | None = None
    detail: str = ""


def _module_digest() -> str:
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for name in _GUARDED:
        digest.update((here / name).read_bytes())
    return digest.hexdigest()


def write_integrity_manifest() -> str:
    """Called at build time to bake in the expected hash of the license code."""
    value = _module_digest()
    _MANIFEST.write_text(value + "\n", encoding="ascii")
    return value


def integrity_ok() -> bool:
    """True unless the guarded modules differ from the build-time manifest.

    Absence of the manifest (a dev tree) is treated as OK: integrity is only
    enforced in a build that shipped a manifest.
    """
    if not _MANIFEST.exists():
        return True
    expected = _MANIFEST.read_text(encoding="ascii").strip()
    return _module_digest() == expected


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
    "GateStatus",
    "GateResult",
    "TAMPER_MESSAGE",
    "check",
    "complete_activation",
    "integrity_ok",
    "write_integrity_manifest",
    "ActivationError",
    "Entitlement",
]
