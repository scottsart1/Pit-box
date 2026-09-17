"""Optional, bounded usage reporting. Never accepts user content or telemetry.

Only daily yes/no feature flags leave the device, after an explicit choice.
The local outbox is separate from race history and is not part of transfers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import __version__

log = logging.getLogger(__name__)
ENDPOINT = "https://pitwall-activation.sarthakvij123450.workers.dev/usage"
EVENTS = frozenset({"app_started", "app_used", "racing", "engineer", "voice", "analysis", "transfer"})
CONSENT_VERSION = 1


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class UsageReporting:
    def __init__(self, path: Path, *, platform: str | None = None) -> None:
        self.path = path
        self.platform = platform or ("android" if hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ else "windows" if sys.platform == "win32" else "other")
        self.enabled = False
        self.decided = False
        self.installation_id: str | None = None
        self.pending: set[tuple[str, str]] = set()
        self.seen: set[tuple[str, str]] = set()
        self.last_sent_at: str | None = None
        self._dirty = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._session: tuple | None = None
        self._previous_time: float | None = None
        self._moving_seconds = 0.0
        self._observed_at: float | None = None

    def _load(self) -> None:
        try:
            # Fail closed on corrupt, old or unexpectedly large state.
            if self.path.stat().st_size > 32_768:
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("consent_version") != CONSENT_VERSION or type(data.get("enabled")) is not bool:
                return
            if data["enabled"]:
                parsed_id = uuid.UUID(data["installation_id"])
                if parsed_id.version != 4:
                    return
                identity = str(parsed_id)
                if identity != data["installation_id"]:
                    return
                self.installation_id = identity
                self.pending = self._valid_rows(data.get("pending", []))
                self.seen = self._valid_rows(data.get("seen", [])) | self.pending
                self.last_sent_at = data.get("last_sent_at")
            self.enabled = data["enabled"]
            self.decided = True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return

    @staticmethod
    def _valid_rows(rows: object) -> set[tuple[str, str]]:
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=6)).isoformat()
        today = utc_day()
        if not isinstance(rows, list) or len(rows) > 49:
            return set()
        return {(row[0], row[1]) for row in rows if isinstance(row, list) and len(row) == 2
                and isinstance(row[0], str) and len(row[0]) == 10 and cutoff <= row[0] <= today
                and isinstance(row[1], str) and row[1] in EVENTS}

    def _state(self) -> dict:
        return {"consent_version": CONSENT_VERSION, "enabled": self.enabled,
                "installation_id": self.installation_id, "pending": sorted(self.pending),
                "seen": sorted(self.seen), "last_sent_at": self.last_sent_at}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)

    async def start(self) -> None:
        await asyncio.to_thread(self._load)
        self.record("app_started")
        self._task = asyncio.create_task(self._run(), name="pitwall-optional-usage")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._dirty:
            try:
                await asyncio.to_thread(self._write, self._state())
            except OSError:
                log.warning("Optional usage queue could not be saved")

    def status(self) -> dict:
        return {"enabled": self.enabled, "decided": self.decided,
                "pending_days": len({day for day, _ in self.pending}),
                "last_sent_at": self.last_sent_at, "consent_version": CONSENT_VERSION}

    async def set_enabled(self, enabled: bool) -> dict:
        async with self._lock:
            if self.decided and enabled == self.enabled:
                return self.status()
            data = {"consent_version": CONSENT_VERSION, "enabled": enabled,
                    "installation_id": str(uuid.uuid4()) if enabled else None,
                    "pending": [], "seen": [], "last_sent_at": None}
            # Persist the choice before enabling it; a failed save cannot opt in.
            await asyncio.to_thread(self._write, data)
            self.enabled, self.decided = enabled, True
            self.installation_id = data["installation_id"]
            self.pending.clear()
            self.seen.clear()
            self.last_sent_at = None
            self._dirty = False
            self._moving_seconds = 0
            self._previous_time = None
            self.record("app_started")
            return self.status()

    def record(self, event: str) -> None:
        # Fast, in-memory only: safe to call in the telemetry/voice paths.
        if not self.enabled or self.platform not in {"windows", "android"} or event not in EVENTS:
            return
        self._prune()
        key = (utc_day(), event)
        if key not in self.seen:
            self.seen.add(key)
            self.pending.add(key)
            self._dirty = True

    def _prune(self) -> None:
        pending = self._valid_rows([list(row) for row in self.pending])
        seen = self._valid_rows([list(row) for row in self.seen])
        if pending != self.pending or seen != self.seen:
            self.pending, self.seen = pending, seen
            self._dirty = True

    def observe_racing(self, snapshot: dict, *, now: float | None = None) -> None:
        """Daily activation after 60 real seconds of fresh, advancing driving.

        Session identifiers, speeds and clocks are used only locally and never
        added to the report. Menus, pauses and repeated stale packets don't count.
        """
        if not self.enabled:
            return
        now = time.time() if now is None else now
        session = (snapshot.get("session_uid"), snapshot.get("restart_epoch", 0))
        clock = snapshot.get("session_time_s")
        if session != self._session:
            self._session, self._previous_time, self._moving_seconds = session, None, 0
        previous, observed = self._previous_time, self._observed_at
        self._previous_time, self._observed_at = clock, now
        freshness = snapshot.get("packet_group_freshness") or {}
        fresh = freshness.get("6", 0)  # F1 car telemetry packet ID
        if not (session[0] and snapshot.get("connected") and not snapshot.get("game_paused")
                and snapshot.get("speed_kph", 0) > 5 and 0 <= now - fresh <= 3
                and isinstance(clock, (int, float)) and isinstance(previous, (int, float))
                and observed is not None and 0 < now - observed <= 3 and clock > previous):
            self._moving_seconds = 0
            return
        self._moving_seconds += min(now - observed, clock - previous)
        if self._moving_seconds >= 60:
            self.record("racing")
            self.record("app_used")

    async def flush(self, client: httpx.AsyncClient) -> bool:
        async with self._lock:
            self._prune()
            if not self.enabled or not self.pending:
                return True
            batch = sorted(self.pending)[:28]
            response = await client.post(ENDPOINT, json={
                "consent_version": CONSENT_VERSION, "installation_id": self.installation_id,
                "platform": self.platform, "version": __version__,
                "events": [{"day": day, "event": event} for day, event in batch],
            })
            if response.status_code != 200:
                return False
            try:
                if response.json() != {"ok": True}:
                    return False
            except ValueError:
                return False
            self.pending.difference_update(batch)
            self.last_sent_at = datetime.now(timezone.utc).isoformat()
            self._dirty = True
            return True

    async def _save_pending(self) -> None:
        if self._dirty:
            async with self._lock:
                self._dirty = False
                try:
                    await asyncio.to_thread(self._write, self._state())
                except OSError:
                    self._dirty = True
                    raise

    async def _run(self) -> None:
        retry = 15.0
        # No client, DNS lookup or network request at all until consent.
        while True:
            await asyncio.sleep(retry)
            try:
                self._prune()
                # Persist before network IO so offline queues survive a crash.
                await self._save_pending()
                if self.enabled and self.pending:
                    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
                        ok = await self.flush(client)
                    retry = 60 if ok else min(retry * 2, 900)
                await self._save_pending()
            except (httpx.HTTPError, OSError):
                # No server response/body, user text or environment in logs.
                retry = min(retry * 2, 900)
