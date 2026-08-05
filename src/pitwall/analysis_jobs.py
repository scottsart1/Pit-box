"""Durable bounded workers for session reprocessing jobs."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .comparison_service import ComparisonService, ComparisonServiceError


@dataclass(frozen=True, slots=True)
class AnalysisJobSnapshot:
    running: bool
    workers: int
    queue_depth: int
    queue_capacity: int
    submitted: int
    completed: int
    failed: int
    deferred: int
    active: int


class AnalysisJobService:
    """Resume durable jobs and derive real pair comparisons off the UDP path."""

    def __init__(
        self,
        database_path: Path,
        comparison_service: ComparisonService,
        *,
        worker_count: int = 2,
        queue_size: int = 128,
    ) -> None:
        self.database_path = Path(database_path)
        self.comparisons = comparison_service
        self.worker_count = max(1, int(worker_count))
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, int(queue_size)))
        self._tasks: list[asyncio.Task[None]] = []
        self._queued_ids: set[str] = set()
        self._fill_lock = asyncio.Lock()
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._deferred = 0
        self._active = 0

    @property
    def running(self) -> bool:
        return bool(self._tasks) and all(not task.done() for task in self._tasks)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    async def start(self) -> None:
        if self.running:
            return
        await asyncio.to_thread(self._resume_interrupted)
        self._tasks = [
            asyncio.create_task(self._worker(), name=f"pitwall-analysis-job-{index}")
            for index in range(self.worker_count)
        ]
        await self._fill_from_database()

    def _resume_interrupted(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE analysis_jobs
                SET state='queued', started_at=NULL,
                    error_code='interrupted',
                    error_detail='Pit Wall stopped while this job was running.'
                WHERE state='running'
                """
            )

    def submit(self, job: dict[str, Any]) -> bool:
        job_id = str(job.get("id") or "")
        if not self.running or not job_id or job_id in self._queued_ids:
            return False
        try:
            self.queue.put_nowait(job_id)
        except asyncio.QueueFull:
            self._deferred += 1
            return False
        self._queued_ids.add(job_id)
        self._submitted += 1
        return True

    def _queued_jobs(self, limit: int) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id FROM analysis_jobs
                WHERE state='queued'
                ORDER BY created_at, id LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    async def _fill_from_database(self) -> None:
        if not self.running:
            return
        async with self._fill_lock:
            capacity = self.queue.maxsize - self.queue.qsize()
            if capacity <= 0:
                return
            rows = await asyncio.to_thread(self._queued_jobs, capacity * 2)
            for job_id in rows:
                if job_id in self._queued_ids:
                    continue
                try:
                    self.queue.put_nowait(job_id)
                except asyncio.QueueFull:
                    break
                self._queued_ids.add(job_id)
                self._submitted += 1

    def _set_state(
        self,
        job_id: str,
        state: str,
        *,
        progress: float,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        now = self._now()
        with self._connect() as db:
            db.execute(
                """
                UPDATE analysis_jobs
                SET state=?, progress=?, error_code=?, error_detail=?,
                    started_at=CASE WHEN ?='running' THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at=CASE WHEN ? IN ('complete', 'failed') THEN ? ELSE NULL END
                WHERE id=?
                """,
                (
                    state,
                    max(0.0, min(1.0, float(progress))),
                    error_code,
                    error_detail,
                    state,
                    now,
                    state,
                    now,
                    job_id,
                ),
            )

    def _job(self, job_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM analysis_jobs WHERE id=?", (job_id,)
            ).fetchone()

    def _laps(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT l.id, l.lap_time_ms, c.is_player, c.car_index
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                WHERE c.session_id=? AND l.valid=1
                  AND l.trace_manifest_id IS NOT NULL
                  AND l.lap_time_ms IS NOT NULL AND l.lap_time_ms>0
                ORDER BY l.lap_time_ms, c.car_index, l.lap_number
                LIMIT 500
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _audit(self, job_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO audit_events(event_type, subject_id, detail_json, created_at)
                VALUES ('analysis_job_complete', ?, ?, ?)
                """,
                (job_id, json.dumps(payload, sort_keys=True), self._now()),
            )

    async def _process(self, job_id: str) -> None:
        row = await asyncio.to_thread(self._job, job_id)
        if row is None or str(row["state"]) not in {"queued", "running"}:
            return
        session_id = str(row["session_id"])
        await asyncio.to_thread(self._set_state, job_id, "running", progress=0.0)
        laps = await asyncio.to_thread(self._laps, session_id)
        player = [item for item in laps if bool(item["is_player"])]
        reference = (player or laps)[0] if laps else None
        candidates = [
            item for item in laps if reference and item["id"] != reference["id"]
        ]
        completed = 0
        skipped: list[dict[str, str]] = []
        for index, candidate in enumerate(candidates, 1):
            try:
                await self.comparisons.create_comparison(
                    str(candidate["id"]),
                    reference_kind="lap",
                    reference_lap_id=str(reference["id"]),
                    allow_caveated_reference=True,
                )
            except ComparisonServiceError as exc:
                skipped.append({"lap_id": str(candidate["id"]), "code": exc.code})
            else:
                completed += 1
            await asyncio.to_thread(
                self._set_state,
                job_id,
                "running",
                progress=index / max(1, len(candidates)),
            )
        result = {
            "session_id": session_id,
            "reference_lap_id": str(reference["id"]) if reference else None,
            "candidate_laps": len(candidates),
            "comparisons_completed": completed,
            "skipped": skipped[:100],
        }
        await asyncio.to_thread(self._audit, job_id, result)
        await asyncio.to_thread(self._set_state, job_id, "complete", progress=1.0)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            self._active += 1
            try:
                await self._process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate one durable job
                self._failed += 1
                await asyncio.to_thread(
                    self._set_state,
                    job_id,
                    "failed",
                    progress=0.0,
                    error_code=type(exc).__name__,
                    error_detail=str(exc)[:1_000],
                )
            else:
                self._completed += 1
            finally:
                self._active -= 1
                self._queued_ids.discard(job_id)
                self.queue.task_done()
                await self._fill_from_database()

    async def wait_idle(self, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while self.queue.qsize() or self._active:
            if time.monotonic() >= deadline:
                raise TimeoutError("analysis jobs did not become idle")
            await asyncio.sleep(0.01)

    async def stop(self, *, drain_timeout_s: float = 10.0) -> None:
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(self.queue.join(), timeout=drain_timeout_s)
        except TimeoutError:
            pass
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def snapshot(self) -> AnalysisJobSnapshot:
        return AnalysisJobSnapshot(
            running=self.running,
            workers=self.worker_count,
            queue_depth=self.queue.qsize(),
            queue_capacity=self.queue.maxsize,
            submitted=self._submitted,
            completed=self._completed,
            failed=self._failed,
            deferred=self._deferred,
            active=self._active,
        )

    def snapshot_dict(self) -> dict[str, Any]:
        return asdict(self.snapshot())


__all__ = ["AnalysisJobService", "AnalysisJobSnapshot"]
