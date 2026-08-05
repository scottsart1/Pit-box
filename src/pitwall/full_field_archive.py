"""Bounded asynchronous persistence for normalized opponent lap batches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from .catalog import lap_id
from .session_assembler import BranchInvalidation, FinalizedLapBatch
from .trace_store import TraceStore

log = logging.getLogger(__name__)

ArchiveItem: TypeAlias = FinalizedLapBatch | BranchInvalidation


@dataclass(frozen=True, slots=True)
class FullFieldArchiveSnapshot:
    state: str
    queue_depth: int
    queue_capacity: int
    queue_high_water: int
    submitted: int
    persisted_laps: int
    invalidations: int
    player_batches_skipped: int
    incomplete_batches_skipped: int
    queue_drops: int
    write_errors: int
    last_error: str | None
    invalidation_queue_depth: int
    invalidation_queue_capacity: int
    invalidation_queue_drops: int
    reconciliation_required: bool


class FullFieldArchiveService:
    """Persist assembler emissions without ever blocking the UDP consumer."""

    def __init__(
        self,
        database_path: Path,
        trace_store: TraceStore,
        *,
        queue_size: int = 512,
    ) -> None:
        self.database_path = Path(database_path)
        self.trace_store = trace_store
        self.queue: asyncio.Queue[ArchiveItem] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._invalidation_queue: asyncio.Queue[BranchInvalidation] = asyncio.Queue(
            maxsize=max(8, min(128, int(queue_size)))
        )
        self._task: asyncio.Task[None] | None = None
        self._state = "off"
        self._queue_high_water = 0
        self._submitted = 0
        self._persisted_laps = 0
        self._invalidations = 0
        self._player_skipped = 0
        self._incomplete_skipped = 0
        self._queue_drops = 0
        self._write_errors = 0
        self._last_error: str | None = None
        self._invalidation_queue_drops = 0
        self._reconciliation_required = False
        self._invalidated_batch_ids: set[str] = set()
        self._invalidated_batch_order: deque[str] = deque(maxlen=16_384)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._state = "running"
        self._last_error = None
        self._task = asyncio.create_task(
            self._worker(), name="pitwall-full-field-archive"
        )

    def submit(self, item: ArchiveItem) -> bool:
        # The existing player analysis path remains authoritative and owns that
        # car's typed trace. Avoid two writers sharing one pending trace buffer.
        if isinstance(item, FinalizedLapBatch):
            if item.identity.is_player:
                self._player_skipped += 1
                return False
            if item.invalidated or not item.groups:
                self._incomplete_skipped += 1
                return False
        if not self.running:
            return False
        if isinstance(item, BranchInvalidation):
            for batch_id in item.affected_batch_ids:
                if batch_id in self._invalidated_batch_ids:
                    continue
                if (
                    len(self._invalidated_batch_order)
                    == self._invalidated_batch_order.maxlen
                ):
                    oldest = self._invalidated_batch_order.popleft()
                    self._invalidated_batch_ids.discard(oldest)
                self._invalidated_batch_order.append(batch_id)
                self._invalidated_batch_ids.add(batch_id)
            try:
                self._invalidation_queue.put_nowait(item)
            except asyncio.QueueFull:
                self._invalidation_queue_drops += 1
                self._reconciliation_required = True
                return False
            self._submitted += 1
            return True
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self._queue_drops += 1
            return False
        self._submitted += 1
        self._queue_high_water = max(self._queue_high_water, self.queue.qsize())
        return True

    async def _worker(self) -> None:
        while True:
            source = "invalidation"
            try:
                item: ArchiveItem = self._invalidation_queue.get_nowait()
            except asyncio.QueueEmpty:
                source = "normal"
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=0.05)
                except TimeoutError:
                    continue
            try:
                if isinstance(item, BranchInvalidation):
                    await asyncio.to_thread(self._persist_invalidation, item)
                    self._invalidations += 1
                else:
                    await asyncio.to_thread(self._persist_batch, item)
                    self._persisted_laps += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate optional field archive
                self._write_errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("Full-field lap archive deferred: %s", exc)
            finally:
                if source == "invalidation":
                    self._invalidation_queue.task_done()
                else:
                    self.queue.task_done()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _ensure_catalog_rows(
        self,
        db: sqlite3.Connection,
        batch: FinalizedLapBatch,
        resolved_lap_id: str,
    ) -> None:
        context = dict(batch.context)
        now = self._now()
        track_id = int(context.get("track_id", -1) or -1)
        layout = str(
            context.get("layout_signature")
            or f"f1:{int(context.get('packet_format', 0) or 0)}:{track_id}"
        )
        db.execute(
            """
            INSERT INTO recorded_sessions(
                id, game_session_uid, restart_epoch, track_id,
                track_layout_signature, session_type, mode_profile, started_at,
                status, packet_format, capture_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recording', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id,
                track_layout_signature=excluded.track_layout_signature,
                session_type=excluded.session_type,
                packet_format=excluded.packet_format,
                updated_at=excluded.updated_at
            """,
            (
                batch.session.id,
                batch.session.game_session_uid,
                batch.session.restart_epoch,
                track_id,
                layout,
                str(context.get("session_type", "Unknown")),
                str(context.get("mode_profile", "unknown")),
                now,
                int(context.get("packet_format", 0) or 0),
                str(context.get("capture_mode", "balanced")),
                now,
                now,
            ),
        )
        identity = batch.identity
        db.execute(
            """
            INSERT INTO session_cars(
                id, session_id, car_index, identity_revision, driver_id,
                network_id, display_name, anonymized_name, race_number, team_id,
                nationality_id, is_ai, is_player, first_frame, last_frame,
                change_reason, identity_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_frame=MAX(session_cars.last_frame, excluded.last_frame),
                display_name=COALESCE(excluded.display_name, session_cars.display_name),
                identity_confidence=MAX(session_cars.identity_confidence,
                                        excluded.identity_confidence)
            """,
            (
                identity.id,
                batch.session.id,
                identity.car_index,
                identity.identity_revision,
                identity.driver_id,
                identity.network_id,
                identity.public_name(),
                identity.anonymized_name,
                identity.race_number,
                identity.team_id,
                identity.nationality_id,
                None if identity.is_ai is None else int(identity.is_ai),
                int(identity.is_player),
                identity.first_frame,
                identity.last_frame,
                identity.change_reason,
                identity.confidence,
            ),
        )
        db.execute(
            """
            INSERT INTO recorded_laps(
                id, session_car_id, lap_number, timeline_epoch, lap_time_ms,
                valid, invalid_reason_mask, tyre_compound, tyre_age_laps,
                fuel_start_kg, weather_class, pit_context, flag_context,
                coverage_ratio, quality_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_car_id, lap_number, timeline_epoch) DO UPDATE SET
                lap_time_ms=COALESCE(excluded.lap_time_ms, recorded_laps.lap_time_ms),
                valid=excluded.valid,
                invalid_reason_mask=excluded.invalid_reason_mask,
                coverage_ratio=MAX(recorded_laps.coverage_ratio,
                                   excluded.coverage_ratio),
                quality_score=MAX(recorded_laps.quality_score,
                                  excluded.quality_score)
            """,
            (
                resolved_lap_id,
                identity.id,
                batch.lap_number,
                batch.timeline_epoch,
                batch.lap_time_ms,
                1 if batch.valid is not False and batch.complete else 0,
                0 if batch.valid is not False else 1,
                context.get("tyre_compound"),
                context.get("tyre_age_laps"),
                context.get("fuel_start_kg", context.get("fuel_kg")),
                context.get("weather_class"),
                1 if context.get("pit_context") else 0,
                1 if context.get("flag_context") else 0,
                batch.coverage_ratio,
                batch.quality_score,
                now,
            ),
        )

    def _persist_batch(self, batch: FinalizedLapBatch) -> None:
        if batch.batch_id in self._invalidated_batch_ids:
            return
        resolved_lap_id = lap_id(
            batch.identity.id, batch.lap_number, batch.timeline_epoch
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_catalog_rows(db, batch, resolved_lap_id)
            existing = db.execute(
                "SELECT trace_manifest_id FROM recorded_laps WHERE id=?",
                (resolved_lap_id,),
            ).fetchone()
            db.commit()

        if existing is not None and existing["trace_manifest_id"]:
            return

        manifest_id = "tm_" + hashlib.sha256(batch.batch_id.encode()).hexdigest()[:24]
        try:
            try:
                manifest = self.trace_store.load_manifest(manifest_id)
            except FileNotFoundError:
                for group in batch.groups:
                    group.append_to(self.trace_store, batch.identity.id)
                manifest = self.trace_store.finalize_lap(
                    resolved_lap_id,
                    session_car_id=batch.identity.id,
                    manifest_id=manifest_id,
                )
        except Exception:
            self.trace_store.abort_pending(batch.identity.id)
            self.trace_store.discard_unregistered_manifest(manifest_id)
            raise
        fields = sorted({field for chunk in manifest.chunks for field in chunk.fields})
        total_samples = manifest.sample_count
        weighted = sum(
            chunk.sample_count
            * (
                sum(chunk.coverage.values()) / len(chunk.coverage)
                if chunk.coverage
                else 0.0
            )
            for chunk in manifest.chunks
        )
        coverage = weighted / total_samples if total_samples else 0.0
        manifest_json = json.dumps(
            manifest.to_dict(), sort_keys=True, separators=(",", ":")
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                INSERT INTO trace_manifests(
                    id, session_id, session_car_id, lap_id, encoding_version,
                    axis_type, field_mask, sample_count, coverage_ratio,
                    checksum, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                """,
                    (
                        manifest.id,
                        batch.session.id,
                        batch.identity.id,
                        resolved_lap_id,
                        manifest.encoding_version,
                        manifest.chunks[0].axis_field,
                        json.dumps(fields, separators=(",", ":")).encode(),
                        total_samples,
                        coverage,
                        hashlib.sha256(manifest_json.encode()).hexdigest(),
                        self._now(),
                    ),
                )
                for chunk in manifest.chunks:
                    chunk_id = hashlib.sha256(
                        f"{manifest.id}:{chunk.ordinal}".encode()
                    ).hexdigest()[:24]
                    db.execute(
                        """
                    INSERT INTO trace_chunks(
                        id, manifest_id, ordinal, relative_path, start_axis,
                        end_axis, sample_count, byte_count, checksum, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')
                    """,
                        (
                            f"chk_{chunk_id}",
                            manifest.id,
                            chunk.ordinal,
                            chunk.relative_path,
                            float(chunk.axis_min or 0.0),
                            float(chunk.axis_max or 0.0),
                            chunk.sample_count,
                            chunk.byte_count,
                            chunk.checksum,
                        ),
                    )
                db.execute(
                    """
                    INSERT INTO full_field_lap_batches(
                        batch_id, session_id, lap_id, timeline_epoch,
                        first_overall_frame, last_overall_frame,
                        started_session_time_s, ended_session_time_s,
                        finalization_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id) DO NOTHING
                    """,
                    (
                        batch.batch_id,
                        batch.session.id,
                        resolved_lap_id,
                        batch.timeline_epoch,
                        batch.first_overall_frame,
                        batch.last_overall_frame,
                        batch.started_session_time_s,
                        batch.ended_session_time_s,
                        batch.finalization_reason,
                        self._now(),
                    ),
                )
                db.execute(
                    """
                    UPDATE recorded_laps
                    SET trace_manifest_id=?, fuel_end_kg=?
                    WHERE id=?
                    """,
                    (manifest.id, batch.context.get("fuel_end_kg"), resolved_lap_id),
                )
                db.commit()
        except Exception:
            self.trace_store.abort_pending(batch.identity.id)
            self.trace_store.discard_unregistered_manifest(manifest_id)
            raise

    def _persist_invalidation(self, invalidation: BranchInvalidation) -> None:
        # Branch evidence is more important than pretending the old normalized
        # lap stayed valid. Raw capture remains untouched and replayable.
        with self._connect() as db:
            affected_rows = db.execute(
                """
                SELECT DISTINCT lap_id
                FROM full_field_lap_batches
                WHERE session_id=? AND timeline_epoch=?
                  AND (last_overall_frame>? OR ended_session_time_s>?)
                """,
                (
                    invalidation.session.id,
                    invalidation.invalidated_timeline_epoch,
                    invalidation.target_overall_frame_identifier,
                    invalidation.target_session_time_s,
                ),
            ).fetchall()
            affected_laps = [str(row["lap_id"]) for row in affected_rows]
            if affected_laps:
                placeholders = ",".join("?" for _ in affected_laps)
                db.execute(
                    f"""
                    UPDATE recorded_laps
                    SET valid=0, invalid_reason_mask=(invalid_reason_mask | 2)
                    WHERE id IN ({placeholders})
                    """,
                    affected_laps,
                )
                db.execute(
                    f"""
                    UPDATE comparisons SET state='stale'
                    WHERE candidate_lap_id IN ({placeholders})
                       OR reference_key IN ({placeholders})
                    """,
                    [*affected_laps, *affected_laps],
                )
            db.execute(
                """
                INSERT INTO audit_events(event_type, subject_id, detail_json, created_at)
                VALUES ('timeline_invalidated', ?, ?, ?)
                """,
                (
                    invalidation.session.id,
                    json.dumps(asdict(invalidation), sort_keys=True, default=str),
                    self._now(),
                ),
            )

    async def stop(self, *, drain_timeout_s: float = 10.0) -> None:
        task = self._task
        if task is None:
            self._state = "off"
            return
        self._state = "draining"
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._invalidation_queue.join(),
                    self.queue.join(),
                ),
                timeout=drain_timeout_s,
            )
        except TimeoutError:
            while True:
                try:
                    self._invalidation_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._invalidation_queue.task_done()
                self._invalidation_queue_drops += 1
                self._reconciliation_required = True
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
        self._task = None
        self._state = "off"

    def snapshot(self) -> FullFieldArchiveSnapshot:
        return FullFieldArchiveSnapshot(
            self._state,
            self.queue.qsize(),
            self.queue.maxsize,
            self._queue_high_water,
            self._submitted,
            self._persisted_laps,
            self._invalidations,
            self._player_skipped,
            self._incomplete_skipped,
            self._queue_drops,
            self._write_errors,
            self._last_error,
            self._invalidation_queue.qsize(),
            self._invalidation_queue.maxsize,
            self._invalidation_queue_drops,
            self._reconciliation_required,
        )


__all__ = ["FullFieldArchiveService", "FullFieldArchiveSnapshot"]
