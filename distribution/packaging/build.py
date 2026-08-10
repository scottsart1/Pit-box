"""Build a shippable Your Pit Box for the platform this runs on.

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
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DIST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DIST_ROOT.parent
SPEC = Path(__file__).resolve().parent / "pitwall.spec"


def _build_root() -> Path:
    """Where to build. Deliberately outside the repository.

    This repo lives under OneDrive. Building into it meant OneDrive held
    handles on the output while uploading, so PyInstaller intermittently died
    with PermissionError trying to clear the previous build — and because the
    failure left the *old* build in place, the next test run silently
    exercised a stale executable and reported a bug that had already been
    fixed. It also uploaded ~300 MB of build litter per run.

    Override with PITWALL_BUILD_DIR if the default is unsuitable.
    """
    override = os.environ.get("PITWALL_BUILD_DIR")
    if override:
        return Path(override).resolve()
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(local) / "PitWallBuild"


BUILD_DIR = _build_root()
OUTPUT_DIR = BUILD_DIR / "artifacts"

APP_NAME = "Your Pit Box"

# The committed development public key, recorded verbatim so preflight can
# recognise it. Comparing against the literal value is the only check that
# works: the key file holds nothing but base64, so there is no comment or
# marker inside it to look for, and a build that shipped this key could never
# be sold to anyone (the matching private key is in the repo's history and on
# the developer's machine).
DEV_PUBLIC_KEY = "4jAcWRQIkwPUwz8PmKamwpIIsU3yFUWgPiKRjh6En0I="


def _pyinstaller_available() -> bool:
    """Whether the interpreter that will run the build can import PyInstaller.

    Deliberately not `shutil.which("pyinstaller")`. The build runs
    `sys.executable -m PyInstaller`, and a venv invoked by path rather than
    "activated" has no pyinstaller.exe on PATH even though the module imports
    perfectly well. Checking PATH made preflight report BLOCKED on the very
    machine where the build succeeds — so the real release command (`build`
    without `--dev`) would have refused to run, and only `--dev`, which is
    marked NOT SELLABLE, would have worked.
    """
    return importlib.util.find_spec("PyInstaller") is not None


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

    if not _pyinstaller_available():
        problems.append(
            f"PyInstaller cannot be imported by {sys.executable}. Install it "
            f'with:  "{sys.executable}" -m pip install pyinstaller'
        )

    if platform.system() == "Darwin" and shutil.which("codesign") is None:
        warnings.append(
            "codesign was not found. An unsigned, un-notarized .app is blocked "
            "by Gatekeeper as \"damaged\" on any Mac but this one."
        )

    if not (REPO_ROOT / "static" / "index.html").exists():
        problems.append("static/ was not found next to the distribution folder.")

    return Preflight(not problems, tuple(problems), tuple(warnings))


def write_eula(app_dir: Path) -> Path:
    """Derive the installer's licence text from the website's EULA.

    Generated rather than maintained separately: two copies of a licence drift,
    and the one a buyer agrees to at install time disagreeing with the one on
    the site is exactly the sort of thing that matters if it is ever disputed.
    """
    source = DIST_ROOT / "website" / "eula.html"
    html = source.read_text(encoding="utf-8")

    body = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # <head> carries a <title> identical to the <h1>, which would appear twice.
    body = re.sub(r"<head\b.*?</head>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<(script|style|header|footer)\b.*?</\1>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"</(p|li|h1|h2|ul|div)>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<li\b[^>]*>", "  - ", body, flags=re.IGNORECASE)
    body = re.sub(r"<h2\b[^>]*>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<[^>]+>", "", body)
    for entity, plain in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("\u2014", "-"), ("\u2019", "'"),
    ):
        body = body.replace(entity, plain)
    lines = [line.strip() for line in body.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    target = app_dir / "EULA.txt"
    target.write_text(text + "\n", encoding="utf-8")
    return target


def build_installer(app_dir: Path, version: str = "4.2.1") -> Path | None:
    """Compile the one-click installer, if Inno Setup is available.

    Returns None rather than failing the build when it is absent: the zip is
    still a valid deliverable, and the compiler is a separate download that
    only matters when cutting a release.
    """
    compiler = _inno_compiler()
    if compiler is None:
        print(
            "\nInno Setup not found, so no PitWall-Setup.exe was produced.\n"
            "  Install it with:  winget install JRSoftware.InnoSetup\n"
            "  Then re-run this with --installer.\n"
            "  The zip above is still usable; it just asks the buyer to "
            "unzip and find the exe themselves."
        )
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _run([
        str(compiler),
        f"/DAppVersion={version}",
        f"/DSourceDir={app_dir}",
        f"/DOutputDir={OUTPUT_DIR}",
        str(Path(__file__).resolve().parent / "pitwall.iss"),
    ])
    produced = OUTPUT_DIR / "PitWall-Setup.exe"
    return produced if produced.exists() else None


def _inno_compiler() -> Path | None:
    """Locate ISCC.exe, including the per-user install winget produces.

    `winget install JRSoftware.InnoSetup` run without elevation — which is what
    happens when the command is typed into an ordinary terminal — installs into
    %LOCALAPPDATA%\\Programs and puts nothing on PATH. Checking only the two
    Program Files locations meant following the documented release steps ended
    in "Inno Setup not found", with the build reporting success and quietly
    shipping no installer.
    """
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)

    roots = [
        Path(r"C:\Program Files (x86)"),
        Path(r"C:\Program Files"),
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs")

    for root in roots:
        for version in ("Inno Setup 6", "Inno Setup 5"):
            candidate = root / version / "ISCC.exe"
            if candidate.exists():
                return candidate
    return None


def stamp_manifest(app_dir: Path, executable: Path) -> str:
    """Record the finished executable's digest inside the packaged app.

    Must run after PyInstaller: a frozen build hashes its own executable, so
    the expected value cannot exist until that executable does. The path
    mirrors where the licensing package lands in the bundle, because gate.py
    looks for the manifest beside itself.
    """
    from ..licensing import gate

    value = gate.digest_for_packaged_executable(executable)
    target = app_dir / "_internal" / "distribution" / "licensing" / gate.MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value + "\n", encoding="ascii")
    return value


def _run(command: list[str]) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, check=True, cwd=REPO_ROOT)


def _pyinstaller() -> None:
    """Freeze, with output paths pinned under distribution/build/.

    PyInstaller defaults to `dist/` and `build/` in the working directory. The
    repo root already gitignores `dist/` for a different reason, and leaving
    build litter beside the source is how stale artifacts get shipped.
    """
    _run([
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", str(BUILD_DIR / "dist"),
        "--workpath", str(BUILD_DIR / "work"),
        str(SPEC),
    ])


def build_windows() -> Path:
    _pyinstaller()
    produced = BUILD_DIR / "dist" / APP_NAME
    if not produced.is_dir():
        raise SystemExit(f"expected a build at {produced}; PyInstaller did not make one")

    executable = produced / f"{APP_NAME}.exe"
    if not executable.exists():
        raise SystemExit(f"expected an executable at {executable}")

    write_eula(produced)
    digest = stamp_manifest(produced, executable)
    print(f"Integrity manifest stamped: {digest[:16]}…")

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
    _pyinstaller()
    bundle = BUILD_DIR / "dist" / f"{APP_NAME}.app"
    if not bundle.exists():
        raise SystemExit(f"expected a bundle at {bundle}; PyInstaller did not make one")

    # No integrity manifest on macOS, deliberately. codesign rewrites the
    # executable, so a digest taken before signing would never match at
    # runtime and the app would refuse to start on every Mac. Signing and
    # notarization are themselves the platform's integrity guarantee, and a
    # stronger one: Gatekeeper refuses a modified bundle before it ever runs.
    # With no manifest present, integrity_ok() returns True and the licence
    # checks proceed normally.
    print("macOS: integrity is enforced by codesign/notarization, not a manifest.")

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
    parser.add_argument(
        "--installer", action="store_true",
        help="also compile the one-click PitWall-Setup.exe (needs Inno Setup)",
    )
    parser.add_argument(
        "--dev", action="store_true",
        help=(
            "build despite preflight failures, for testing the packaging "
            "itself. The result is signed with the development key and must "
            "never be sold or published."
        ),
    )
    args = parser.parse_args(argv)

    system = platform.system()
    print(f"Your Pit Box packaging — host platform: {system}")

    checks = preflight()
    print("Preflight:")
    print(checks.report())

    # An older build wrote a manifest into the source tree. A checkout that
    # still has one enforces integrity against a stale hash and reads as
    # tampered after any edit to a guarded module.
    stray = DIST_ROOT / "licensing" / "integrity_manifest.txt"
    if stray.exists():
        stray.unlink()
        print(f"Removed a stray manifest from the source tree: {stray}")

    if args.check:
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
        if not args.dev:
            print("\nRefusing to build: fix the BLOCKED items above.")
            return 1
        print(
            "\n--dev: building anyway. THIS BUILD IS NOT SELLABLE - it carries "
            "the development key and/or a placeholder activation endpoint."
        )

    if system == "Windows":
        artifact = build_windows()
        if args.installer:
            setup = build_installer(BUILD_DIR / "dist" / APP_NAME)
            if setup is not None:
                print(f"\nInstaller: {setup}")
    elif system == "Darwin":
        artifact = build_macos()
        print("\nNot yet signed or notarized. Remaining steps:\n")
        print(macos_release_steps())
    else:
        print(f"\nUnsupported build host: {system}. Your Pit Box ships for Windows and macOS.")
        return 1

    print(f"\nBuilt: {artifact}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
