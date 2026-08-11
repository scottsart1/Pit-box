# PyInstaller spec shared by the Windows and macOS builds.
#
# One spec for both platforms so the two artifacts cannot drift: the only
# platform-specific part is the BUNDLE step at the bottom, which PyInstaller
# ignores off macOS.
#
# Run it through the build driver rather than directly, so preflight and the
# integrity manifest happen first:
#
#     python -m distribution.packaging.build

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
DIST_ROOT = SPEC_DIR.parent
REPO_ROOT = DIST_ROOT.parent

APP_NAME = "Your Pit Box"

datas = [
    # The dashboard is served from disk at runtime.
    (str(REPO_ROOT / "static"), "static"),
    # The public key the licence check verifies against, and the build-time
    # integrity manifest written by the driver.
    (str(DIST_ROOT / "licensing" / "embedded_public_key.txt"), "distribution/licensing"),
]

# The integrity manifest is NOT collected here. A frozen build hashes its own
# executable, so the value cannot be known until after PyInstaller has run --
# the build driver writes it into the finished app directory afterwards.

hiddenimports = [
    # Tk is only imported inside functions in first_run.py, so the analysis
    # cannot see it.
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    # uvicorn picks its loop and protocol implementations by name at runtime.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

analysis = Analysis(
    [str(DIST_ROOT / "main.py")],
    pathex=[str(REPO_ROOT), str(REPO_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # The signing key must never be reachable from a shipped build, even by
    # accident. Excluding the package is belt-and-braces: .secrets/ is not in
    # datas either.
    excludes=["distribution.tools", "pytest", "_pytest"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    # A console window would sit behind the dashboard for the whole race.
    console=False,
    disable_windowed_traceback=False,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        collect,
        name=f"{APP_NAME}.app",
        bundle_identifier="com.pitwall.desktop",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": "4.3.0",
            "NSHighResolutionCapable": True,
            # Your Pit Box listens for UDP telemetry on the local network, which
            # macOS gates behind a user prompt. Without this key the prompt
            # never appears and the socket silently receives nothing.
            "NSLocalNetworkUsageDescription":
                "Your Pit Box receives F1 telemetry from your console over the "
                "local network.",
            # No microphone entitlement is requested here: voice is optional
            # and the OS prompts on first use.
        },
    )
