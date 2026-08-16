"""Capture documentation screenshots from the running application.

Drives a headless Chrome over the DevTools Protocol so a screen that needs a
selection - Session Review and Lap Lab both require a session to be chosen -
can be put into a real, populated state before the frame is captured. Every
pixel comes from the actual rendered UI; nothing here mocks markup.

    python -m tools.capture_screens --out docs/screenshots --base http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)

# Executables to look for on PATH, and the roots a Playwright/Puppeteer
# install drops a browser into. Capture is developed on Windows but has to
# run on the Linux machines that cut the marketing footage, where Chrome is
# rarely installed system-wide.
CHROME_ON_PATH = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge",
)
CHROME_SEARCH_ROOTS = (
    "/opt/pw-browsers",
    str(Path.home() / ".cache" / "ms-playwright"),
    str(Path.home() / ".cache" / "puppeteer"),
)


def browser_flags() -> list[str]:
    """Extra Chrome flags this machine needs.

    Chrome refuses to start its sandbox as root, which is how a container
    build agent runs. Dropping the sandbox is safe here and only here: the
    only page ever loaded is our own dashboard on loopback.
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return ["--no-sandbox", "--disable-dev-shm-usage"]
    return []


def find_browser() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in CHROME_ON_PATH:
        found = shutil.which(name)
        if found:
            return found
    # A downloaded browser bundle: chrome-linux/chrome, chrome-mac/…, and the
    # headless shell, which drives CDP identically and is all a capture needs.
    for root in CHROME_SEARCH_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        for pattern in ("*/chrome-*/chrome", "*/chrome-*/headless_shell"):
            for found in sorted(base.glob(pattern)):
                if found.is_file():
                    return str(found)
    raise SystemExit("no Chrome or Edge binary found for screenshot capture")


class Devtools:
    def __init__(self, websocket) -> None:
        self.websocket = websocket
        self._id = 0

    async def call(self, method: str, **params):
        self._id += 1
        message_id = self._id
        await self.websocket.send(
            json.dumps({"id": message_id, "method": method, "params": params})
        )
        while True:
            raw = json.loads(await self.websocket.recv())
            if raw.get("id") == message_id:
                if "error" in raw:
                    raise RuntimeError(f"{method}: {raw['error']}")
                return raw.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.call(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=True,
            returnByValue=True,
        )
        return result.get("result", {}).get("value")

    async def goto(self, url: str, settle: float = 3.0) -> None:
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(settle)

    async def screenshot(self, path: Path, width: int, height: int) -> None:
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=False,
        )
        await asyncio.sleep(0.4)
        shot = await self.call("Page.captureScreenshot", format="png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(shot["data"]))
        print(f"  wrote {path.name} ({path.stat().st_size:,} bytes)")


SELECT_FIRST_SESSION = """
(async () => {
  const sel = document.getElementById('%(select)s');
  if (!sel) return 'no select';
  const option = [...sel.options].find(o => o.value && o.value.startsWith('ses_'));
  if (!option) return 'no session option';
  sel.value = option.value;
  sel.dispatchEvent(new Event('change', {bubbles: true}));
  return option.value;
})()
"""


async def capture(
    base: str, out: Path, browser_path: str, only: set[str] | None = None
) -> None:
    def wanted(group: str) -> bool:
        return not only or group in only

    profile = Path(tempfile.mkdtemp(prefix="pitwall_shots_"))
    process = subprocess.Popen(
        [
            browser_path,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            *browser_flags(),
            "--remote-debugging-port=9333",
            f"--user-data-dir={profile}",
            "--window-size=1600,1080",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Attach to a page target, not the browser target: the browser-level
        # endpoint does not implement Page or Runtime.
        endpoint = None
        for _ in range(60):
            try:
                targets = httpx.get("http://127.0.0.1:9333/json/list", timeout=1.0).json()
                pages = [
                    item for item in targets
                    if item.get("type") == "page" and item.get("webSocketDebuggerUrl")
                ]
                if pages:
                    endpoint = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:  # noqa: BLE001 - browser is still starting
                pass
            time.sleep(0.5)
        if not endpoint:
            raise SystemExit("headless browser did not expose a page debugger endpoint")

        async with websockets.connect(endpoint, max_size=64 * 1024 * 1024) as socket:
            devtools = Devtools(socket)
            await devtools.call("Page.enable")
            await devtools.call("Runtime.enable")

            if wanted("live"):
                print("capturing live screens")
                await devtools.goto(f"{base}/#live", settle=6.0)
                await devtools.screenshot(out / "01-drive-live-command-center.png", 1600, 1080)
                await devtools.screenshot(out / "02-drive-radio-feed.png", 1600, 1560)

                await devtools.goto(f"{base}/#connection", settle=5.0)
                await devtools.screenshot(out / "03-connection-center.png", 1600, 1080)

            if wanted("library"):
                await devtools.goto(f"{base}/#library", settle=5.0)
                await devtools.screenshot(out / "04-library.png", 1600, 1080)

            if not wanted("analysis"):
                return
            print("capturing analysis screens (selecting a session first)")
            await devtools.goto(f"{base}/#session-review", settle=4.0)
            picked = await devtools.evaluate(
                SELECT_FIRST_SESSION % {"select": "reviewSessionSelect"}
            )
            print(f"  session-review selected: {picked}")
            await asyncio.sleep(7.0)
            await devtools.screenshot(out / "05-session-review.png", 1600, 1200)

            await devtools.goto(f"{base}/#field", settle=4.0)
            picked = await devtools.evaluate(
                SELECT_FIRST_SESSION % {"select": "fieldSessionSelect"}
            )
            print(f"  field selected: {picked}")
            await asyncio.sleep(7.0)
            await devtools.screenshot(out / "06-field-lab.png", 1600, 1200)

            # Lap Lab needs a candidate and a reference, then a comparison run.
            await devtools.goto(f"{base}/#lap-lab", settle=4.0)
            detail = await devtools.evaluate(LAP_LAB_SETUP)
            print(f"  lap-lab: {detail}")
            await asyncio.sleep(10.0)
            await devtools.screenshot(out / "07-lap-lab.png", 1600, 1400)
    finally:
        process.terminate()
        shutil.rmtree(profile, ignore_errors=True)


LAP_LAB_SETUP = """
(async () => {
  const fire = (el) => el.dispatchEvent(new Event('change', {bubbles: true}));
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const review = document.getElementById('reviewSessionSelect');
  if (review) {
    const option = [...review.options].find(o => o.value && o.value.startsWith('ses_'));
    if (option) { review.value = option.value; fire(review); }
  }
  await wait(6000);
  const cand = document.getElementById('candidateLapSelect');
  if (!cand) return 'no candidate select';
  const candOption = [...cand.options].find(o => o.value && o.value.startsWith('lap_'));
  if (!candOption) return 'no candidate laps';
  cand.value = candOption.value; fire(cand);
  await wait(5000);
  const ref = document.getElementById('referenceLapSelect');
  const refOption = ref ? [...ref.options].find(o => o.value && o.value.startsWith('lap_')) : null;
  if (!refOption) return 'candidate set, no reference available';
  ref.value = refOption.value; fire(ref);
  await wait(1500);
  const button = [...document.querySelectorAll('button')]
    .find(b => /compare laps/i.test(b.innerText));
  if (button) button.click();
  return 'candidate ' + candOption.value + ' vs reference ' + refOption.value;
})()
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--only", default="", help="live,library,analysis")
    args = parser.parse_args()
    out = Path(args.out)
    groups = {g.strip() for g in args.only.split(",") if g.strip()} or None
    asyncio.run(capture(args.base, out, find_browser(), groups))
    print(f"\nscreenshots written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
