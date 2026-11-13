"""Fetch hash-pinned Android NumPy wheels from Flet's public package index.

Chaquopy's NumPy 1.26 wheel pulls an old x86_64 OpenBLAS library which cannot
load on 16 KB devices. Flet publishes both CPython 3.13 Android ABIs with
16 KB-aligned native extensions. Keep the exact URLs and SHA-256 digests here
so a changed upstream artifact cannot silently enter a release.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from urllib.request import urlopen


HERE = Path(__file__).resolve().parent
WHEELS = HERE / "wheels"
SOURCES = (
    (
        "numpy-2.4.6-1-cp313-cp313-android_24_arm64_v8a.whl",
        "https://pypi.flet.dev/-/ver_sY3wQ/numpy-2.4.6-1-cp313-cp313-android_24_arm64_v8a.whl",
        "928a3815944ef568d58dd45b9b00f87fb42a479ae910a1e46b741f9cb639fbe1",
    ),
    (
        "numpy-2.4.6-1-cp313-cp313-android_24_x86_64.whl",
        "https://pypi.flet.dev/-/ver_10WJaz/numpy-2.4.6-1-cp313-cp313-android_24_x86_64.whl",
        "71f92eb98f7b1057e9bf069e95ed7c066adb93d0f055cab4ca876a4ce753eb97",
    ),
    (
        "flet_libcpp_shared-27.2.12479018-1-py3-none-android_24_arm64_v8a.whl",
        "https://pypi.flet.dev/-/ver_1L92GE/flet_libcpp_shared-27.2.12479018-1-py3-none-android_24_arm64_v8a.whl",
        "671a4f38410e6268c336d6f8ac86ef3ff6df16549805f13e4560dad477a01bbf",
    ),
    (
        "flet_libcpp_shared-27.2.12479018-1-py3-none-android_24_x86_64.whl",
        "https://pypi.flet.dev/-/ver_1D0Tnf/flet_libcpp_shared-27.2.12479018-1-py3-none-android_24_x86_64.whl",
        "f3af52efe0cc613bdfd8deff78ca725e732d9f21bbdfccda901193d3ffd892e8",
    ),
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    WHEELS.mkdir(exist_ok=True)
    for name, url, expected in SOURCES:
        target = WHEELS / name
        if target.exists():
            if digest(target) != expected:
                raise RuntimeError(f"Existing wheel does not match pinned SHA-256: {name}")
            print(f"Verified {name}")
            continue
        pending = target.with_suffix(".download")
        try:
            with urlopen(url, timeout=120) as source, pending.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            if digest(pending) != expected:
                raise RuntimeError(f"Downloaded wheel does not match pinned SHA-256: {name}")
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)
        print(f"Downloaded and verified {name}")


if __name__ == "__main__":
    main()
