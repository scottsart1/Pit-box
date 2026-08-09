"""Your Pit Box distribution licensing.

Isolated from the core application: nothing under src/pitwall imports this
package, and this package's license gate runs only in the packaged build. The
dev app (`python -m pitwall.main`) is never gated.

Only the PUBLIC verification key ships here. The private signing key lives on
the developer's machine (distribution/.secrets, gitignored) and signs codes offline.
"""

from .entitlement import Entitlement, canonical_bytes
from .verify import VerificationError, verify_entitlement

__all__ = [
    "Entitlement",
    "VerificationError",
    "canonical_bytes",
    "verify_entitlement",
]
