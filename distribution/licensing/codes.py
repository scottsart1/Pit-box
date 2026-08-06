"""Activation code format.

A code is the human-typable public identifier of an entitlement: the
`code_id`. It is NOT the signature (an Ed25519 signature is 64 bytes and cannot
be typed). The signature is fetched from the activation server at first
activation and then verified offline against the embedded public key.

Format: PITW-XXXXX-XXXXX-XXXXX
  - Crockford base32 (no I, L, O, U — avoids ambiguity when typed/read aloud).
  - 15 payload characters = 75 bits of entropy, far beyond guessable for a
    hobby product's volume.
  - A group layout that is easy to read back over the phone or Discord.
"""

from __future__ import annotations

import re
import secrets

# Crockford base32 alphabet, uppercase, ambiguous letters removed.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_GROUPS = 3
_GROUP_LEN = 5
_PAYLOAD_LEN = _GROUPS * _GROUP_LEN

_CODE_RE = re.compile(
    r"^PITW-([" + _ALPHABET + r"]{5})-([" + _ALPHABET + r"]{5})-([" + _ALPHABET + r"]{5})$"
)


def generate_code() -> str:
    """A fresh random activation code."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_PAYLOAD_LEN))
    groups = [body[i : i + _GROUP_LEN] for i in range(0, _PAYLOAD_LEN, _GROUP_LEN)]
    return "PITW-" + "-".join(groups)


_CROCKFORD = str.maketrans({"O": "0", "I": "1", "L": "1"})


def normalize_code(raw: str) -> str | None:
    """Canonicalize user input, or None if it is not a well-formed code.

    Accepts lower case, extra spaces or dashes, an omitted PITW prefix, and
    Crockford's common read-back substitutions (O->0, I/L->1) so a
    mistyped-but-recognizable code still works. Returns the canonical
    `PITW-XXXXX-XXXXX-XXXXX` form.
    """
    if not raw:
        return None
    text = raw.strip().upper().replace(" ", "").replace("_", "")
    # Strip the prefix BEFORE any substitution: "PITW" itself contains an "I",
    # which the Crockford I->1 rule would otherwise corrupt.
    for prefix in ("PITW-", "PITW"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    body = text.replace("-", "").translate(_CROCKFORD)
    if len(body) != _PAYLOAD_LEN or any(c not in _ALPHABET for c in body):
        return None
    groups = [body[i : i + _GROUP_LEN] for i in range(0, _PAYLOAD_LEN, _GROUP_LEN)]
    candidate = "PITW-" + "-".join(groups)
    return candidate if _CODE_RE.match(candidate) else None


def is_valid_code(raw: str) -> bool:
    return normalize_code(raw) is not None
