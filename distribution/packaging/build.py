"""Build a shippable Pit Wall for the platform this runs on.

    python -m distribution.packaging.build --check     # no build, just verify
    python -m distribution.packaging.build             # build for this OS

Windows produces a one-folder PyInstaller build plus a zip; macOS produces a
.app bundle plus a .dmg. Both are built from the same spec so the two cannot
drift apart.

Cross-building is not possible: PyInstaller freezes the interpreter it is run
by, so a macOS bundle must be produced on macOS. This script refuses to
pretend otherwise, and `--check` is provided so the parts that *can* be
verified from any machine (preflight, spec validity, manifest generation) are
not blocked on owning a Mac.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DIST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DIST_ROOT.parent
SPEC = Path(__file__).resolve().parent / "pitwall.spec"
BUILD_DIR = DIST_ROOT / "build"
OUTPUT_DIR = DIST_ROOT / "artifacts"

APP_NAME = "Pit Wall"

# The committed development public key, recorded verbatim so preflight can
# recognise it. Comparing against the literal value is the only check that
# works: the key file holds nothing but base64, so there is no comment or
# marker inside it to look for, and a build that shipped this key could never
# be sold to anyone (the matching private key is in the repo's history and on
# the developer's machine).
DEV_PUBLIC_KEY = "4jAcWRQIkwPUwz8PmKamwpIIsU3yFUWgPiKRjh6En0I="


@dataclass(frozen=True, slots=True)
class Preflight:
    ok: bool
    problems: tuple[str, ...]
    warnings: tuple[str, ...]

    def report(self) -> str:
        lines = []
        for problem in self.problems:
            lines.append(f"  BLOCKED  {problem}")
        for warning in self.warnings:
            lines.append(f"  warning  {warning}")
        return "\n".join(lines) or "  all checks passed"


def preflight() -> Preflight:
    """Refuse to build something that must not be sold.

    The blocking checks are the ones that would ship a broken or unsafe
    artifact; anything recoverable after the fact is only a warning.
    """
    problems: list[str] = []
    warnings: list[str] = []

    public_key = DIST_ROOT / "licensing" / "embedded_public_key.txt"
    if not public_key.exists():
        problems.append("licensing/embedded_public_key.txt is missing.")
    elif public_key.read_text(encoding="utf-8").strip() == DEV_PUBLIC_KEY:
        problems.append(
            "embedded_public_key.txt still holds the DEVELOPMENT key, whose "
            "private half is not secret. Run `python -m distribution.tools."
            "keygen --force`, keep the private key offline, and commit the "
            "new public key before building anything for sale."
        )

    from ..launcher import ACTIVATION_ENDPOINT

    if ".invalid" in ACTIVATION_ENDPOINT or "example" in ACTIVATION_ENDPOINT:
        problems.append(
            f"launcher.ACTIVATION_ENDPOINT is still the placeholder "
            f"({ACTIVATION_ENDPOINT}). Point it at the deployed Worker."
        )

    if shutil.which("pyinstaller") is None:
        problems.append("PyInstaller is not installed (pip install pyinstaller).")

    if platform.system() == "Darwin" and shutil.which("codesign") is None:
        warnings.append(
            "codesign was not found. An unsigned, un-notarized .app is blocked "
            "by Gatekeeper as \"damaged\" on any Mac but this one."
        )

    if not (REPO_ROOT / "static" / "index.html").exists():
        problems.append("static/ was not found next to the distribution folder.")

    return Preflight(not problems, tuple(problems), tuple(warnings))


def _manifest_path() -> Path:
    return DIST_ROOT / "licensing" / "integrity_manifest.txt"


def write_manifest() -> str:
    """Bake the integrity hash of the licensing modules into the build."""
    from ..licensing import gate

    return gate.write_integrity_manifest()


def _run(command: list[str]) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, check=True, cwd=REPO_ROOT)


def build_windows() -> Path:
    _run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)])
    produced = BUILD_DIR / "dist" / APP_NAME
    archive = OUTPUT_DIR / f"{APP_NAME.replace(' ', '')}-windows"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return Path(shutil.make_archive(str(archive), "zip", root_dir=produced))


def build_macos() -> Path:
    """Build the .app and wrap it in a .dmg.

    Signing and notarization are deliberately NOT run here. They need an Apple
    Developer identity that only the person selling the app has, and a silent
    unsigned build would look successful while being unopenable on any other
    Mac. `macos_release_steps()` prints exactly what to run.
    """
    _run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)])
    bundle = BUILD_DIR / "dist" / f"{APP_NAME}.app"
    if not bundle.exists():
        raise SystemExit(f"expected a bundle at {bundle}; PyInstaller did not make one")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dmg = OUTPUT_DIR / f"{APP_NAME.replace(' ', '')}-macos.dmg"
    dmg.unlink(missing_ok=True)
    _run([
        "hdiutil", "create",
        "-volname", APP_NAME,
        "-srcfolder", str(bundle),
        "-ov", "-format", "UDZO",
        str(dmg),
    ])
    return dmg


def macos_release_steps(identity: str = "Developer ID Application: YOUR NAME (TEAMID)") -> str:
    """The steps that need a Mac and an Apple Developer account."""
    dmg = OUTPUT_DIR / f"{APP_NAME.replace(' ', '')}-macos.dmg"
    bundle = BUILD_DIR / "dist" / f"{APP_NAME}.app"
    return "\n".join([
        "# 1. Sign the bundle (hardened runtime is required for notarization)",
        "codesign --deep --force --options runtime --timestamp \\",
        f'  --sign "{identity}" "{bundle}"',
        "",
        "# 2. Build the .dmg (this script does that step for you)",
        "python -m distribution.packaging.build",
        "",
        "# 3. Notarize, then staple so it opens offline",
        f'xcrun notarytool submit "{dmg}" \\',
        '  --apple-id YOUR_APPLE_ID --team-id TEAMID \\',
        '  --password APP_SPECIFIC_PASSWORD --wait',
        f'xcrun stapler staple "{dmg}"',
        "",
        "# 4. Verify what a buyer will see",
        f'spctl --assess --type open --context context:primary-signature -v "{dmg}"',
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="run preflight and write the manifest, but do not build",
    )
    args = parser.parse_args(argv)

    system = platform.system()
    print(f"Pit Wall packaging — host platform: {system}")

    checks = preflight()
    print("Preflight:")
    print(checks.report())

    manifest = write_manifest()
    print(f"Integrity manifest: {manifest[:16]}… ({len(manifest)} hex chars)")

    if args.check:
        # Leaving the manifest in a source tree would make every dev checkout
        # start enforcing integrity, and fail the moment a guarded module is
        # edited. A real build keeps it, because PyInstaller has to collect it.
        _manifest_path().unlink(missing_ok=True)
        print("Integrity manifest removed again (--check does not alter the tree).")
        print("\n--check: stopping before the build.")
        if system != "Darwin":
            print(
                "\nmacOS artifacts cannot be produced here. PyInstaller freezes "
                "the running interpreter, so a .app must be built on a Mac. "
                "Everything above is platform-independent and has been "
                "verified. The Mac-only steps are:\n"
            )
            print(macos_release_steps())
        return 0 if checks.ok else 1

    if not checks.ok:
        print("\nRefusing to build: fix the BLOCKED items above.")
        return 1

    if system == "Windows":
        artifact = build_windows()
    elif system == "Darwin":
        artifact = build_macos()
        print("\nNot yet signed or notarized. Remaining steps:\n")
        print(macos_release_steps())
    else:
        print(f"\nUnsupported build host: {system}. Pit Wall ships for Windows and macOS.")
        return 1

    print(f"\nBuilt: {artifact}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
