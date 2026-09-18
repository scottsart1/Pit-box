"""Publish notifications after hashing the public download. Credentials use env only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import httpx

API = "https://pitwall-activation.sarthakvij123450.workers.dev"
ROUTES = {"windows": "/installer", "android": "/android"}
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\Z")


def fingerprint(path: Path) -> tuple[int, str]:
    size, digest = 0, hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def publish(client: httpx.Client, *, platform: str, version: str, size: int,
            sha256: str, notes: str, token: str, announce: bool = True) -> dict:
    if (platform not in ROUTES or not VERSION.fullmatch(version)
            or not 1 <= size <= 2_000_000_000 or not re.fullmatch(r"[a-f0-9]{64}", sha256)
            or not notes.strip() or len(notes) > 4000 or not 32 <= len(token) <= 256):
        raise ValueError("Invalid release metadata or missing publication credential")
    # Range avoids inflating download-start counters; never forward credentials here.
    total, digest = 0, hashlib.sha256()
    with client.stream("GET", API + ROUTES[platform], headers={"Range": "bytes=0-", "Accept-Encoding": "identity"}, follow_redirects=False) as response:
        if response.status_code != 206 or response.headers.get("content-range") != f"bytes 0-{size - 1}/{size}":
            raise ValueError("Public artifact range/size does not match this release")
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > size:
                raise ValueError("Public artifact is larger than this release")
            digest.update(chunk)
    if total != size or digest.hexdigest() != sha256:
        raise ValueError("Public artifact checksum does not match; no announcement published")
    site = client.get("https://yourpitbox.com/", follow_redirects=False)
    if site.status_code != 200 or sha256 not in site.text or f'id="{platform}-release"' not in site.text or version not in site.text:
        raise ValueError("Production website does not advertise this verified release yet")
    result = client.post(API + "/release-admin/publish", follow_redirects=False,
                         headers={"Authorization": "Bearer " + token},
                         json={"platform": platform, "version": version, "size": size,
                               "sha256": sha256, "notes": notes, "announce": announce})
    if result.status_code != 200:
        raise ValueError(f"Release publication rejected (HTTP {result.status_code}); retry with identical metadata")
    data = result.json()
    if data.get("ok") is not True or data.get("release", {}).get("sha256") != sha256:
        raise ValueError("Release publication response could not be verified")
    return {"platform": platform, "version": version, "sha256": sha256,
            "size": size, "email": data.get("email")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=ROUTES, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--no-email", action="store_true", help="Publish metadata without queuing subscriber emails")
    args = parser.parse_args()
    try:
        size, sha256 = fingerprint(args.artifact)
        notes = args.notes_file.read_text(encoding="utf-8").strip() if args.notes_file else f"Your Pit Box {args.version} is available for {args.platform.title()}. Visit the download page for release and installation details."
        with httpx.Client(timeout=60, trust_env=False, follow_redirects=False) as client:
            data = publish(client, platform=args.platform, version=args.version, size=size, sha256=sha256,
                           notes=notes, token=os.environ.get("PITBOX_RELEASE_PUBLISH_TOKEN", ""), announce=not args.no_email)
        print(json.dumps(data))
        if data["email"] == "not_configured":
            print("Version published. Email is DISABLED: configure a verified sender before announcements can be sent.", file=sys.stderr)
        return 0
    except (OSError, ValueError, httpx.HTTPError):
        # Never echo a credential or provider response from an exception.
        print("Release notification publication failed. Check artifact, website, metadata and private key; no successful announcement is claimed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
