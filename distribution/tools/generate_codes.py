"""Mint a batch of signed activation codes. PRIVATE — output never committed.

Produces three things, all under distribution/ledger/ (gitignored):
  1. A ledger JSON: every code, its signed entitlement, and its lifecycle
     (unused -> sold -> redeemed_email). This is your source of truth. Guard it.
  2. A D1 seed .sql to upload the codes' claim state + signatures to the
     activation server.
  3. A human list (.txt) of just the codes, to paste out as you sell them.

The private key is read from $PITWALL_SIGNING_KEY (a path) if set, else from
distribution/.secrets/signing_key.ed25519. Keep it off any synced/cloud folder.

    python -m distribution.tools.generate_codes --count 50 --issued 2026-08
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution.licensing.codes import generate_code, normalize_code  # noqa: E402
from distribution.licensing.entitlement import (  # noqa: E402
    ENTITLEMENT_VERSION,
    Entitlement,
    canonical_bytes,
)

DEFAULT_KEY = DIST / ".secrets" / "signing_key.ed25519"
LEDGER_DIR = DIST / "ledger"
SKU = "pitwall-desktop-1"


def _load_private_key() -> Ed25519PrivateKey:
    path = Path(os.environ.get("PITWALL_SIGNING_KEY", str(DEFAULT_KEY)))
    if not path.exists():
        raise SystemExit(
            f"private signing key not found at {path}. Run keygen, or set "
            f"PITWALL_SIGNING_KEY to its path."
        )
    raw = base64.b64decode(path.read_text(encoding="ascii").strip())
    if len(raw) != 32:
        raise SystemExit("private key is not a 32-byte Ed25519 key")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--issued", default=time.strftime("%Y-%m"))
    parser.add_argument("--sku", default=SKU)
    args = parser.parse_args()
    if args.count < 1 or args.count > 100_000:
        raise SystemExit("count must be between 1 and 100000")

    private = _load_private_key()
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    seen: set[str] = set()
    records: list[dict[str, object]] = []
    for _ in range(args.count):
        code = generate_code()
        while code in seen:
            code = generate_code()
        seen.add(code)
        code_id = normalize_code(code)  # canonical form == the code itself
        entitlement = Entitlement(
            version=ENTITLEMENT_VERSION, code_id=code_id, sku=args.sku,
            issued=args.issued,
        )
        signature = private.sign(canonical_bytes(entitlement))
        records.append(
            {
                "code": code,
                "entitlement": entitlement.to_dict(),
                "signature": base64.b64encode(signature).decode("ascii"),
                "status": "unused",           # unused -> sold -> redeemed
                "sold_at": None,
                "redeemed_email": None,
            }
        )

    ledger_path = LEDGER_DIR / f"codes_batch_{stamp}.ledger.json"
    ledger_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    codes_path = LEDGER_DIR / f"codes_batch_{stamp}.txt"
    codes_path.write_text("\n".join(r["code"] for r in records) + "\n", encoding="utf-8")

    sql_lines = [
        "-- Seed for the activation server D1 database. PRIVATE.",
        "-- Each row is a claimable code with its pre-signed entitlement.",
    ]
    for record in records:
        entitlement_json = _sql_escape(json.dumps(record["entitlement"], sort_keys=True))
        sql_lines.append(
            "INSERT INTO codes (code_id, entitlement_json, signature, claimed, "
            "claimed_device, claimed_at) VALUES ("
            f"'{_sql_escape(str(record['code']))}', "
            f"'{entitlement_json}', "
            f"'{_sql_escape(str(record['signature']))}', "
            "0, NULL, NULL);"
        )
    sql_path = LEDGER_DIR / f"seed_codes_{stamp}.sql"
    sql_path.write_text("\n".join(sql_lines) + "\n", encoding="utf-8")

    # The workbook you actually work from when someone pays. Written in the
    # same run as the ledger and the seed, so the three cannot disagree about
    # which codes exist.
    from distribution.tools.keys_workbook import build_from_records

    workbook_path = LEDGER_DIR / f"activation_keys_{stamp}.xlsx"
    build_from_records(records, workbook_path, f"{args.issued} · {args.count} codes")

    print(f"Minted {args.count} codes (sku={args.sku}, issued={args.issued}).")
    print(f"  ledger : {ledger_path}")
    print(f"  codes  : {codes_path}")
    print(f"  D1 seed: {sql_path}")
    print(f"  TRACKER: {workbook_path}   <- the one you open when you make a sale")
    print()
    print("All four are under distribution/ledger/ and gitignored. Never commit them.")
    print("Upload the seed to D1:  wrangler d1 execute pitwall-licenses "
          f"--file {sql_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
