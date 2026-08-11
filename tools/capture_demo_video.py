"""Record the marketing demo video from the running application.

Nothing here is staged. The frames are the real UI rendering real state that
arrived over a real UDP socket, and the engineer's words are whatever the real
engineer said when asked — this tool never writes dialogue. What it controls is
only the *questions*, which is the driver's side of the radio, and the timing of
what is on screen.

    python -m tools.capture_demo_video --base http://127.0.0.1:8010 --out docs/demo

The driver's questions are spoken, not typed: each is synthesized to audio and
posted to `/api/voice`, the same endpoint the browser's push-to-talk uses. That
runs the genuine path — transcription, intent routing, tool calls, the model,
then the app's own text-to-speech — so the reply is computed, not authored. The
only thing skipped is the microphone, because this machine has none.

Captions are injected into the page as a real DOM overlay rather than burned in
afterwards, so they are styled like the product and stay legible at any scale.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.capture_screens import find_browser  # noqa: E402

DEBUG_PORT = 9334
WIDTH, HEIGHT = 1600, 900


# --------------------------------------------------------------------------
# The storyboard
# --------------------------------------------------------------------------
#
# `ask` entries are the driver's side of the radio. They are questions, not
# answers: what comes back is whatever the engineer computes from the
# telemetry, and is not known when this file is written.


@dataclass(frozen=True, slots=True)
class Beat:
    caption: str
    seconds: float
    page: str | None = None
    ask: str | None = None
    sub: str = ""
    # Optional JavaScript run in the page when the beat starts, so a
    # storyboard can press the product's own buttons: build a race plan,
    # open a tab's inner view, start a comparison. The UI it drives is real.
    js: str | None = None
    # Trigger the app's own unsolicited call (POST /api/proactive/test) and
    # play what it actually spoke. The engineer speaks first; nothing is
    # scripted, and the driver never asked.
    proactive: bool = False


STORYBOARD: tuple[Beat, ...] = (
    Beat(
        page="live", seconds=8.0,
        caption="Hungaroring, lap 55 of 70",
        sub="Live telemetry arriving over UDP from the car. Every screen here is the real application.",
    ),
    Beat(
        seconds=8.0,
        caption="P4, on hards that are 22 laps old",
        sub="Around 50% worn with 16 laps to run. Enough to reach the flag — but not quickly.",
    ),
    Beat(
        seconds=9.0,
        caption="Antonelli is 3.6 seconds back on mediums 13 laps fresher",
        sub="Faster right now, and degrading faster too. Hungaroring is one of the hardest circuits to overtake on, which is what makes track position worth keeping.",
    ),
    Beat(
        seconds=2.0,
        ask="Mark, what is my tyre situation and how many laps are left?",
        caption="Asking over the radio",
        sub="Spoken, transcribed and answered by the running application. No dialogue was written for this video.",
    ),
    Beat(
        seconds=2.0,
        ask="Mark, should I box now or stay out and defend?",
        caption="The call the race turns on",
        sub="A stop costs 20.5 seconds and the position. Staying out costs pace for sixteen laps.",
    ),
    Beat(
        seconds=2.0,
        ask="Mark, can I hold Antonelli off if I stay out to the end?",
        caption="The case for staying out",
        sub="",
    ),
    Beat(
        page="live", seconds=9.0,
        caption="Three plans, separated by 1.6 seconds",
        sub="Ranked by projected finishing position, not elapsed time. When the options are this close, the reasoning matters more than the answer.",
    ),
    Beat(
        seconds=2.0,
        ask="Mark, what does boxing now cost me in track position?",
        caption="The case for stopping",
        sub="",
    ),
    Beat(
        page="field", seconds=9.0,
        caption="The whole field, not just your car",
        sub="Every rival's compound, tyre age and clean pace — read from the same packets the game already broadcasts.",
    ),
    Beat(
        seconds=2.0,
        ask="Mark, how quickly is Antonelli closing on me?",
        caption="Checking the threat behind",
        sub="",
    ),
    Beat(
        page="live", seconds=10.0,
        caption="Deterministic maths. The model only speaks it aloud.",
        sub="Your Pit Box — a race engineer for F1 26 on PS5.",
    ),
)


def load_storyboard(path: Path) -> tuple[Beat, ...]:
    """Load a storyboard from JSON, so one pipeline cuts many videos.

    Each entry mirrors Beat: caption, seconds, and optionally page, ask, sub.
    The hardcoded STORYBOARD remains the default for the original long demo.
    """
    entries = json.loads(path.read_text(encoding="utf-8"))
    beats = []
    for entry in entries:
        beats.append(
            Beat(
                caption=str(entry.get("caption", "")),
                seconds=float(entry.get("seconds", 4.0)),
                page=entry.get("page"),
                ask=entry.get("ask"),
                sub=str(entry.get("sub", "")),
                js=entry.get("js"),
                proactive=bool(entry.get("proactive", False)),
            )
        )
    if not beats:
        raise SystemExit(f"{path} contains no beats")
    return tuple(beats)


CAPTION_CSS = """
/* Solid, not translucent. A gradient let the timing tower show through and
   the eyebrow landed on top of a driver name. */
#pw-demo-caption{position:fixed;left:0;right:0;bottom:0;z-index:2147483647;
padding:26px 34px 30px;background:rgba(4,7,10,.97);border-top:1px solid rgba(255,255,255,.10);
box-shadow:0 -34px 46px -12px rgba(4,7,10,.92);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#fff;
opacity:0;transition:opacity .45s ease;pointer-events:none}
#pw-demo-caption.on{opacity:1}
#pw-demo-caption b{display:block;font-size:30px;font-weight:800;letter-spacing:-.02em;line-height:1.15}
#pw-demo-caption span{display:block;margin-top:8px;font-size:18px;font-weight:500;color:#c6cedb;max-width:78ch;line-height:1.4}
#pw-demo-caption i{display:inline-block;font-style:normal;font-size:12px;font-weight:800;letter-spacing:.12em;
text-transform:uppercase;color:#ff4d5f;margin-bottom:9px}
#pw-demo-radio{position:fixed;right:34px;top:28px;z-index:2147483647;max-width:560px;
padding:16px 20px;border-radius:14px;background:rgba(6,10,14,.94);border:1px solid rgba(255,255,255,.14);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#fff;
opacity:0;transition:opacity .4s ease;pointer-events:none;box-shadow:0 18px 40px -18px rgba(0,0,0,.8)}
#pw-demo-radio.on{opacity:1}
#pw-demo-radio em{display:block;font-style:normal;font-size:12px;font-weight:800;letter-spacing:.1em;
text-transform:uppercase;color:#7fd1a8;margin-bottom:7px}
#pw-demo-radio p{margin:0;font-size:17px;line-height:1.45}
#pw-demo-radio p+p{margin-top:10px;color:#c6cedb;font-size:15px}
"""

INSTALL_OVERLAY = """
(() => {
  if (document.getElementById('pw-demo-caption')) return 'already';
  const style = document.createElement('style');
  style.textContent = %(css)s;
  document.head.appendChild(style);
  const bar = document.createElement('div');
  bar.id = 'pw-demo-caption';
  bar.innerHTML = '<i>Your Pit Box</i><b></b><span></span>';
  document.body.appendChild(bar);
  const radio = document.createElement('div');
  radio.id = 'pw-demo-radio';
  radio.innerHTML = '<em>Radio</em><p class="q"></p><p class="a"></p>';
  document.body.appendChild(radio);
  return 'installed';
})()
"""

SET_CAPTION = """
(() => {
  const bar = document.getElementById('pw-demo-caption');
  if (!bar) return 'missing';
  bar.querySelector('b').textContent = %(title)s;
  bar.querySelector('span').textContent = %(sub)s;
  bar.classList.add('on');
  return 'ok';
})()
"""

SET_RADIO = """
(() => {
  const box = document.getElementById('pw-demo-radio');
  if (!box) return 'missing';
  box.querySelector('.q').textContent = %(q)s;
  box.querySelector('.a').textContent = %(a)s;
  box.classList.toggle('on', %(show)s);
  return 'ok';
})()
"""


class Devtools:
    """A CDP client that keeps events, which the screenshot tool discards."""

    def __init__(self, websocket) -> None:
        self.websocket = websocket
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.frames: list[tuple[float, bytes]] = []
        self._recording = False
        self._pump = asyncio.create_task(self._read_forever())

    async def _read_forever(self) -> None:
        try:
            async for raw in self.websocket:
                message = json.loads(raw)
                if "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(str(message["error"])))
                        else:
                            future.set_result(message.get("result", {}))
                    continue
                if message.get("method") == "Page.screencastFrame":
                    params = message["params"]
                    if self._recording:
                        self.frames.append(
                            (time.monotonic(), base64.b64decode(params["data"]))
                        )
                    # Chrome stops sending frames until each one is acked.
                    with contextlib.suppress(Exception):
                        await self.call_nowait(
                            "Page.screencastFrameAck", sessionId=params["sessionId"]
                        )
        except Exception:  # noqa: BLE001 - the socket closing ends the pump
            pass

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def call_nowait(self, method: str, **params) -> None:
        await self.websocket.send(
            json.dumps({"id": self._next_id(), "method": method, "params": params})
        )

    async def call(self, method: str, **params):
        message_id = self._next_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self.websocket.send(
            json.dumps({"id": message_id, "method": method, "params": params})
        )
        return await asyncio.wait_for(future, timeout=45.0)

    async def evaluate(self, expression: str):
        result = await self.call(
            "Runtime.evaluate", expression=expression,
            awaitPromise=True, returnByValue=True,
        )
        return result.get("result", {}).get("value")

    async def goto(self, url: str, settle: float = 4.0) -> None:
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(settle)

    async def start_recording(self) -> None:
        await self.call(
            "Page.startScreencast",
            format="jpeg", quality=92,
            maxWidth=WIDTH, maxHeight=HEIGHT, everyNthFrame=1,
        )
        self._recording = True

    async def stop_recording(self) -> None:
        self._recording = False
        with contextlib.suppress(Exception):
            await self.call("Page.stopScreencast")


async def install_overlay(devtools: Devtools) -> None:
    await devtools.evaluate(INSTALL_OVERLAY % {"css": json.dumps(CAPTION_CSS)})


async def set_caption(devtools: Devtools, title: str, sub: str) -> None:
    await devtools.evaluate(
        SET_CAPTION % {"title": json.dumps(title), "sub": json.dumps(sub)}
    )


async def set_radio(devtools: Devtools, question: str, answer: str, show: bool) -> None:
    await devtools.evaluate(
        SET_RADIO % {
            "q": json.dumps(question), "a": json.dumps(answer),
            "show": "true" if show else "false",
        }
    )


# --------------------------------------------------------------------------
# The real radio round trip
# --------------------------------------------------------------------------


@dataclass
class RadioExchange:
    question: str
    transcript: str
    reply: str
    audio: Path
    offset_s: float = 0.0


async def speak_question(client: httpx.AsyncClient, text: str, target: Path) -> Path:
    """Synthesize the driver's side of the radio.

    The driver's question is ours to write — it is the prompt, not the claim.
    Everything downstream of it is the application's.
    """
    from pitwall.audio import AudioService

    service = AudioService()
    if service.client is None:
        raise SystemExit("OPENAI_API_KEY is not configured; the demo needs the real engineer")
    # A different voice from the engineer's, so the two sides are distinguishable.
    response = await service.client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="ash", input=text, response_format="wav",
    )
    target.write_bytes(response.content)
    return target


async def ask_over_radio(
    base: str, question: str, work: Path, index: int
) -> RadioExchange:
    """Post spoken audio to /api/voice and keep what the engineer said."""
    clip = work / f"question-{index:02d}.wav"
    async with httpx.AsyncClient(timeout=180.0) as client:
        await speak_question(client, question, clip)
        with clip.open("rb") as handle:
            response = await client.post(
                f"{base}/api/voice",
                files={"file": ("clip.wav", handle, "audio/wav")},
            )
        response.raise_for_status()
        payload = response.json()
        audio = work / f"reply-{index:02d}.mp3"
        got = await client.get(f"{base}/api/latest-audio")
        got.raise_for_status()
        audio.write_bytes(got.content)
    return RadioExchange(
        question=question,
        transcript=payload.get("transcript", ""),
        reply=payload.get("reply", ""),
        audio=audio,
    )


async def trigger_proactive_call(base: str, work: Path, index: int) -> RadioExchange:
    """Fire the app's own test call and keep exactly what it spoke."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        state = await client.get(f"{base}/api/state")
        before = (state.json().get("proactive") or {}).get("last_call", "")
        response = await client.post(f"{base}/api/proactive/test")
        response.raise_for_status()
        spoken = ""
        for _ in range(120):
            await asyncio.sleep(1.0)
            state = await client.get(f"{base}/api/state")
            payload = (state.json().get("proactive") or {})
            if payload.get("last_call") and payload.get("last_call") != before:
                spoken = str(payload["last_call"])
                break
        if not spoken:
            raise RuntimeError("the proactive call was never delivered")
        audio = work / f"proactive-{index:02d}.mp3"
        got = await client.get(f"{base}/api/latest-audio")
        got.raise_for_status()
        audio.write_bytes(got.content)
    return RadioExchange(question="", transcript="", reply=spoken, audio=audio)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def write_frames(frames: list[tuple[float, bytes]], work: Path) -> tuple[Path, float]:
    """Write frames and a concat list carrying each one's real duration.

    Screencast only emits a frame when something changes, so the stream is not
    a fixed frame rate. Timing each frame by the wall clock keeps the video in
    step with the audio; assuming a constant rate would drift.
    """
    directory = work / "frames"
    directory.mkdir(parents=True, exist_ok=True)
    listing = work / "frames.txt"
    lines: list[str] = []
    total = 0.0
    for index, (stamp, data) in enumerate(frames):
        path = directory / f"f{index:05d}.jpg"
        path.write_bytes(data)
        if index + 1 < len(frames):
            duration = max(0.02, min(2.0, frames[index + 1][0] - stamp))
        else:
            duration = 0.25
        total += duration
        lines.append(f"file '{path.as_posix()}'")
        lines.append(f"duration {duration:.4f}")
    # The concat demuxer ignores the final duration unless the last file repeats.
    if frames:
        lines.append(f"file '{(directory / f'f{len(frames) - 1:05d}.jpg').as_posix()}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing, total


def assemble(
    listing: Path, exchanges: list[RadioExchange], out: Path, total: float,
    tail: float = 4.0,
) -> Path:
    ffmpeg = ffmpeg_exe()
    out.parent.mkdir(parents=True, exist_ok=True)

    command = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", str(listing),
    ]
    spoken = [e for e in exchanges if e.audio.exists() and e.audio.stat().st_size > 0]
    for exchange in spoken:
        command += ["-i", str(exchange.audio)]

    if spoken:
        # Place each reply at the moment it was actually spoken, then mix.
        parts = []
        for position, exchange in enumerate(spoken, start=1):
            delay = max(0, int(exchange.offset_s * 1000))
            parts.append(f"[{position}:a]adelay={delay}|{delay},volume=1.6[a{position}]")
        mix = "".join(f"[a{i}]" for i in range(1, len(spoken) + 1))
        parts.append(f"{mix}amix=inputs={len(spoken)}:dropout_transition=0:normalize=0[mix]")
        command += [
            "-filter_complex", ";".join(parts),
            "-map", "0:v", "-map", "[mix]",
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        command += ["-map", "0:v"]

    command += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", "30", "-preset", "medium", "-crf", "21",
        "-movflags", "+faststart",
        "-t", f"{total + tail:.2f}",
        str(out),
    ]
    print("  encoding…")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:])
        raise SystemExit("ffmpeg failed")
    return out


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


@dataclass
class Result:
    video: Path
    exchanges: list[RadioExchange] = field(default_factory=list)
    stills: list[Path] = field(default_factory=list)


async def record(
    base: str, out: Path, work: Path, browser: str,
    storyboard: tuple[Beat, ...] = STORYBOARD,
    stills: bool = True,
    tail: float = 4.0,
) -> Result:
    profile = Path(tempfile.mkdtemp(prefix="pitwall_video_"))
    process = subprocess.Popen(
        [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={profile}",
            f"--window-size={WIDTH},{HEIGHT}",
            "--force-device-scale-factor=1",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    endpoint = None
    for _ in range(60):
        try:
            targets = httpx.get(f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=1.0).json()
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                endpoint = pages[0]["webSocketDebuggerUrl"]
                break
        except Exception:  # noqa: BLE001 - still starting
            pass
        # await, not time.sleep: this runs on the event loop, and blocking it
        # would stall the CDP pump that has to service the same browser.
        await asyncio.sleep(0.5)
    if not endpoint:
        process.terminate()
        raise SystemExit("headless browser did not expose a debugger endpoint")

    exchanges: list[RadioExchange] = []
    stills: list[Path] = []
    try:
        async with websockets.connect(endpoint, max_size=96 * 1024 * 1024) as socket:
            devtools = Devtools(socket)
            await devtools.call("Page.enable")
            await devtools.call("Runtime.enable")
            await devtools.call(
                "Emulation.setDeviceMetricsOverride",
                width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False,
            )

            print("loading the dashboard")
            await devtools.goto(f"{base}/#live", settle=8.0)
            await install_overlay(devtools)

            # Phase one: ask everything BEFORE recording starts.
            #
            # A model round trip takes 20-100 seconds. Recording through them
            # produced a nine-minute video that was mostly a still frame
            # waiting for an answer. Asking first costs the same wall-clock
            # time but keeps it out of the footage, and the reply is just as
            # real for having been fetched a minute earlier.
            answers: dict[int, RadioExchange] = {}
            for number, beat in enumerate(storyboard, start=1):
                if beat.proactive:
                    print(f"  triggering the engineer's own call [{number}]")
                    try:
                        exchange = await trigger_proactive_call(base, work, number)
                        answers[number] = exchange
                        print(f"    engineer (unprompted): {exchange.reply[:110]}")
                    except Exception as exc:  # noqa: BLE001 - reported, not fatal
                        print(f"    proactive call failed: {exc}")
                    continue
                if not beat.ask:
                    continue
                print(f"  asking [{number}]: {beat.ask}")
                try:
                    exchange = await ask_over_radio(base, beat.ask, work, number)
                    answers[number] = exchange
                    print(f"    engineer: {exchange.reply[:110]}")
                except Exception as exc:  # noqa: BLE001 - reported, not fatal
                    print(f"    radio failed: {exc}")

            await devtools.start_recording()
            start = time.monotonic()
            print("recording")

            for number, beat in enumerate(storyboard, start=1):
                if beat.page:
                    await devtools.goto(f"{base}/#{beat.page}", settle=2.5)
                    await install_overlay(devtools)
                if beat.js:
                    await devtools.evaluate(beat.js)
                await set_caption(devtools, beat.caption, beat.sub)

                exchange = answers.get(number)
                if beat.proactive and exchange is not None:
                    # The engineer's own call: no question card, just the voice
                    # and the reply text, timed like any radio exchange.
                    exchange.offset_s = time.monotonic() - start
                    await set_radio(devtools, "", exchange.reply, True)
                    exchanges.append(exchange)
                    await asyncio.sleep(_clip_seconds(exchange.audio) + 1.0)
                elif beat.ask and exchange is not None:
                    # Show the question, then the answer as it starts speaking.
                    await set_radio(devtools, exchange.transcript or beat.ask, "…", True)
                    await asyncio.sleep(1.1)
                    exchange.offset_s = time.monotonic() - start
                    await set_radio(
                        devtools, exchange.transcript or beat.ask, exchange.reply, True
                    )
                    exchanges.append(exchange)
                    # Hold while it is spoken, plus a moment to read it.
                    await asyncio.sleep(_clip_seconds(exchange.audio) + 1.0)
                elif beat.ask:
                    await set_radio(devtools, "", "", False)
                    await asyncio.sleep(beat.seconds)
                else:
                    await set_radio(devtools, "", "", False)
                    await asyncio.sleep(beat.seconds)

            await devtools.stop_recording()
            elapsed = time.monotonic() - start
            print(f"captured {len(devtools.frames)} frames over {elapsed:.1f}s")

            # Fresh stills from the same session, with the overlay removed.
            await devtools.evaluate(
                "(()=>{for(const id of ['pw-demo-caption','pw-demo-radio']){"
                "const n=document.getElementById(id); if(n) n.remove();} return 'clean'})()"
            )
            for page, name, height in (
                ("live", "07-hungaroring-decision.png", 1080),
                ("live", "08-hungaroring-radio.png", 1560),
                ("field", "09-hungaroring-field.png", 1080),
            ) if stills else ():
                await devtools.goto(f"{base}/#{page}", settle=3.5)
                await devtools.call(
                    "Emulation.setDeviceMetricsOverride",
                    width=1600, height=height, deviceScaleFactor=1, mobile=False,
                )
                await asyncio.sleep(1.0)
                shot = await devtools.call("Page.captureScreenshot", format="png")
                path = work / name
                path.write_bytes(base64.b64decode(shot["data"]))
                stills.append(path)
                print(f"  still: {name}")

            frames = list(devtools.frames)
    finally:
        process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)

    if not frames:
        raise SystemExit("no frames were captured")

    listing, total = write_frames(frames, work)
    video = assemble(listing, exchanges, out, total, tail=tail)
    return Result(video=video, exchanges=exchanges, stills=stills)


def _clip_seconds(path: Path) -> float:
    """Length of an audio clip, so the caption holds while it plays."""
    if not path.exists():
        return 0.0
    try:
        probe = subprocess.run(
            [ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True,
        )
        for line in probe.stderr.splitlines():
            if "Duration:" in line:
                stamp = line.split("Duration:")[1].split(",")[0].strip()
                hours, minutes, seconds = stamp.split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception:  # noqa: BLE001 - a probe failure just means a default hold
        pass
    return 6.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default="distribution/website/assets/pitwall-demo.mp4")
    parser.add_argument("--work", default="")
    parser.add_argument(
        "--storyboard", default="",
        help="JSON beats file; omitted means the original long-form demo",
    )
    parser.add_argument("--no-stills", action="store_true")
    parser.add_argument(
        "--tail", type=float, default=4.0,
        help="seconds the final frame holds; short punchy cuts want ~0.8",
    )
    args = parser.parse_args()

    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="pitwall_demo_"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"working directory: {work}")

    board = load_storyboard(Path(args.storyboard)) if args.storyboard else STORYBOARD
    result = asyncio.run(
        record(
            args.base, Path(args.out), work, find_browser(),
            storyboard=board, stills=not args.no_stills, tail=args.tail,
        )
    )

    print(f"\nvideo: {result.video}  ({result.video.stat().st_size / 1e6:.1f} MB)")
    transcript = work / "radio-transcript.md"
    lines = ["# Demo radio transcript", ""]
    lines.append("Every answer below was computed by the running application "
                 "from the synthetic telemetry. None of it was written by hand.")
    lines.append("")
    for exchange in result.exchanges:
        lines.append(f"**Driver:** {exchange.transcript or exchange.question}")
        lines.append("")
        lines.append(f"**Engineer:** {exchange.reply}")
        lines.append("")
    transcript.write_text("\n".join(lines), encoding="utf-8")
    print(f"transcript: {transcript}")
    for still in result.stills:
        print(f"still: {still}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
