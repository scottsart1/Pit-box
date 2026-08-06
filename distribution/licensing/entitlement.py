"""The signed entitlement: what a genuine, purchased code authorizes.

The entitlement is what gets signed at code-generation time. Its canonical
byte form MUST be identical on the signer (code-gen) and the verifier (the
app), or valid signatures fail. Both sides call canonical_bytes() here; there
is one implementation and no second place to get it wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

# Bump only with a migration plan: older installs verify against the shape
# they were built with.
ENTITLEMENT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The authenticated claim carried by a code.

    It intentionally does NOT bind a device. Codes are pre-signed offline in
    batches, before any buyer or machine is known, so the device cannot be in
    the signature. Device binding is enforced separately by the activation
    server (one claim per code) and by the local license checking the machine
    hash on every launch.
    """

    version: int
    code_id: str
    sku: str
    issued: str  # e.g. "2026-08" — coarse, not a per-buyer timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "code_id": self.code_id,
            "sku": self.sku,
            "issued": self.issued,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Entitlement":
        return cls(
            version=int(data["version"]),
            code_id=str(data["code_id"]),
            sku=str(data["sku"]),
            issued=str(data["issued"]),
        )


def canonical_bytes(entitlement: Entitlement | Mapping[str, Any]) -> bytes:
    """The exact bytes that are signed and verified.

    Deterministic: sorted keys, no insignificant whitespace, UTF-8. Any change
    to this function is a signature-breaking change and must be versioned.
    """
    data = (
        entitlement.to_dict()
        if isinstance(entitlement, Entitlement)
        else dict(entitlement)
    )
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
