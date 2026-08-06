"""The cached local license: what lets the app run offline after activation.

Written once at activation, read on every launch. Validation is fully offline:
verify the entitlement signature against the embedded public key, and confirm
the machine hash still matches this device. No network is used here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .device import device_hash
from .entitlement import Entitlement
from .verify import VerificationError, verify_entitlement

LICENSE_VERSION = 1


class LicenseInvalid(Exception):
    """The cached license is missing, malformed, forged, or for another device."""


class LicenseForAnotherDevice(LicenseInvalid):
    """A genuine, correctly signed license that belongs to a different machine.

    Kept distinct from the other failures because it is the one the user needs
    explaining: the licence is real and they did nothing wrong, they have just
    copied an activated install to a second computer. Treating it like "no
    licence" would drop them on the activation form with no idea why, and they
    would burn a second code trying to fix it.
    """


@dataclass(frozen=True, slots=True)
class License:
    entitlement: Entitlement
    signature_b64: str
    device_hash: str
    activated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "license_version": LICENSE_VERSION,
            "entitlement": self.entitlement.to_dict(),
            "signature": self.signature_b64,
            "device_hash": self.device_hash,
            "activated_at": self.activated_at,
        }


def license_path(config_dir: Path) -> Path:
    return config_dir / "license.json"


def save_license(config_dir: Path, license_obj: License) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = license_path(config_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(license_obj.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)  # atomic publish
    return path


def load_and_validate(config_dir: Path) -> License:
    """Load the cached license and prove it is genuine for this machine.

    Raises LicenseInvalid on any failure; the caller then routes to first-run
    activation. This never deletes anything and never phones home.
    """
    path = license_path(config_dir)
    if not path.exists():
        raise LicenseInvalid("no license present")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LicenseInvalid(f"license unreadable: {exc}") from exc

    try:
        entitlement = Entitlement.from_dict(data["entitlement"])
        signature = str(data["signature"])
        bound_device = str(data["device_hash"])
        activated_at = str(data.get("activated_at", ""))
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseInvalid(f"license malformed: {exc}") from exc

    # 1) The entitlement must be genuinely signed by the private key.
    try:
        verify_entitlement(entitlement, signature)
    except VerificationError as exc:
        raise LicenseInvalid(f"license signature invalid: {exc}") from exc

    # 2) It must be bound to THIS machine.
    try:
        current = device_hash()
    except Exception as exc:  # noqa: BLE001 - device read is platform-specific
        raise LicenseInvalid(f"device id unavailable: {exc}") from exc
    if bound_device != current:
        raise LicenseForAnotherDevice(
            "This copy of Pit Wall was activated on a different computer.\n\n"
            "Each activation code works on one computer only. Copying an "
            "activated installation to another machine does not carry the "
            "licence with it.\n\n"
            "If you have moved to a new PC, or this one was rebuilt, email "
            "vale.scott00@gmail.com and a replacement code will be issued - "
            "you will not be asked to buy it again."
        )

    return License(
        entitlement=entitlement,
        signature_b64=signature,
        device_hash=bound_device,
        activated_at=activated_at,
    )
