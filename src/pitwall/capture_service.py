from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capture import (
    CapturedDatagram,
    CaptureFormatError,
    CaptureScanReport,
    CaptureWriter,
    recover_capture,
    scan_capture,
)


@dataclass(frozen=True, slots=True)
class CaptureServiceSnapshot:
    state: str
    relative_path: str | None
    queue_depth: int
    queue_capacity: int
    queue_high_water: int
    packets_queued: int
    packets_written: int
    bytes_written: int
    active_file_bytes: int
    queue_drops: int
    write_errors: int
    last_write_at: float | None
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CaptureRecoverySummary:
    recovered: tuple[CaptureScanReport, ...]
    unresolved: tuple[tuple[str, str], ...]


class CaptureService:
    """Bounded asynchronous bridge from the UDP hot path to PWCAP storage."""

    def __init__(
        self,
        root: Path,
        *,
        queue_size: int = 8192,
        max_file_bytes: int | None = None,
        minimum_free_bytes: int = 0,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue[CapturedDatagram] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self.max_file_bytes = (
            None if max_file_bytes is None else max(1, int(max_file_bytes))
        )
        self.minimum_free_bytes = max(0, int(minimum_free_bytes))
        self._writer: CaptureWriter | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._relative_path: str | None = None
        self._state = "off"
        self._packets_queued = 0
        self._packets_written = 0
        self._bytes_written = 0
        self._queue_drops = 0
        self._write_errors = 0
        self._queue_high_water = 0
        self._last_write_at: float | None = None
        self._last_error: str | None = None
        self._current_datagram_bytes = 0

    @property
    def running(self) -> bool:
        return self._worker_task is not None and not self._worker_task.done()

    def _safe_destination(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("capture path must be relative to the capture root")
        destination = (self.root / relative).resolve()
        try:
            destination.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("capture path escapes the configured root") from exc
        return destination

    async def recover_pending(self) -> CaptureRecoverySummary:
        """Recover complete blocks from exact unfinished PWCAP temp files."""

        def quarantine(path: Path) -> str | None:
            """Move a permanently unrecoverable temp file out of the scan path.

            Without this, a dead stub (typically a 0-byte file left by a launch
            that lost the port race and died before writing a header) was
            re-reported as unresolved at every startup forever. The file is
            moved, never deleted: whatever bytes it holds stay inspectable
            under ``unrecoverable/``.
            """
            relative = path.relative_to(self.root)
            destination = self.root / "unrecoverable" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
                destination = destination.with_name(
                    f"{destination.name}.{stamp}"
                )
            try:
                path.replace(destination)
            except OSError:
                return None
            return destination.relative_to(self.root).as_posix()

        def recover() -> CaptureRecoverySummary:
            recovered: list[CaptureScanReport] = []
            unresolved: list[tuple[str, str]] = []
            for path in sorted(self.root.rglob("*.tmp")):
                if ".pwcap" not in path.name.casefold():
                    continue
                if "unrecoverable" in path.relative_to(self.root).parts:
                    continue
                try:
                    path.resolve().relative_to(self.root)
                    report = recover_capture(path)
                except CaptureFormatError as exc:
                    # The content itself is invalid; retrying next launch can
                    # never succeed. Park it once and stop warning about it.
                    moved = quarantine(path)
                    note = (
                        f"{exc} — moved to {moved}"
                        if moved
                        else f"{exc} — could not be moved aside"
                    )
                    unresolved.append(
                        (path.relative_to(self.root).as_posix(), note)
                    )
                    continue
                except (OSError, ValueError) as exc:
                    # Possibly transient (file lock, disk hiccup): leave the
                    # file in place so the next launch can retry it.
                    unresolved.append(
                        (path.relative_to(self.root).as_posix(), str(exc))
                    )
                    continue
                recovered.append(report)
            return CaptureRecoverySummary(tuple(recovered), tuple(unresolved))

        return await asyncio.to_thread(recover)

    async def start(
        self,
        *,
        relative_path: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if self.running:
            assert self._relative_path is not None
            return self._relative_path
        if relative_path is None:
            year = datetime.now(UTC).strftime("%Y")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            relative_path = Path(year) / f"capture-{stamp}.pwcap"
        destination = self._safe_destination(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        capture_metadata = {
            "app_version": "4.6.2",
            "created_utc": datetime.now(UTC).isoformat(),
            "privacy_mode": "private",
            **(metadata or {}),
        }
        try:
            writer = await asyncio.to_thread(
                CaptureWriter, destination, metadata=capture_metadata
            )
        except Exception as exc:
            self._state = "error"
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        self._writer = writer
        self._relative_path = destination.relative_to(self.root).as_posix()
        self._state = "recording"
        self._last_error = None
        self._current_datagram_bytes = 0
        self._worker_task = asyncio.create_task(
            self._worker(), name="pitwall-raw-capture-writer"
        )
        return self._relative_path

    def submit(
        self,
        data: bytes | bytearray | memoryview,
        source: tuple[str, int],
        *,
        monotonic_ns: int | None = None,
        wall_ns: int | None = None,
    ) -> bool:
        """Offer an immutable original datagram without awaiting disk I/O."""
        if not self.running or self._state != "recording":
            return False
        try:
            item = CapturedDatagram(
                monotonic_ns=time.monotonic_ns()
                if monotonic_ns is None
                else monotonic_ns,
                wall_ns=time.time_ns() if wall_ns is None else wall_ns,
                source_host=str(source[0]),
                source_port=int(source[1]),
                data=bytes(data),
            )
        except (TypeError, ValueError) as exc:
            self._write_errors += 1
            self._last_error = f"invalid_datagram: {exc}"
            return False
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                self._queue_drops += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self._queue_drops += 1
            return False
        self._packets_queued += 1
        self._queue_high_water = max(self._queue_high_water, self.queue.qsize())
        return True

    @staticmethod
    def _write_batch(writer: CaptureWriter, batch: list[CapturedDatagram]) -> None:
        for item in batch:
            writer.write(
                item.data,
                item.source,
                monotonic_ns=item.monotonic_ns,
                wall_ns=item.wall_ns,
            )

    async def _worker(self) -> None:
        while True:
            first = await self.queue.get()
            batch = [first]
            while len(batch) < 256:
                try:
                    batch.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            writer = self._writer
            try:
                if writer is None:
                    raise RuntimeError("capture writer is not open")
                incoming_bytes = sum(len(item.data) for item in batch)
                if (
                    self.max_file_bytes is not None
                    and self._current_datagram_bytes + incoming_bytes
                    > self.max_file_bytes
                ):
                    self._state = "limit_reached"
                    self._last_error = "capture_file_size_limit_reached"
                    self._queue_drops += len(batch)
                    continue
                if self.minimum_free_bytes:
                    free_bytes = await asyncio.to_thread(
                        lambda: shutil.disk_usage(self.root).free
                    )
                    if free_bytes - incoming_bytes < self.minimum_free_bytes:
                        self._state = "limit_reached"
                        self._last_error = "capture_minimum_free_disk_reached"
                        self._queue_drops += len(batch)
                        continue
                await asyncio.to_thread(self._write_batch, writer, batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate optional disk capture
                self._write_errors += len(batch)
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._state = "error"
            else:
                self._packets_written += len(batch)
                self._bytes_written += incoming_bytes
                self._current_datagram_bytes += incoming_bytes
                self._last_write_at = time.time()
            finally:
                for _ in batch:
                    self.queue.task_done()

    async def stop(self, *, drain_timeout_s: float = 5.0) -> Path | None:
        task = self._worker_task
        writer = self._writer
        if task is None or writer is None:
            self._state = "off"
            return None
        self._state = "finalizing"
        try:
            await asyncio.wait_for(
                self.queue.join(), timeout=max(0.0, float(drain_timeout_s))
            )
        except TimeoutError:
            while True:
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self.queue.task_done()
                self._queue_drops += 1
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._worker_task = None
        self._writer = None
        try:
            path = await asyncio.to_thread(writer.close)
            report = await asyncio.to_thread(scan_capture, path)
            if not report.valid:
                raise RuntimeError("finalized capture did not pass validation")
        except Exception as exc:
            self._state = "error"
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        self._state = "off"
        return path

    def snapshot(self) -> CaptureServiceSnapshot:
        active_file_bytes = 0
        writer = self._writer
        if writer is not None:
            try:
                active_file_bytes = writer.temp_path.stat().st_size
            except OSError:
                active_file_bytes = 0
            active_file_bytes += writer.pending_bytes
        return CaptureServiceSnapshot(
            state=self._state,
            relative_path=self._relative_path,
            queue_depth=self.queue.qsize(),
            queue_capacity=self.queue.maxsize,
            queue_high_water=self._queue_high_water,
            packets_queued=self._packets_queued,
            packets_written=self._packets_written,
            bytes_written=self._bytes_written,
            active_file_bytes=active_file_bytes,
            queue_drops=self._queue_drops,
            write_errors=self._write_errors,
            last_write_at=self._last_write_at,
            last_error=self._last_error,
        )


__all__ = [
    "CaptureRecoverySummary",
    "CaptureService",
    "CaptureServiceSnapshot",
]
