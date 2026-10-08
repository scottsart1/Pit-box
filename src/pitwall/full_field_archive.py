"""Bounded asynchronous persistence for normalized opponent lap batches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias

from f1.packets import SESSIONS

from .catalog import SessionCatalog, lap_id, session_id
from .lap_history import authoritative_timing
from .session_assembler import BranchInvalidation, FinalizedLapBatch
from .trace_store import TraceStore

# The protocol's own names for the session type enum. Only a fallback here:
# the live classifier owns the semantic label, because the effective one also
# weighs the manual override and the weekend structure.
SESSION_TYPE_LABELS = {int(type_id): label for type_id, label in SESSIONS.items()}

log = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class FieldHistoryUpdate:
    context: dict[str, Any]
    history: list[dict[str, Any]]
    identity_key: tuple[Any, ...] = ()
    fingerprint: str = ""


ArchiveItem: TypeAlias = FinalizedLapBatch | BranchInvalidation | FieldHistoryUpdate


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
    out_of_scope_batches_skipped: int = 0
    history_laps_reconciled: int = 0
    history_updates_processed: int = 0
    history_updates_discarded: int = 0
    history_updates_coalesced: int = 0
    history_empty_skipped: int = 0


def cars_in_trace_scope(state: dict[str, Any]) -> set[int] | None:
    """Car indices whose full traces are worth keeping, or None for everyone.

    Storing every sampled channel for all 24 cars every lap of a race is far
    more than the analysis ever reads back, and the write pressure is what
    made per-car telemetry land sporadically rather than completely. In a
    race the cars that actually get compared are the player, the teammate,
    the podium, and the cars racing the player — so those are kept in full.

    Practice and qualifying return None: there is no traffic to speak of, the
    sessions are short, and full-field lap comparison is the entire point of
    them.
    """
    if str(state.get("mode_profile", "")) not in {"race", "sprint"}:
        return None

    drivers = state.get("drivers", []) or []
    player_index = int(state.get("player_car_index", -1))
    keep: set[int] = {player_index} if player_index >= 0 else set()

    player = next(
        (d for d in drivers if int(d.get("car_idx", -1)) == player_index), None
    )
    player_grid = int((player or {}).get("grid_position", 0) or 0)
    player_team = int((player or {}).get("team_id", -1)) if player else -1

    for driver in drivers:
        index = int(driver.get("car_idx", -1))
        if index < 0:
            continue
        if driver.get("is_teammate") or (
            player_team >= 0 and int(driver.get("team_id", -2)) == player_team
        ):
            keep.add(index)
        if 1 <= int(driver.get("position", 0) or 0) <= 3:
            keep.add(index)
        # The cars the player started among: the race-long comparison set,
        # fixed at the grid so it does not churn as positions swap.
        grid = int(driver.get("grid_position", 0) or 0)
        if player_grid and grid and abs(grid - player_grid) <= 2:
            keep.add(index)
    return keep


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
        self.catalog = SessionCatalog(self.database_path)
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
        # None means keep everything, which is the default until a live race
        # narrows it. Never inferred here: the service cannot see positions.
        self._scope: set[int] | None = None
        self._out_of_scope_skipped = 0
        self._history_laps_reconciled = 0
        self._history_updates_processed = 0
        self._history_updates_discarded = 0
        self._history_updates_coalesced = 0
        self._history_empty_skipped = 0
        # Pending entries include the in-flight write. Keep their order: A, B,
        # then A is a real correction, even if the first A already succeeded.
        # Pending memory is bounded by the archive queue plus its worker.
        self._history_pending: dict[tuple[Any, ...], deque[tuple[int, str]]] = {}
        self._history_succeeded: OrderedDict[tuple[Any, ...], str] = OrderedDict()
        self._replacement_epochs: dict[str, int] = {}

    def set_trace_scope(self, indices: set[int] | None) -> None:
        """Restrict full-trace archiving to these car indices (None = all)."""
        self._scope = None if indices is None else set(indices)

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
            if (
                self._scope is not None
                and int(item.identity.car_index) not in self._scope
            ):
                self._out_of_scope_skipped += 1
                return False
        if not self.running:
            return False
        if isinstance(item, FieldHistoryUpdate):
            prepared = self._prepare_history(item)
            if prepared is None:
                return False
            item = prepared
        if isinstance(item, BranchInvalidation):
            self._replacement_epochs[item.session.id] = max(
                item.replacement_timeline_epoch, self._replacement_epochs.get(item.session.id, 0)
            )
            if len(self._replacement_epochs) > 128:
                self._replacement_epochs.pop(next(iter(self._replacement_epochs)))
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
        if isinstance(item, FieldHistoryUpdate):
            self._history_pending.setdefault(item.identity_key, deque()).append((id(item), item.fingerprint))
        self._queue_high_water = max(self._queue_high_water, self.queue.qsize())
        return True

    def submit_history(self, context: dict[str, Any], history: list[dict[str, Any]]) -> bool:
        """Keep all-car timing even when full traces are outside capture scope."""
        return self.submit(FieldHistoryUpdate(context, history))

    def _prepare_history(self, item: FieldHistoryUpdate) -> FieldHistoryUpdate | None:
        completed = [(row, authoritative_timing(row)) for row in item.history]
        unusable = [{name: int(row.get(name, 0) or 0) for name in (
            "lap_num", "lap_ms", "s1_ms", "s2_ms", "s3_ms", "valid_flags")}
            for row, timing in completed if not timing and int(row.get("lap_ms", 0) or 0) > 0]
        completed = [(row, timing) for row, timing in completed if timing]
        # One complete empty snapshot can prove there are no retained laps
        # after a rewind. Repeats coalesce just like nonempty history; an
        # incomplete empty observation has no authority to change the archive.
        if not completed and not item.context.get("history_complete"):
            self._history_empty_skipped += 1
            return None
        context = item.context
        key = (str(context["session_uid"]), *(int(context.get(name, 0) or 0) for name in (
            "restart_epoch", "timeline_epoch", "player_car_index", "identity_revision", "session_generation")))
        # Packet frame/time and identity.last_frame advance even when every
        # completed lap is unchanged. They are not new timing authority.
        stable_context = {name: value for name, value in context.items() if name not in {
            "frame_identifier", "overall_frame_identifier", "session_time_s", "history_updated_at"}}
        if isinstance(stable_context.get("history_identity"), dict):
            stable_context["history_identity"] = {
                name: value for name, value in stable_context["history_identity"].items() if name != "last_frame"}
        fingerprint = hashlib.sha256(json.dumps(
            [stable_context, [{"lap_num": int(row["lap_num"]), **timing} for row, timing in completed], unusable],
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        pending = self._history_pending.get(key)
        latest = pending[-1][1] if pending else self._history_succeeded.get(key)
        if latest == fingerprint:
            self._history_updates_coalesced += 1
            return None
        # Preserve raw coverage evidence for reconciliation. A malformed
        # positive lap is not interchangeable with zero completed laps.
        return FieldHistoryUpdate(deepcopy(context), deepcopy(item.history), key, fingerprint)

    def _finish_history(self, item: FieldHistoryUpdate, *, succeeded: bool) -> None:
        pending = self._history_pending.get(item.identity_key)
        if pending:
            # Normal completion is FIFO; shutdown may drop queued entries
            # before cancelling the in-flight item, so release this item only.
            pending.remove((id(item), item.fingerprint))
            if not pending:
                self._history_pending.pop(item.identity_key, None)
        if succeeded:
            self._history_succeeded[item.identity_key] = item.fingerprint
            self._history_succeeded.move_to_end(item.identity_key)
            while len(self._history_succeeded) > 128:
                self._history_succeeded.popitem(last=False)
        else:
            # A failed/cancelled write must never suppress the next identical
            # packet. Nor may an abandoned timeline become a successful cache.
            self._history_succeeded.pop(item.identity_key, None)

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
            history_succeeded = False
            try:
                if isinstance(item, BranchInvalidation):
                    await asyncio.to_thread(self._persist_invalidation, item)
                    self._invalidations += 1
                elif isinstance(item, FieldHistoryUpdate):
                    key = session_id(item.context["session_uid"], int(item.context.get("restart_epoch", 0) or 0))
                    if int(item.context.get("timeline_epoch", 0) or 0) >= self._replacement_epochs.get(key, 0):
                        reconciled = await self.catalog.reconcile_field_history(item.context, item.history)
                        self._history_laps_reconciled += len(reconciled)
                        self._history_updates_processed += 1
                        history_succeeded = True
                    else:
                        self._history_updates_discarded += 1
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
                if isinstance(item, FieldHistoryUpdate):
                    self._finish_history(item, succeeded=history_succeeded)
                if source == "invalidation":
                    self._invalidation_queue.task_done()
                else:
                    self.queue.task_done()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path, timeout=15)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection

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
        # Track 0 is a real circuit (Melbourne), not a missing value.
        raw_track = context.get("track_id")
        track_id = int(raw_track) if raw_track is not None else -1
        layout = str(
            context.get("layout_signature")
            or f"f1:{int(context.get('packet_format', 0) or 0)}:{track_id}"
        )
        # This writer only ever sees the protocol enum, so it must not touch
        # the semantic session_type: writing str(16) here replaced a catalog
        # row already correctly reading "Race 2" with the number, and Session
        # Review showed a protocol id where the session name belongs. The enum
        # keeps its own column, and the label is written only as the seed for
        # a row this writer reaches before the live classifier has made one.
        raw_session_type = context.get("session_type")
        raw_session_type_id = (
            raw_session_type if isinstance(raw_session_type, int) else None
        )
        seed_label = (
            "Unknown"
            if raw_session_type_id is None
            else SESSION_TYPE_LABELS.get(raw_session_type_id, "Unknown")
        )
        db.execute(
            """
            INSERT INTO recorded_sessions(
                id, game_session_uid, restart_epoch, track_id,
                track_layout_signature, session_type, raw_session_type_id,
                mode_profile, started_at, status, packet_format, capture_mode,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'recording', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_id=excluded.track_id,
                track_layout_signature=excluded.track_layout_signature,
                raw_session_type_id=excluded.raw_session_type_id,
                packet_format=excluded.packet_format,
                updated_at=excluded.updated_at
            """,
            (
                batch.session.id,
                batch.session.game_session_uid,
                batch.session.restart_epoch,
                track_id,
                layout,
                seed_label,
                raw_session_type_id,
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
        existing = db.execute(
            "SELECT engineering_json, invalid_reason_mask FROM recorded_laps WHERE id=?", (resolved_lap_id,),
        ).fetchone()
        recorded = json.loads(existing["engineering_json"] or "{}") if existing else {}
        frozen = {**context, "session_uid": int(batch.session.game_session_uid),
                  "restart_epoch": batch.session.restart_epoch, "timeline_epoch": batch.timeline_epoch,
                  "player_car_index": identity.car_index, "identity_revision": identity.identity_revision,
                  "lap_num": batch.lap_number, "lap_time_ms": batch.lap_time_ms,
                  "valid": batch.valid is not False and batch.complete}
        frozen = SessionCatalog._merge_authoritative_timing(frozen, recorded)
        if recorded.get("context_observed") is False and not frozen.get("telemetry_timing_mismatch"):
            # Packet11 may arrive before the matching observed lap batch. Only
            # measured whole-lap coverage can replace the timing-only marker;
            # a nonempty fragment is not evidence of a complete recording.
            length = float(context.get("track_length_m", 0) or 0)
            distances = [float(sample["lap_distance_m"]) for group in batch.groups for sample in group.samples
                         if isinstance(sample.get("lap_distance_m"), (int, float))]
            span = min(1.0, max(0.0, (max(distances) - min(distances)) / length)) if length > 0 and distances else 0.0
            frozen["trace_coverage"] = min(span, batch.coverage_ratio)
            if batch.complete and span >= .9:
                frozen["context_observed"] = True
                frozen["trace_incomplete"] = False
                frozen["learning_exclusions"] = [value for value in frozen.get("learning_exclusions", []) if value != "missing_telemetry"]
            else:
                frozen["trace_incomplete"] = True
        invalid_mask = int(existing["invalid_reason_mask"] or 0) & ~1 if existing else 0
        valid = bool(frozen["valid"]) and not (invalid_mask & 2)
        coverage = float(frozen.get("trace_coverage", batch.coverage_ratio))
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
                tyre_compound=COALESCE(excluded.tyre_compound, recorded_laps.tyre_compound),
                tyre_age_laps=COALESCE(excluded.tyre_age_laps, recorded_laps.tyre_age_laps),
                fuel_start_kg=COALESCE(excluded.fuel_start_kg, recorded_laps.fuel_start_kg),
                weather_class=COALESCE(excluded.weather_class, recorded_laps.weather_class),
                pit_context=excluded.pit_context,
                flag_context=excluded.flag_context,
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
                frozen["lap_time_ms"],
                int(valid),
                invalid_mask | (0 if valid else 1),
                context.get("tyre_compound"),
                context.get("tyre_age_laps"),
                context.get("fuel_start_kg", context.get("fuel_kg")),
                context.get("weather_class"),
                1 if context.get("pit_context") else 0,
                1 if context.get("flag_context") else 0,
                coverage,
                batch.quality_score,
                now,
            ),
        )
        db.execute("UPDATE recorded_laps SET engineering_json=? WHERE id=?",
                   (json.dumps(frozen, allow_nan=False), resolved_lap_id))
        if frozen.get("telemetry_timing_mismatch"):
            db.execute("UPDATE recorded_laps SET coverage_ratio=0, quality_score=MIN(quality_score,0.2) WHERE id=?", (resolved_lap_id,))

    def _persist_batch(self, batch: FinalizedLapBatch) -> None:
        if batch.batch_id in self._invalidated_batch_ids:
            return
        if batch.timeline_epoch < self._replacement_epochs.get(batch.session.id, 0):
            return
        with self._connect() as db:
            if batch.timeline_epoch < SessionCatalog.history_replacement_epoch(db, batch.session.id):
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
            db.execute("BEGIN IMMEDIATE")
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
            SessionCatalog.defer_timing_only_laps(
                db, invalidation.session.id, invalidation.invalidated_timeline_epoch,
                invalidation.replacement_timeline_epoch,
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
                    dropped = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(dropped, FieldHistoryUpdate):
                    self._finish_history(dropped, succeeded=False)
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
            self._out_of_scope_skipped,
            self._history_laps_reconciled,
            self._history_updates_processed,
            self._history_updates_discarded,
            self._history_updates_coalesced,
            self._history_empty_skipped,
        )


__all__ = [
    "FullFieldArchiveService",
    "FullFieldArchiveSnapshot",
    "cars_in_trace_scope",
]
