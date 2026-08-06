"""Verify an entitlement signature with the embedded public key.

This is the only trust anchor in the app. If the signature is valid against
the public key, the entitlement is genuine — it was signed by the holder of
the private key, which never leaves the developer's machine. No server, no
network, and no local file can forge a valid entitlement.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature

from .entitlement import Entitlement, canonical_bytes
from .keys import load_public_key


class VerificationError(Exception):
    """The entitlement signature did not verify against the public key."""


def verify_entitlement(entitlement: Entitlement, signature_b64: str) -> None:
    """Raise VerificationError unless the signature is genuine.

    signature_b64 is the base64 Ed25519 signature over canonical_bytes of the
    entitlement, as produced by the offline code-generation tool.
    """
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise VerificationError("signature is not valid base64") from exc
    if len(signature) != 64:
        raise VerificationError("signature is not a 64-byte Ed25519 signature")

    public_key = load_public_key()
    try:
        public_key.verify(signature, canonical_bytes(entitlement))
    except InvalidSignature as exc:
        raise VerificationError("entitlement signature does not verify") from exc
