#!/usr/bin/env python3
"""Reject incomplete/corrupt Android wheel caches before invoking Gradle.

A package cached for a phone alone does not satisfy an x86_64 emulator build.
Only CPython 3.13 wheels usable on our minimum Android API 24 count.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import zipfile

HERE = Path(__file__).resolve().parent


def usable_wheel(path: Path, package: str, arch: str) -> bool:
    name, version = package.split("==", 1)
    prefix = f"{name.replace('-', '_')}-{version}-"
    if not path.name.startswith(prefix):
        return False
    match = re.search(r"-cp313-(?:cp313|abi3)-android_(\d+)_" + re.escape(arch) + r"\.whl$", path.name)
    if not match or int(match.group(1)) > 24:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None and any(
                member.endswith(".dist-info/WHEEL") for member in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile):
        return False


def missing_wheels(directory: Path, packages: list[str], archs: list[str]) -> list[str]:
    wheels = list(directory.glob("*.whl"))
    return [
        f"{package} ({arch}, CPython 3.13, Android API <=24)"
        for package in packages
        for arch in archs
        if not any(usable_wheel(path, package, arch) for path in wheels)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=HERE / "wheels")
    parser.add_argument("--package", action="append")
    parser.add_argument("--archs", nargs="+", choices=["arm64_v8a", "x86_64"], default=["arm64_v8a", "x86_64"])
    args = parser.parse_args()
    packages = args.package or [
        line.strip() for line in (HERE / "native-requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    missing = missing_wheels(args.directory, packages, args.archs)
    if missing:
        print("Missing or unusable Android wheels:\n" + "\n".join(missing))
        return 1
    print(f"Verified {len(packages)} native packages for {', '.join(args.archs)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
