"""Session-aware raw-capture rotation outside the UDP receive hot path."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .capture import CaptureScanReport, scan_capture
from .capture_service import CaptureService

log = logging.getLogger(__name__)


class RawCaptureCatalog(Protocol):
    async def register_raw_capture(
        self,
        session_key: str | None,
        relative_path: str,
        report: CaptureScanReport,
        *,
        privacy_mode: str = "private",
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class CaptureLifecycleSnapshot:
    active_session_id: str | None
    queued_rotations: int
    rotation_drops: int
    rotations_completed: int
    last_error: str | None


class SessionCaptureCoordinator:
    """Rotate and catalogue captures when the normalized session key changes."""

    def __init__(
        self,
        service: CaptureService,
        catalog: RawCaptureCatalog,
        capture_root: Path,
        *,
        queue_size: int = 32,
    ) -> None:
        self.service = service
        self.catalog = catalog
        self.capture_root = Path(capture_root).resolve()
        self._queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._worker: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._active_session_id: str | None = None
        self._last_requested_session_id: str | None = None
        self._base_metadata: dict[str, Any] = {}
        self._rotation_drops = 0
        self._rotations_completed = 0
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start(self, *, metadata: Mapping[str, Any] | None = None) -> None:
        if self.running:
            return
        self._active_session_id = None
        self._last_requested_session_id = None
        self._base_metadata = dict(metadata or {})
        await self.service.start(
            metadata={**self._base_metadata, "session_id": None}
        )
        self._worker = asyncio.create_task(
            self._rotation_worker(), name="pitwall-capture-rotation"
        )

    def observe_session(self, session_id: str) -> None:
        """Schedule a transition and return immediately to the packet consumer."""

        key = str(session_id).strip()
        if not self.running or not key or key == self._last_requested_session_id:
            return
        self._last_requested_session_id = key
        try:
            self._queue.put_nowait(key)
        except asyncio.QueueFull:
            # Keep the newest identity: it is safer for all following packets.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self._rotation_drops += 1
            try:
                self._queue.put_nowait(key)
            except asyncio.QueueFull:
                self._rotation_drops += 1

    async def _rotation_worker(self) -> None:
        while True:
            key = await self._queue.get()
            try:
                await self._rotate(key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - capture remains optional
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("Raw capture rotation failed: %s", exc)
            finally:
                self._queue.task_done()

    async def _register(self, path: Path, session_id: str | None) -> None:
        report = await asyncio.to_thread(scan_capture, path)
        relative = path.resolve().relative_to(self.capture_root).as_posix()
        await self.catalog.register_raw_capture(session_id, relative, report)

    async def _rotate(self, session_id: str) -> None:
        async with self._lock:
            if session_id == self._active_session_id:
                return
            previous = self._active_session_id
            path = await self.service.stop()
            if path is not None:
                await self._register(path, previous)
            await self.service.start(
                metadata={**self._base_metadata, "session_id": session_id}
            )
            self._active_session_id = session_id
            self._rotations_completed += 1
            self._last_error = None

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        task = self._worker
        if task is not None:
            await self._queue.join()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._worker = None
        async with self._lock:
            path = await self.service.stop()
            if path is not None:
                await self._register(path, self._active_session_id)

    def snapshot(self) -> CaptureLifecycleSnapshot:
        return CaptureLifecycleSnapshot(
            active_session_id=self._active_session_id,
            queued_rotations=self._queue.qsize(),
            rotation_drops=self._rotation_drops,
            rotations_completed=self._rotations_completed,
            last_error=self._last_error,
        )


__all__ = ["CaptureLifecycleSnapshot", "SessionCaptureCoordinator"]
