from __future__ import annotations

import asyncio
import ctypes
import io
import os
import re
import time
from ctypes import wintypes

from PIL import Image, ImageGrab

from .engine import RaceEngine
from .hud_ocr import read_regions
from .models import OCRConfig, Observation, Sample


def windows() -> list[dict]:
    if os.name != "nt":
        return []
    api = ctypes.WinDLL("user32", use_last_error=True)
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    @callback_type
    def visit(hwnd, _):
        length = api.GetWindowTextLengthW(hwnd)
        if length and api.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(length + 1)
            api.GetWindowTextW(hwnd, name, length + 1)
            result.append({"id": int(hwnd), "title": name.value[:160]})
        return True
    api.EnumWindows(visit, 0)
    return result


def capture(window_id: int) -> Image.Image:
    if os.name != "nt":
        raise ValueError("Window capture requires Windows")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsIconic.argtypes = [wintypes.HWND]
    if not api.IsWindow(window_id) or api.IsIconic(window_id):
        raise ValueError("The selected window is closed or minimized")
    # Capture only the explicitly selected HWND, never the whole desktop.
    frame = ImageGrab.grab(window=window_id)
    if frame.width < 100 or frame.height < 100:
        raise ValueError("The selected window is too small to read")
    if max(frame.convert("L").getextrema()) < 5:
        raise ValueError("The game returned a black image. Use windowed/borderless mode and keep it visible.")
    return frame


def parse_reading(field: str, text: str):
    text = " ".join(text.upper().split())
    if field == "flag":
        matches = [flag for flag, pattern in [("yellow", r"\b(CAUTION|YELLOW FLAG)\b"), ("red", r"\bRED FLAG\b"),
                   ("white", r"\b(WHITE FLAG|FINAL LAP)\b"), ("green", r"\bGREEN FLAG\b"),
                   ("checkered", r"\b(CHECKERED FLAG|CHEQUERED FLAG|RACE COMPLETE)\b")] if re.search(pattern, text)]
        return matches[0] if len(matches) == 1 else None
    if field == "pit_open":
        if re.search(r"\bPIT(?: ROAD|S)? CLOSED\b", text):
            return False
        if re.search(r"\bPIT(?: ROAD|S)? OPEN\b", text):
            return True
        return None
    text = re.sub(r"^(LAP|LAPS|POS|POSITION|FUEL|SPEED|LAST LAP|LF|RF|LR|RR)\s*:?\s*", "", text)
    if field == "fuel_pct" or field.startswith("tire_"):
        # Windows OCR commonly reads the percent glyph as a small 0/0.
        text = re.sub(r"[0O]\s*/\s*[0O]$", "%", text)
    if field in ("current_lap", "leader_current_lap", "completed_laps", "position"):
        match = re.fullmatch(r"(\d{1,4})(?:\s*(?:/|OF)\s*\d{1,4})?", text)
        if not match:
            return None
        value = int(match[1])
        return value if (1 <= value <= 80 if field == "position" else 0 <= value <= 2200) else None
    if field == "last_lap_s":
        match = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,3}\.\d{1,3})", text)
        if not match:
            return None
        value = int(match[1] or 0) * 60 + float(match[2])
        return value if 0 < value <= 1800 else None
    suffix = r"(?:\s*MPH)?" if field == "speed_mph" else r"\s*%?"
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,2})?)" + suffix, text)
    if not match:
        return None
    value = float(match[1])
    return value if 0 <= value <= (300 if field == "speed_mph" else 100) else None


async def recognize(frame: Image.Image) -> str:
    reading = (await asyncio.to_thread(read_regions, [frame]))[0]
    return reading["text"] if reading["accepted"] else ""


class Observer:
    def __init__(self, engine: RaceEngine):
        self.engine = engine
        self.task: asyncio.Task | None = None
        self.config: OCRConfig | None = None
        self.previous: dict = {}
        self.read_lock = asyncio.Lock()
        self.status = {"running": False, "frames": 0, "last_error": None, "readings": {}, "latency_ms": None}

    async def read(self, frame: Image.Image, config: OCRConfig) -> dict:
        crops = []
        for region in config.regions:
            left, top = int(region.x * frame.width), int(region.y * frame.height)
            right, bottom = int((region.x + region.width) * frame.width), int((region.y + region.height) * frame.height)
            crops.append(frame.crop((left, top, right, bottom)))
        async with self.read_lock:
            results = await asyncio.to_thread(read_regions, crops)
        readings = {}
        for region, result in zip(config.regions, results):
            value = parse_reading(region.field, result["text"]) if result["accepted"] else None
            readings[region.field] = {**result, "value": value}
        return readings

    async def start(self, config: OCRConfig):
        if not self.engine.sid or self.engine.config.mode == "demo":
            raise ValueError("Start a driver weekend before starting the observer")
        await self.stop()
        # Validate selected handle and OCR availability before reporting running.
        frame = await asyncio.to_thread(capture, config.window_id)
        readings = await self.read(frame, config)
        if not any(item["value"] is not None for item in readings.values()):
            raise ValueError("No configured HUD values are readable. Crop one number or text line per box, then test again.")
        self.config, self.previous = config, {}
        self.status.update(running=True, last_error=None)
        self.task = asyncio.create_task(self.loop(self.engine.sid))

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.status["running"] = False

    async def loop(self, sid: str):
        failures = 0
        try:
            while self.engine.sid == sid:
                started = time.monotonic()
                try:
                    frame = await asyncio.to_thread(capture, self.config.window_id)
                    readings = await self.read(frame, self.config)
                    sample, tires = {}, {}
                    for field, item in readings.items():
                        value = item["value"]
                        old = self.previous.get(field)
                        self.previous[field] = value
                        # Require two matching reads for discrete HUD values.
                        stable = value is not None and value == old
                        if field == "speed_mph" and value is not None and old is not None:
                            stable = abs(value - old) <= 30
                        if (field == "fuel_pct" or field.startswith("tire_")) and value is not None and old is not None:
                            stable = abs(value - old) <= 2 * self.config.interval_s
                        if not stable:
                            continue
                        if field.startswith("tire_"):
                            tires[field[-2:]] = value
                        elif field in ("current_lap", "leader_current_lap"):
                            if value >= 1:
                                sample["leader_completed_laps" if field == "leader_current_lap" else "completed_laps"] = value - 1
                        else:
                            sample[field] = value
                    if tires:
                        sample["tires"] = tires
                    if sample:
                        self.engine.ingest(Observation(session_id=sid, sequence=time.monotonic_ns(), source="ocr", confidence=.9, sample=Sample.model_validate(sample)))
                    self.status.update(frames=self.status["frames"] + 1, readings=readings, last_error=None,
                                       latency_ms=round((time.monotonic() - started) * 1000))
                    failures = 0
                except (ValueError, OSError, RuntimeError) as exc:
                    failures += 1
                    self.status["last_error"] = str(exc)[:250]
                    self.previous.clear()
                    if failures >= 5:
                        break
                await asyncio.sleep(max(.05, self.config.interval_s - (time.monotonic() - started)))
        finally:
            self.status["running"] = False


def image_bytes(frame: Image.Image) -> bytes:
    output = io.BytesIO()
    frame.save(output, "PNG")
    return output.getvalue()
