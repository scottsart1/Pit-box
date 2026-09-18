"""Read-only release checks. No installation identity, telemetry or auto-installing."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
from contextlib import suppress
from datetime import datetime, timezone

import httpx

from . import __version__

ENDPOINT = "https://pitwall-activation.sarthakvij123450.workers.dev/releases"
INTERVAL = 6 * 60 * 60
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\Z")


def version_parts(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("Invalid stable release version")
    return tuple(map(int, value.split(".")))


def validate_release(data: dict, platform: str) -> dict | None:
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("platform") != platform:
        raise ValueError("Invalid release manifest")
    release = data.get("release")
    if release is None:
        return None
    version_parts(release["version"])
    if release["download_url"] != f"https://yourpitbox.com/#{platform}-release":
        raise ValueError("Untrusted download destination")
    if not isinstance(release["notes"], str) or len(release["notes"]) > 4000:
        raise ValueError("Invalid release notes")
    stamp = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.timestamp() > time.time() + 300:
        raise ValueError("Invalid release time")
    if not re.fullmatch(r"[a-f0-9]{64}", release["sha256"]):
        raise ValueError("Invalid release checksum")
    if type(release["size"]) is not int or not 1 <= release["size"] <= 2_000_000_000:
        raise ValueError("Invalid release size")
    return {key: release[key] for key in ("version", "notes", "download_url", "published_at", "sha256", "size")}


class UpdateService:
    def __init__(self, path: Path, *, platform: str | None = None, current_version: str = __version__) -> None:
        self.path = path
        self.platform = platform or ("android" if hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ else "windows" if sys.platform == "win32" else "other")
        self.current_version = current_version
        self.enabled = True
        self.dismissed = ""
        self.latest: dict | None = None
        self.checked_at: str | None = None
        self.outcome = "not_checked"
        self._attempt = 0.0
        self._success = 0.0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if type(data.get("enabled")) is bool:
                self.enabled = data["enabled"]
            if isinstance(data.get("dismissed"), str) and VERSION.fullmatch(data["dismissed"]):
                self.dismissed = data["dismissed"]
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def status(self) -> dict:
        latest = self.latest if time.monotonic() - self._success < 86400 else None
        available = bool(latest and version_parts(latest["version"]) > version_parts(self.current_version))
        return {"enabled": self.enabled, "platform": self.platform, "current_version": self.current_version,
                "release": latest, "available": available, "dismissed": bool(latest and self.dismissed == latest["version"]),
                "checked_at": self.checked_at, "outcome": self.outcome}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self.path)

    async def preferences(self, *, enabled: bool | None = None, dismiss: bool = False) -> dict:
        async with self._lock:
            choice = self.enabled if enabled is None else enabled
            dismissed = self.latest["version"] if dismiss and self.latest else self.dismissed
            await asyncio.to_thread(self._write, {"enabled": choice, "dismissed": dismissed})
            self.enabled, self.dismissed = choice, dismissed
            return self.status()

    async def check(self, client: httpx.AsyncClient | None = None) -> dict:
        async with self._lock:
            if self.platform not in {"windows", "android"}:
                self.outcome = "unsupported"
                return self.status()
            if self._attempt and time.monotonic() - self._attempt < 60:
                return self.status()
            self._attempt = time.monotonic()
            own_client = client is None
            client = client or httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False, headers={"User-Agent": "YourPitBox-update-check"})
            try:
                async with client.stream("GET", ENDPOINT, params={"platform": self.platform}) as response:
                    response.raise_for_status()
                    if not response.headers.get("content-type", "").startswith("application/json"):
                        raise ValueError("Unexpected release response")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 32768:
                            raise ValueError("Release response too large")
                self.latest = validate_release(json.loads(body), self.platform)
                self._success = time.monotonic()
                self.checked_at = datetime.now(timezone.utc).isoformat()
                self.outcome = "ok" if self.latest else "not_published"
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                self.outcome = "unavailable"
            finally:
                if own_client:
                    await client.aclose()
            return self.status()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="release-check")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(10)  # never delay telemetry startup
        while True:
            if self.enabled and (not self._attempt or time.monotonic() - self._attempt >= INTERVAL):
                await self.check()
            await asyncio.sleep(60)
