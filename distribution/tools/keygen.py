"""Generate the Ed25519 signing key pair for Your Pit Box licensing.

The PRIVATE key is written to distribution/.secrets/ (gitignored) and must never leave
this machine or enter git. Back it up somewhere offline; if it is lost, no new
codes can ever be signed for the existing installed base's public key.

The PUBLIC key is printed and written to licensing/embedded_public_key.txt, to
be baked into the shipped app. Publishing the public key is safe and required.

    python -m distribution.tools.keygen              # refuses to overwrite an existing key
    python -m distribution.tools.keygen --force      # overwrite (you will resign everything)
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DIST = Path(__file__).resolve().parents[1]
SECRETS = DIST / ".secrets"
PRIVATE_PATH = SECRETS / "signing_key.ed25519"
PUBLIC_EMBED = DIST / "licensing" / "embedded_public_key.txt"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if PRIVATE_PATH.exists() and not args.force:
        print(f"Refusing to overwrite existing private key at {PRIVATE_PATH}.")
        print("Use --force only if you intend to re-sign every code.")
        return 1

    SECRETS.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    public = private.public_key()

    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    PRIVATE_PATH.write_text(_b64(private_raw) + "\n", encoding="ascii")
    try:  # best-effort lock-down on POSIX; harmless on Windows
        PRIVATE_PATH.chmod(0o600)
    except OSError:
        pass

    public_b64 = _b64(public_raw)
    PUBLIC_EMBED.write_text(public_b64 + "\n", encoding="ascii")

    print("Generated Ed25519 signing key pair.")
    print(f"  private (SECRET, gitignored): {PRIVATE_PATH}")
    print(f"  public  (embed in app):       {PUBLIC_EMBED}")
    print()
    print("Public key (base64, safe to publish):")
    print(f"  {public_b64}")
    print()
    print("Back up the private key OFFLINE now. It cannot be recovered.")
    if "OneDrive" in str(PRIVATE_PATH):
        print()
        print("WARNING: this repo lives under OneDrive. distribution/.secrets is gitignored,")
        print("but OneDrive may still sync it to the cloud. Move the private key to a")
        print("non-synced location and point PITWALL_SIGNING_KEY at it for code-gen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
