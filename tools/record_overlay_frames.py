"""Record the product's own OBS overlay as a transparent PNG sequence.

    python -m tools.record_overlay_frames --out marketing/videos/overlay-frames \
        --seconds 48 --fps 12

The page is ``static/overlay.html`` served by the running application, driven by
the same live WebSocket a streamer's browser source uses. Nothing is redrawn or
restyled for the recording: what lands in the frames is the overlay itself, on a
transparent background, so ``tools.cut_social_video`` can composite it over real
gameplay footage without anyone rebuilding the panel in a video editor.

Point it at a session that is actually running -- a real one, or
``tools/replay_demo.py`` -- and record the window you want. The numbers, the
strategy call and the radio line are whatever the application computed while the
capture was open.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.capture_screens import Devtools, browser_flags, find_browser  # noqa: E402

DEBUG_PORT = 9345


async def open_page(port: int) -> str:
    """Ask headless Chrome for a page target.

    Headless starts with no tab of its own, so waiting for one to appear in
    /json/list waits forever.
    """
    for _ in range(60):
        await asyncio.sleep(0.5)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                created = (
                    await client.put(
                        f"http://127.0.0.1:{port}/json/new?url=about:blank"
                    )
                ).json()
            return str(created["webSocketDebuggerUrl"])
        except Exception:  # noqa: BLE001 - the browser is still starting
            continue
    raise SystemExit("the browser never exposed a page target")


async def record(base: str, out: Path, seconds: float, fps: float, width: int,
                 height: int, scale: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="pitbox-overlay-"))
    browser = subprocess.Popen(
        [
            find_browser(),
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            "--hide-scrollbars",
            *browser_flags(),
            "--disable-gpu",
            "--no-first-run",
            # Without this the page is composited onto opaque white and the
            # captured PNGs have no usable alpha.
            "--default-background-color=00000000",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        target = await open_page(DEBUG_PORT)
        async with websockets.connect(target, max_size=64 * 1024 * 1024) as socket:
            devtools = Devtools(socket)
            await devtools.call("Page.enable")
            await devtools.call("Runtime.enable")
            await devtools.call(
                "Emulation.setDeviceMetricsOverride",
                width=width, height=height, deviceScaleFactor=scale, mobile=False,
            )
            await devtools.call(
                "Emulation.setDefaultBackgroundColorOverride",
                color={"r": 0, "g": 0, "b": 0, "a": 0},
            )
            await devtools.call("Page.navigate", url=f"{base}/overlay")
            # The overlay renders placeholders until the first state arrives.
            await asyncio.sleep(4.0)

            interval = 1.0 / fps
            started = time.monotonic()
            total = int(seconds * fps)
            for index in range(total):
                shot = await devtools.call(
                    "Page.captureScreenshot", format="png", fromSurface=True,
                    captureBeyondViewport=False,
                )
                (out / f"ov_{index:05d}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
                delay = started + (index + 1) * interval - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            print(f"{total} overlay frames -> {out}")
    finally:
        browser.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=36.0)
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--width", type=int, default=380)
    parser.add_argument("--height", type=int, default=560)
    parser.add_argument(
        "--scale", type=int, default=3,
        help="device pixel ratio; 3 keeps the panel sharp when it is scaled up",
    )
    arguments = parser.parse_args()
    asyncio.run(
        record(arguments.base, arguments.out, arguments.seconds, arguments.fps,
               arguments.width, arguments.height, arguments.scale)
    )


if __name__ == "__main__":
    main()
