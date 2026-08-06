"""The embedded public verification key.

Only the public key ships. It is loaded from embedded_public_key.txt, which is
written by keygen. There is no code path that loads or needs the private key in
the application.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_EMBED = Path(__file__).with_name("embedded_public_key.txt")


class MissingPublicKeyError(RuntimeError):
    """The build is missing its embedded public key."""


def load_public_key() -> Ed25519PublicKey:
    if not _EMBED.exists():
        raise MissingPublicKeyError(
            "embedded_public_key.txt is missing; run `python -m distribution.tools.keygen` "
            "before building a distributable."
        )
    raw = base64.b64decode(_EMBED.read_text(encoding="ascii").strip())
    if len(raw) != 32:
        raise MissingPublicKeyError("embedded public key is not a 32-byte Ed25519 key")
    return Ed25519PublicKey.from_public_bytes(raw)


def public_key_b64() -> str:
    return _EMBED.read_text(encoding="ascii").strip()
