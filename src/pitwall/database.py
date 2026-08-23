from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import SessionCatalog
from .migrations import LATEST_SCHEMA_VERSION, MIGRATIONS

log = logging.getLogger(__name__)

_SQLITE_INT64_MIN = -(1 << 63)
_SQLITE_INT64_MAX = (1 << 63) - 1
_UINT64_MODULUS = 1 << 64
_UINT64_MAX = _UINT64_MODULUS - 1


def _session_uid_to_sqlite(value: Any) -> int:
    """Map the game's unsigned 64-bit session UID into SQLite's signed INTEGER.

    F1 telemetry exposes ``sessionUID`` as uint64, while SQLite INTEGER is a
    signed 64-bit value.  Values above 2**63-1 therefore raise OverflowError
    unless they are stored using their two's-complement signed equivalent.
    This mapping is one-to-one for the complete uint64 range.
    """
    uid = int(value or 0)
    if _SQLITE_INT64_MIN <= uid <= _SQLITE_INT64_MAX:
        return uid
    if 0 <= uid <= _UINT64_MAX:
        return uid - _UINT64_MODULUS
    raise ValueError(f"session_uid is outside the supported 64-bit range: {uid}")


def _session_uid_from_sqlite(value: Any) -> int:
    """Restore a signed SQLite INTEGER to the telemetry uint64 value."""
    uid = int(value or 0)
    return uid + _UINT64_MODULUS if uid < 0 else uid


def _restore_session_uid(record: dict[str, Any]) -> dict[str, Any]:
    if "session_uid" in record:
        record["session_uid"] = _session_uid_from_sqlite(record["session_uid"])
    return record


class PitWallDatabase:
    """Small SQLite cold store for laps, traces, corners, setups and feedback."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.last_backup_path: Path | None = None
        self.schema_version = 0
        self.catalog = SessionCatalog(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    @contextmanager
    def _migration_file_lock_sync(
        self, timeout_s: float = 30.0
    ) -> Iterator[None]:
        """Serialize schema inspection, backup, and migration across processes."""

        lock_path = self.path.with_name(f"{self.path.name}.migration.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        locked = False
        try:
            while not locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Timed out waiting for another Your Pit Box database "
                            "migration to finish"
                        ) from exc
                    time.sleep(0.05)
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _schema_versions_table_sql() -> str:
        return """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                app_version TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
        """

    def _current_schema_version_sync(self) -> int:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_versions'"
            ).fetchone()
            if not exists:
                return 0
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_versions"
            ).fetchone()
            return int(row[0] if row else 0)
        finally:
            connection.close()

    def _integrity_check_sync(self, *, quick: bool = True) -> list[str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return ["ok"]
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            pragma = "quick_check" if quick else "integrity_check"
            return [str(row[0]) for row in connection.execute(f"PRAGMA {pragma}")]
        finally:
            connection.close()

    def _check_migration_space_sync(self) -> None:
        size = self.path.stat().st_size if self.path.exists() else 0
        free = shutil.disk_usage(self.path.parent).free
        # SQLite backup plus migration/WAL headroom.  The fixed floor avoids a
        # misleading successful preflight on a nearly full drive and remains
        # small enough for normal temporary test volumes.
        required = max(size * 2 + 8 * 1024 * 1024, 16 * 1024 * 1024)
        if free < required:
            raise RuntimeError(
                "Not enough free disk space to back up and migrate Your Pit Box "
                f"(need {required} bytes, have {free} bytes)"
            )

    def _backup_sync(self) -> Path:
        backups = self.path.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = backups / f"{self.path.stem}-pre-v42-{stamp}.sqlite3"
        source_db = sqlite3.connect(self.path, timeout=30)
        backup_db = sqlite3.connect(destination, timeout=30)
        try:
            source_db.backup(backup_db)
            backup_db.commit()
            check = [str(row[0]) for row in backup_db.execute("PRAGMA quick_check")]
            if check != ["ok"]:
                raise RuntimeError(f"SQLite backup integrity check failed: {check}")
        finally:
            backup_db.close()
            source_db.close()
        return destination

    def _apply_versioned_migrations_sync(self) -> None:
        with self._connect() as db:
            db.execute(self._schema_versions_table_sql())
            applied = {
                int(row["version"]): str(row["checksum"])
                for row in db.execute(
                    "SELECT version, checksum FROM schema_versions"
                ).fetchall()
            }
            for migration in MIGRATIONS:
                prior_checksum = applied.get(migration.version)
                if prior_checksum is not None:
                    if prior_checksum != migration.checksum:
                        raise RuntimeError(
                            "Applied migration checksum does not match this build: "
                            f"{migration.version}"
                        )
                    continue
                try:
                    db.execute("BEGIN IMMEDIATE")
                    for statement in migration.statements:
                        db.execute(statement)
                    db.execute(
                        """
                        INSERT INTO schema_versions(version, applied_at, app_version, checksum)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            migration.version,
                            datetime.now(UTC).isoformat(),
                            migration.app_version,
                            migration.checksum,
                        ),
                    )
                    db.execute(f"PRAGMA user_version={migration.version}")
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
            row = db.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_versions"
            ).fetchone()
            self.schema_version = int(row["version"] if row else 0)

    async def create_backup(self) -> Path:
        """Create and verify an online SQLite backup under ``data/backups``."""
        async with self._lock:
            path = await asyncio.to_thread(self._backup_sync)
            self.last_backup_path = path
            return path

    def _restore_backup_sync(self, backup_path: Path) -> None:
        backup_root = (self.path.parent / "backups").resolve()
        resolved = backup_path.resolve()
        try:
            resolved.relative_to(backup_root)
        except ValueError as exc:
            raise ValueError("backup path must be inside the Your Pit Box backup directory") from exc
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        source = sqlite3.connect(resolved, timeout=30)
        destination = sqlite3.connect(self.path, timeout=30)
        try:
            check = [str(row[0]) for row in source.execute("PRAGMA quick_check")]
            if check != ["ok"]:
                raise RuntimeError(f"Refusing to restore a corrupt backup: {check}")
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()

    async def restore_backup(self, backup_path: Path) -> Path:
        """Restore an explicitly selected verified backup.

        A safety image of the current database is created first, so even an
        intentional rollback remains recoverable. Runtime services must be
        stopped before this administrative operation is invoked.
        """
        async with self._lock:
            safety_backup = await asyncio.to_thread(self._backup_sync)
            await asyncio.to_thread(self._restore_backup_sync, backup_path)
            checks = await asyncio.to_thread(self._integrity_check_sync, quick=True)
            if checks != ["ok"]:
                raise RuntimeError(
                    "Restored database failed integrity verification; current-state "
                    f"backup retained at {safety_backup}"
                )
            self.schema_version = await asyncio.to_thread(
                self._current_schema_version_sync
            )
            self.last_backup_path = safety_backup
            return safety_backup

    async def integrity_report(self, *, deep: bool = False) -> dict[str, Any]:
        checks = await asyncio.to_thread(self._integrity_check_sync, quick=not deep)
        return {
            "ok": checks == ["ok"],
            "checks": checks,
            "schema_version": self.schema_version,
            "latest_schema_version": LATEST_SCHEMA_VERSION,
            "last_backup": str(self.last_backup_path) if self.last_backup_path else None,
        }

    def _initialize_sync(self) -> None:
        with self._migration_file_lock_sync():
            self._initialize_locked_sync()

    def _initialize_locked_sync(self) -> None:
        existing_database = self.path.exists() and self.path.stat().st_size > 0
        current_version = self._current_schema_version_sync()
        if current_version > LATEST_SCHEMA_VERSION:
            raise RuntimeError(
                "Your Pit Box database schema is newer than this application "
                f"({current_version} > {LATEST_SCHEMA_VERSION}); refusing a downgrade"
            )
        pending_migration = current_version < LATEST_SCHEMA_VERSION
        if existing_database and pending_migration:
            checks = self._integrity_check_sync(quick=True)
            if checks != ["ok"]:
                deep_checks = self._integrity_check_sync(quick=False)
                raise RuntimeError(
                    "Your Pit Box database failed its pre-migration integrity check: "
                    f"{deep_checks}"
                )
            self._check_migration_space_sync()
            self.last_backup_path = self._backup_sync()
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_uid INTEGER PRIMARY KEY,
                    track_id INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    mode_profile TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    result_position INTEGER,
                    total_laps INTEGER,
                    setup_json TEXT
                );

                CREATE TABLE IF NOT EXISTS laps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    lap_num INTEGER NOT NULL,
                    lap_time_ms INTEGER NOT NULL,
                    valid INTEGER NOT NULL,
                    compound TEXT,
                    tyre_age_start INTEGER,
                    tyre_age_end INTEGER,
                    wear_start_json TEXT,
                    wear_end_json TEXT,
                    temps_end_json TEXT,
                    track_temp_c REAL,
                    air_temp_c REAL,
                    weather TEXT,
                    mode_profile TEXT,
                    fuel_start_kg REAL,
                    fuel_end_kg REAL,
                    position INTEGER,
                    s1_ms INTEGER DEFAULT 0,
                    s2_ms INTEGER DEFAULT 0,
                    s3_ms INTEGER DEFAULT 0,
                    pit_status INTEGER DEFAULT 0,
                    pit_lane_time_ms INTEGER DEFAULT 0,
                    setup_json TEXT,
                    trace_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(session_uid, lap_num)
                );

                CREATE INDEX IF NOT EXISTS idx_laps_track_valid_time
                    ON laps(track_id, valid, lap_time_ms);
                CREATE INDEX IF NOT EXISTS idx_laps_session
                    ON laps(session_uid, lap_num);

                CREATE TABLE IF NOT EXISTS corner_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    lap_num INTEGER NOT NULL,
                    corner_no INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    entry_m REAL NOT NULL,
                    apex_m REAL NOT NULL,
                    exit_m REAL NOT NULL,
                    brake_point_m REAL,
                    brake_peak REAL,
                    min_speed_kph REAL,
                    apex_speed_kph REAL,
                    throttle_on_m REAL,
                    full_throttle_m REAL,
                    gear_at_apex INTEGER,
                    max_lat_g REAL,
                    wheel_lock INTEGER,
                    wheelspin INTEGER,
                    time_in_corner_s REAL,
                    loss_vs_pb_s REAL,
                    cause TEXT,
                    created_at REAL NOT NULL,
                    UNIQUE(session_uid, lap_num, corner_no)
                );

                CREATE INDEX IF NOT EXISTS idx_corners_track
                    ON corner_metrics(track_id, corner_no, loss_vs_pb_s);

                CREATE TABLE IF NOT EXISTS setup_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    setup_json TEXT NOT NULL,
                    performance_json TEXT NOT NULL,
                    score REAL NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_setup_runs_track_profile
                    ON setup_runs(track_id, profile, score);

                CREATE TABLE IF NOT EXISTS setup_recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    track_name TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    setup_json TEXT NOT NULL,
                    rationale_json TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS line_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    lap_num INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(session_uid, lap_num)
                );

                CREATE INDEX IF NOT EXISTS idx_line_metrics_track
                    ON line_metrics(track_id, created_at);

                CREATE TABLE IF NOT EXISTS radio_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    lap_num INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'radio',
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_radio_session
                    ON radio_messages(session_uid, created_at);

                CREATE TABLE IF NOT EXISTS strategy_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    lap_num INTEGER NOT NULL,
                    race_control_phase TEXT NOT NULL,
                    recommended_json TEXT NOT NULL,
                    plans_json TEXT NOT NULL,
                    model_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_session_lap
                    ON strategy_snapshots(session_uid, lap_num, created_at);

                CREATE TABLE IF NOT EXISTS proactive_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    lap_num INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    delivered INTEGER NOT NULL,
                    outcome TEXT NOT NULL DEFAULT '',
                    refusal_reason TEXT NOT NULL DEFAULT '',
                    queued_at REAL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS briefings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_briefings_session_kind
                    ON briefings(session_uid, kind, created_at);

                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_uid INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    lap_num INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            existing = {row[1] for row in db.execute("PRAGMA table_info(laps)").fetchall()}
            migrations = {
                "track_temp_c": "REAL",
                "air_temp_c": "REAL",
                "weather": "TEXT",
                "mode_profile": "TEXT",
            }
            for column, column_type in migrations.items():
                if column not in existing:
                    db.execute(f"ALTER TABLE laps ADD COLUMN {column} {column_type}")
            proactive_existing = {
                row[1]
                for row in db.execute("PRAGMA table_info(proactive_calls)").fetchall()
            }
            proactive_migrations = {
                "outcome": "TEXT NOT NULL DEFAULT ''",
                "refusal_reason": "TEXT NOT NULL DEFAULT ''",
                "queued_at": "REAL",
            }
            for column, column_type in proactive_migrations.items():
                if column not in proactive_existing:
                    db.execute(
                        f"ALTER TABLE proactive_calls ADD COLUMN {column} {column_type}"
                    )
        self._apply_versioned_migrations_sync()
        post_checks = self._integrity_check_sync(quick=True)
        if post_checks != ["ok"]:
            raise RuntimeError(
                "Your Pit Box database failed its post-migration integrity check; "
                f"backup retained at {self.last_backup_path}: {post_checks}"
            )

    async def maintain(
        self,
        *,
        keep_trace_sessions: int = 12,
        vacuum: bool = True,
    ) -> dict[str, Any]:
        """Reclaim space and repair rows written by earlier builds.

        Safe to run at startup. Traces are the reference data for the live delta
        and racing-line comparison, so the fastest valid lap per track always
        keeps its trace regardless of age.
        """
        # Cleanup writes are serialized with normal database work. VACUUM is
        # deliberately outside that lock: on a large historic file it can take
        # minutes, and holding the application lock would stall lap, radio and
        # strategy persistence for the whole operation.
        async with self._lock:
            report = await asyncio.to_thread(
                self._maintain_sync, int(keep_trace_sessions), False
            )
        if vacuum:
            await asyncio.to_thread(self._vacuum_sync)
        report["size_after_bytes"] = await asyncio.to_thread(self._database_size_sync)
        return report

    def _vacuum_sync(self) -> None:
        connection = sqlite3.connect(self.path, timeout=120, isolation_level=None)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()

    def _database_size_sync(self) -> int:
        with self._connect() as db:
            return int(db.execute("PRAGMA page_count").fetchone()[0]) * int(
                db.execute("PRAGMA page_size").fetchone()[0]
            )

    def _maintain_sync(self, keep_trace_sessions: int, vacuum: bool) -> dict[str, Any]:
        report: dict[str, Any] = {}
        with self._connect() as db:
            before = int(db.execute("PRAGMA page_count").fetchone()[0]) * int(
                db.execute("PRAGMA page_size").fetchone()[0]
            )
            report["size_before_bytes"] = before

            # Controller-button events were recorded at packet frequency and
            # carry no race narrative.
            report["button_events_removed"] = db.execute(
                "DELETE FROM session_events WHERE event_type='BUTN'"
            ).rowcount

            # Collapse the strategy snapshots that predate per-change gating:
            # keep the newest row per (session, lap, race-control phase).
            report["strategy_snapshots_removed"] = db.execute(
                """
                DELETE FROM strategy_snapshots WHERE id NOT IN (
                    SELECT MAX(id) FROM strategy_snapshots
                    GROUP BY session_uid, lap_num, race_control_phase
                )
                """
            ).rowcount

            # Repeated chequered-flag packets appended one event each.
            report["duplicate_chequered_removed"] = db.execute(
                """
                DELETE FROM session_events WHERE event_type='CHQF' AND id NOT IN (
                    SELECT MIN(id) FROM session_events
                    WHERE event_type='CHQF' GROUP BY session_uid
                )
                """
            ).rowcount

            # A session cannot finish before it started.
            report["session_times_repaired"] = db.execute(
                "UPDATE sessions SET ended_at=NULL WHERE ended_at IS NOT NULL AND ended_at < started_at"
            ).rowcount

            # Drop 60 Hz traces outside the recent window, preserving each
            # track's reference lap.
            recent = [
                row[0]
                for row in db.execute(
                    "SELECT session_uid FROM sessions ORDER BY started_at DESC LIMIT ?",
                    (max(1, keep_trace_sessions),),
                ).fetchall()
            ]
            placeholders = ",".join("?" for _ in recent) or "NULL"
            report["traces_cleared"] = db.execute(
                f"""
                UPDATE laps SET trace_json='[]'
                WHERE trace_json NOT IN ('[]', '')
                  AND session_uid NOT IN ({placeholders})
                  AND id NOT IN (
                      SELECT id FROM laps l WHERE valid=1 AND lap_time_ms>0
                        AND lap_time_ms=(
                            SELECT MIN(lap_time_ms) FROM laps
                            WHERE track_id=l.track_id AND valid=1 AND lap_time_ms>0
                        )
                  )
                """,
                recent,
            ).rowcount

        if vacuum:
            self._vacuum_sync()
        report["size_after_bytes"] = self._database_size_sync()
        return report

    async def upsert_session(self, state: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_session_sync, state)
        try:
            await self.catalog.upsert_live_session(state)
        except Exception as exc:  # noqa: BLE001 - legacy write remains authoritative
            log.warning("4.2 session catalog update deferred: %s", exc)

    def _upsert_session_sync(self, state: dict[str, Any]) -> None:
        if not state.get("session_uid"):
            return
        # The final-classification packet is the only signal that a session
        # actually finished, so the result is recorded here rather than needing
        # a separate write path. COALESCE keeps a result already on record if a
        # later upsert happens to run without classification data.
        classification = state.get("final_classification") or {}
        position = int(classification.get("position", 0) or 0)
        # One clock reading for both columns. Reading time.time() separately for
        # ended_at and started_at made a session that was first written after the
        # chequered flag finish microseconds *before* it started.
        now = time.time()
        finished_at = now if position > 0 else None
        result_position = position if position > 0 else None
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sessions(
                    session_uid, track_id, track_name, session_type,
                    mode_profile, started_at, ended_at, result_position,
                    total_laps, setup_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_uid) DO UPDATE SET
                    track_id=excluded.track_id,
                    track_name=excluded.track_name,
                    session_type=excluded.session_type,
                    mode_profile=excluded.mode_profile,
                    ended_at=COALESCE(sessions.ended_at, excluded.ended_at),
                    result_position=COALESCE(
                        sessions.result_position, excluded.result_position
                    ),
                    total_laps=excluded.total_laps,
                    setup_json=excluded.setup_json
                """,
                (
                    _session_uid_to_sqlite(state["session_uid"]),
                    int(state.get("track_id", -1)),
                    state.get("track_name", "—"),
                    state.get("session_type", "Unknown"),
                    state.get("mode_profile", "idle"),
                    now,
                    finished_at,
                    result_position,
                    int(state.get("total_laps", 0)),
                    json.dumps(state.get("car_setup", {})),
                ),
            )

    async def save_lap(
        self,
        lap: dict[str, Any],
        corner_metrics: list[dict[str, Any]],
    ) -> str | None:
        async with self._lock:
            legacy_lap_id = await asyncio.to_thread(
                self._save_lap_sync, lap, corner_metrics
            )
        try:
            return await self.catalog.record_player_lap(
                lap, legacy_lap_id=legacy_lap_id
            )
        except Exception as exc:  # noqa: BLE001 - resumable backfill repairs this
            log.warning("4.2 lap catalog update deferred: %s", exc)
            return None

    def _save_lap_sync(
        self,
        lap: dict[str, Any],
        corner_metrics: list[dict[str, Any]],
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO laps(
                    session_uid, track_id, track_name, session_type, lap_num,
                    lap_time_ms, valid, compound, tyre_age_start, tyre_age_end,
                    wear_start_json, wear_end_json, temps_end_json,
                    track_temp_c, air_temp_c, weather, mode_profile,
                    fuel_start_kg, fuel_end_kg, position,
                    s1_ms, s2_ms, s3_ms, pit_status, pit_lane_time_ms,
                    setup_json, trace_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_uid, lap_num) DO UPDATE SET
                    lap_time_ms=excluded.lap_time_ms,
                    valid=excluded.valid,
                    compound=excluded.compound,
                    tyre_age_start=excluded.tyre_age_start,
                    tyre_age_end=excluded.tyre_age_end,
                    wear_start_json=excluded.wear_start_json,
                    wear_end_json=excluded.wear_end_json,
                    temps_end_json=excluded.temps_end_json,
                    track_temp_c=excluded.track_temp_c,
                    air_temp_c=excluded.air_temp_c,
                    weather=excluded.weather,
                    mode_profile=excluded.mode_profile,
                    fuel_start_kg=excluded.fuel_start_kg,
                    fuel_end_kg=excluded.fuel_end_kg,
                    position=excluded.position,
                    s1_ms=excluded.s1_ms,
                    s2_ms=excluded.s2_ms,
                    s3_ms=excluded.s3_ms,
                    pit_status=excluded.pit_status,
                    pit_lane_time_ms=excluded.pit_lane_time_ms,
                    setup_json=excluded.setup_json,
                    trace_json=excluded.trace_json
                """,
                (
                    _session_uid_to_sqlite(lap["session_uid"]),
                    int(lap["track_id"]),
                    lap["track_name"],
                    lap["session_type"],
                    int(lap["lap_num"]),
                    int(lap.get("lap_time_ms", 0)),
                    1 if lap.get("valid") else 0,
                    lap.get("compound", "UNKNOWN"),
                    int(lap.get("tyre_age_start", 0)),
                    int(lap.get("tyre_age_end", 0)),
                    json.dumps(lap.get("wear_start", [])),
                    json.dumps(lap.get("wear_end", [])),
                    json.dumps(lap.get("temps_end", [])),
                    float(lap.get("track_temp_c", 0.0)),
                    float(lap.get("air_temp_c", 0.0)),
                    str(lap.get("weather", "Unknown")),
                    str(lap.get("mode_profile", "")),
                    float(lap.get("fuel_start_kg", 0.0)),
                    float(lap.get("fuel_end_kg", 0.0)),
                    int(lap.get("position", 0)),
                    int(lap.get("s1_ms", 0)),
                    int(lap.get("s2_ms", 0)),
                    int(lap.get("s3_ms", 0)),
                    int(lap.get("pit_status", 0)),
                    int(lap.get("pit_lane_time_ms", 0)),
                    json.dumps(lap.get("setup", {})),
                    json.dumps(lap.get("trace", []), separators=(",", ":")),
                    float(lap.get("created_at", time.time())),
                ),
            )
            self._write_corner_rows_sync(
                db,
                int(lap["session_uid"]),
                int(lap["track_id"]),
                int(lap["lap_num"]),
                corner_metrics,
            )
            row = db.execute(
                "SELECT id FROM laps WHERE session_uid=? AND lap_num=?",
                (
                    _session_uid_to_sqlite(lap["session_uid"]),
                    int(lap["lap_num"]),
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("saved lap could not be read back")
            return int(row["id"])

    @staticmethod
    def _write_corner_rows_sync(
        db: sqlite3.Connection,
        session_uid: int,
        track_id: int,
        lap_num: int,
        corner_metrics: list[dict[str, Any]],
    ) -> None:
        db.execute(
            "DELETE FROM corner_metrics WHERE session_uid=? AND lap_num=?",
            (_session_uid_to_sqlite(session_uid), lap_num),
        )
        for metric in corner_metrics:
            db.execute(
                """
                INSERT INTO corner_metrics(
                    session_uid, track_id, lap_num, corner_no, name,
                    entry_m, apex_m, exit_m, brake_point_m, brake_peak,
                    min_speed_kph, apex_speed_kph, throttle_on_m,
                    full_throttle_m, gear_at_apex, max_lat_g,
                    wheel_lock, wheelspin, time_in_corner_s,
                    loss_vs_pb_s, cause, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(session_uid),
                    track_id,
                    lap_num,
                    int(metric["corner_no"]),
                    metric["name"],
                    float(metric["entry_m"]),
                    float(metric["apex_m"]),
                    float(metric["exit_m"]),
                    metric.get("brake_point_m"),
                    metric.get("brake_peak"),
                    metric.get("min_speed_kph"),
                    metric.get("apex_speed_kph"),
                    metric.get("throttle_on_m"),
                    metric.get("full_throttle_m"),
                    metric.get("gear_at_apex"),
                    metric.get("max_lat_g"),
                    1 if metric.get("wheel_lock") else 0,
                    1 if metric.get("wheelspin") else 0,
                    metric.get("time_in_corner_s"),
                    metric.get("loss_vs_pb_s"),
                    metric.get("cause", ""),
                    time.time(),
                ),
            )

    async def tracks_with_traces(self) -> list[int]:
        """Track ids that have at least one stored lap trace to segment."""
        async with self._lock:
            return await asyncio.to_thread(self._tracks_with_traces_sync)

    def _tracks_with_traces_sync(self) -> list[int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT track_id FROM laps WHERE length(trace_json) > 50"
            ).fetchall()
            return [int(row[0]) for row in rows]

    async def rebuild_track_corners(
        self,
        track_id: int,
        segmenter: Any,
    ) -> int:
        """Build the track's canonical turn model and re-measure every lap.

        ``segmenter(trace, personal_best)`` is AnalysisEngine.segment_corners.
        Exists because laps recorded before the milli-g fix produced one
        lap-length "corner" per lap; the traces themselves were always good,
        so the correct per-corner record is fully recoverable. Runs only when
        the track has no model yet and enough traced laps to trust the shape;
        after it, live laps are measured over the same windows, so the whole
        history stays comparable. Idempotent.
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._rebuild_track_corners_sync, track_id, segmenter
            )

    def _rebuild_track_corners_sync(self, track_id: int, segmenter: Any) -> int:
        from .setup_insights import MODEL_MIN_LAPS, build_turn_model, measure_turns

        if self._load_preference_sync(f"turn_model:{track_id}", None):
            return 0
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT session_uid, lap_num, lap_time_ms, valid, trace_json
                FROM laps
                WHERE track_id=? AND length(trace_json) > 50
                ORDER BY (valid=1 AND lap_time_ms>0) DESC, lap_time_ms ASC
                """,
                (track_id,),
            ).fetchall()
            if len(rows) < MODEL_MIN_LAPS:
                return 0
            traces: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
            for row in rows:
                try:
                    trace = json.loads(row["trace_json"])
                except (TypeError, ValueError):
                    continue
                if isinstance(trace, list) and trace:
                    traces.append((_restore_session_uid(dict(row)), trace))
            model = build_turn_model(
                [segmenter(trace, None) for _, trace in traces]
            )
            if not model:
                return 0
            # The fastest valid lap (first row) provides the reference pass
            # each lap's loss and cause are computed against.
            pb_rows = measure_turns(traces[0][1], model) if traces else []
            rebuilt = 0
            for row, trace in traces:
                metrics = measure_turns(trace, model, pb_rows)
                self._write_corner_rows_sync(
                    db,
                    int(row["session_uid"]),
                    track_id,
                    int(row["lap_num"]),
                    metrics,
                )
                rebuilt += 1
        self._save_preference_sync(f"turn_model:{track_id}", model)
        return rebuilt

    async def corner_rows_for_track(
        self,
        track_id: int,
        max_laps: int = 40,
    ) -> list[dict[str, Any]]:
        """Corner rows for the most recent valid laps at a circuit."""
        async with self._lock:
            return await asyncio.to_thread(
                self._corner_rows_for_track_sync, track_id, max_laps
            )

    def _corner_rows_for_track_sync(
        self, track_id: int, max_laps: int
    ) -> list[dict[str, Any]]:
        # Only canonical rows ("Turn N", fixed-window measurement) are safe to
        # compare across laps; zone rows from unmodelled tracks measure
        # varying extents and would fabricate loss where there is none.
        with self._connect() as db:
            rows = db.execute(
                """
                WITH recent AS (
                    SELECT session_uid, lap_num FROM laps
                    WHERE track_id=? AND valid=1 AND lap_time_ms>0
                    ORDER BY created_at DESC LIMIT ?
                )
                SELECT cm.* FROM corner_metrics cm
                JOIN recent r
                  ON r.session_uid = cm.session_uid AND r.lap_num = cm.lap_num
                WHERE cm.track_id=? AND cm.name LIKE 'Turn %'
                ORDER BY cm.apex_m
                """,
                (track_id, max_laps, track_id),
            ).fetchall()
            return [_restore_session_uid(dict(row)) for row in rows]

    async def lap_trace(
        self, session_uid: int, lap_num: int
    ) -> list[dict[str, Any]]:
        """The stored telemetry trace of one lap (empty when absent)."""
        async with self._lock:
            return await asyncio.to_thread(
                self._lap_trace_sync, session_uid, lap_num
            )

    def _lap_trace_sync(self, session_uid: int, lap_num: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            row = db.execute(
                "SELECT trace_json FROM laps WHERE session_uid=? AND lap_num=?",
                (_session_uid_to_sqlite(session_uid), int(lap_num)),
            ).fetchone()
            if row is None or not row["trace_json"]:
                return []
            try:
                trace = json.loads(row["trace_json"])
            except (TypeError, ValueError):
                return []
            return trace if isinstance(trace, list) else []

    async def recent_lap_traces(
        self, track_id: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Recent valid laps with their stored traces, newest first.

        Feeds whole-history geometry analysis (racing line), so it loads many
        megabytes of JSON — call it from on-demand paths only, never per lap.
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._recent_lap_traces_sync, track_id, limit
            )

    def _recent_lap_traces_sync(
        self, track_id: int, limit: int
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT session_uid, lap_num, lap_time_ms, trace_json FROM laps
                WHERE track_id=? AND valid=1 AND lap_time_ms>0
                  AND length(trace_json) > 100
                ORDER BY created_at DESC LIMIT ?
                """,
                (track_id, limit),
            ).fetchall()
            laps: list[dict[str, Any]] = []
            for row in rows:
                try:
                    trace = json.loads(row["trace_json"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(trace, list):
                    continue
                laps.append(
                    {
                        "session_uid": _restore_session_uid(dict(row))["session_uid"],
                        "lap_num": int(row["lap_num"]),
                        "lap_time_ms": int(row["lap_time_ms"]),
                        "trace": trace,
                    }
                )
            return laps

    async def get_personal_best(self, track_id: int) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_personal_best_sync, track_id)

    def _get_personal_best_sync(self, track_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM laps
                WHERE track_id=? AND valid=1 AND lap_time_ms>0
                ORDER BY lap_time_ms ASC LIMIT 1
                """,
                (track_id,),
            ).fetchone()
            if row is None:
                return None
            result = _restore_session_uid(dict(row))
            result["trace"] = json.loads(result.pop("trace_json"))
            result["setup"] = json.loads(result.pop("setup_json") or "{}")
            result["wear_start"] = json.loads(result.pop("wear_start_json") or "[]")
            result["wear_end"] = json.loads(result.pop("wear_end_json") or "[]")
            result["temps_end"] = json.loads(result.pop("temps_end_json") or "[]")
            corners = db.execute(
                """
                SELECT * FROM corner_metrics
                WHERE session_uid=? AND lap_num=?
                ORDER BY corner_no
                """,
                (_session_uid_to_sqlite(result["session_uid"]), result["lap_num"]),
            ).fetchall()
            result["corner_metrics"] = [_restore_session_uid(dict(item)) for item in corners]
            return result

    async def backfill_lap_sectors(
        self,
        session_uid: int,
        laps: list[dict[str, Any]],
    ) -> int:
        """Fill sector times on rows that were saved before the game reported them.

        The session-history packet usually arrives after a lap has already been
        analysed and persisted, which would otherwise leave the sector columns
        at zero for the rest of the session. Only complete splits are written,
        and only over rows that are still missing one.
        """
        pending: list[tuple[int, int, int, int]] = []
        for lap in laps:
            lap_num = int(lap.get("lap_num", -1))
            sectors = [int(lap.get(key, 0) or 0) for key in ("s1_ms", "s2_ms", "s3_ms")]
            if lap_num >= 0 and all(sectors):
                pending.append((sectors[0], sectors[1], sectors[2], lap_num))
        if not pending:
            return 0
        async with self._lock:
            return await asyncio.to_thread(
                self._backfill_lap_sectors_sync, session_uid, pending
            )

    def _backfill_lap_sectors_sync(
        self,
        session_uid: int,
        pending: list[tuple[int, int, int, int]],
    ) -> int:
        stored_uid = _session_uid_to_sqlite(session_uid)
        with self._connect() as db:
            updated = 0
            for s1_ms, s2_ms, s3_ms, lap_num in pending:
                cursor = db.execute(
                    """
                    UPDATE laps SET s1_ms=?, s2_ms=?, s3_ms=?
                    WHERE session_uid=? AND lap_num=?
                      AND (s1_ms=0 OR s2_ms=0 OR s3_ms=0)
                    """,
                    (s1_ms, s2_ms, s3_ms, stored_uid, lap_num),
                )
                updated += cursor.rowcount or 0
            return updated

    async def recent_laps(
        self,
        track_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._recent_laps_sync, track_id, limit)

    def _recent_laps_sync(
        self, track_id: int | None, limit: int
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM laps"
        params: list[Any] = []
        if track_id is not None:
            query += " WHERE track_id=?"
            params.append(track_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
            return [_restore_session_uid(dict(row)) for row in rows]

    async def track_review(self, track_id: int, limit: int = 30) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._track_review_sync, track_id, limit)

    def _track_review_sync(self, track_id: int, limit: int) -> dict[str, Any]:
        with self._connect() as db:
            laps = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    """
                    SELECT session_uid, lap_num, lap_time_ms, valid, compound,
                           tyre_age_start, tyre_age_end, fuel_start_kg, fuel_end_kg,
                           position, s1_ms, s2_ms, s3_ms, created_at
                    FROM laps WHERE track_id=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (track_id, limit),
                ).fetchall()
            ]
            corner_rows = db.execute(
                """
                SELECT corner_no, name,
                       AVG(loss_vs_pb_s) AS avg_loss_s,
                       MAX(loss_vs_pb_s) AS max_loss_s,
                       COUNT(*) AS samples,
                       SUM(wheel_lock) AS locks,
                       SUM(wheelspin) AS wheelspins
                FROM corner_metrics
                WHERE track_id=? AND loss_vs_pb_s IS NOT NULL
                GROUP BY corner_no, name
                ORDER BY avg_loss_s DESC
                LIMIT 12
                """,
                (track_id,),
            ).fetchall()
            return {
                "track_id": track_id,
                "laps": laps,
                "corner_opportunities": [dict(row) for row in corner_rows],
            }

    async def session_debrief(self, session_uid: int) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._session_debrief_sync, session_uid)

    def _session_debrief_sync(self, session_uid: int) -> dict[str, Any]:
        """Deterministic end-of-session summary: pace, consistency, tyre use,
        result and the biggest recurring corner losses. The language model
        narrates this; it does not compute it.
        """
        stored_uid = _session_uid_to_sqlite(session_uid)
        with self._connect() as db:
            session = db.execute(
                "SELECT * FROM sessions WHERE session_uid=?", (stored_uid,)
            ).fetchone()
            valid = db.execute(
                """
                SELECT lap_num, lap_time_ms, compound, tyre_age_end,
                       s1_ms, s2_ms, s3_ms
                FROM laps
                WHERE session_uid=? AND valid=1 AND lap_time_ms>0
                ORDER BY lap_num
                """,
                (stored_uid,),
            ).fetchall()
            lap_times = [int(row["lap_time_ms"]) for row in valid]
            fastest = min(lap_times) if lap_times else None
            mean_ms = int(sum(lap_times) / len(lap_times)) if lap_times else None
            spread_ms = (max(lap_times) - min(lap_times)) if lap_times else None
            if len(lap_times) > 1:
                mean = sum(lap_times) / len(lap_times)
                variance = sum((value - mean) ** 2 for value in lap_times) / len(lap_times)
                consistency_ms = int(variance**0.5)
            else:
                consistency_ms = None
            compounds = sorted({str(row["compound"]) for row in valid if row["compound"]})
            corners = db.execute(
                """
                SELECT corner_no, name,
                       AVG(loss_vs_pb_s) AS avg_loss_s,
                       COUNT(*) AS samples
                FROM corner_metrics
                WHERE session_uid=? AND loss_vs_pb_s IS NOT NULL AND loss_vs_pb_s>0
                GROUP BY corner_no, name
                ORDER BY avg_loss_s DESC
                LIMIT 3
                """,
                (stored_uid,),
            ).fetchall()
            return {
                "session_uid": session_uid,
                "track_name": session["track_name"] if session else None,
                "session_type": session["session_type"] if session else None,
                "result_position": session["result_position"] if session else None,
                "valid_lap_count": len(lap_times),
                "fastest_lap_ms": fastest,
                "average_lap_ms": mean_ms,
                "lap_time_spread_ms": spread_ms,
                "consistency_stdev_ms": consistency_ms,
                "compounds_used": compounds,
                "top_time_losses": [dict(row) for row in corners],
            }

    async def sector_bests(self, track_id: int) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._sector_bests_sync, track_id)

    def _sector_bests_sync(self, track_id: int) -> dict[str, Any]:
        """Best-ever sector times and the theoretical-best lap they compose.

        Each sector best is taken only from laps where that sector was actually
        recorded (non-zero), so the historical zero-sector rows from before the
        3.4 sector fix do not poison the minimum. The theoretical best is the
        sum of the three independent minima and is only reported when all three
        are present.
        """
        with self._connect() as db:
            sectors: dict[str, dict[str, Any] | None] = {}
            for key in ("s1_ms", "s2_ms", "s3_ms"):
                row = db.execute(
                    f"""
                    SELECT {key} AS ms, session_uid, lap_num, compound, created_at
                    FROM laps
                    WHERE track_id=? AND valid=1 AND {key}>0
                    ORDER BY {key} ASC LIMIT 1
                    """,
                    (track_id,),
                ).fetchone()
                sectors[key] = _restore_session_uid(dict(row)) if row else None
            best_lap = db.execute(
                """
                SELECT lap_time_ms, session_uid, lap_num, compound
                FROM laps WHERE track_id=? AND valid=1 AND lap_time_ms>0
                ORDER BY lap_time_ms ASC LIMIT 1
                """,
                (track_id,),
            ).fetchone()
            complete = all(sectors[key] for key in ("s1_ms", "s2_ms", "s3_ms"))
            theoretical_ms = (
                sum(int(sectors[key]["ms"]) for key in ("s1_ms", "s2_ms", "s3_ms"))
                if complete
                else None
            )
            best_lap_ms = int(best_lap["lap_time_ms"]) if best_lap else None
            return {
                "track_id": track_id,
                "sector_bests": sectors,
                "personal_best_lap_ms": best_lap_ms,
                "theoretical_best_ms": theoretical_ms,
                # How much is left on the table between the best real lap and the
                # sum of the best individual sectors.
                "time_left_on_table_ms": (
                    best_lap_ms - theoretical_ms
                    if best_lap_ms and theoretical_ms
                    else None
                ),
            }

    async def progress_trend(
        self,
        track_id: int,
        limit: int = 20,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._progress_trend_sync, track_id, limit)

    def _progress_trend_sync(self, track_id: int, limit: int) -> dict[str, Any]:
        """Per-session best valid lap over time, for a track, oldest to newest.

        This is the raw material for "am I faster at Spa than I was a month
        ago". One row per session, so a weekend's improvement curve is visible
        without returning every individual lap.
        """
        with self._connect() as db:
            # Take the most recent `limit` sessions, then present them oldest to
            # newest. Ordering ASC before LIMIT would keep the *oldest* sessions
            # and hide recent progress once history grows past the limit.
            rows = db.execute(
                """
                SELECT * FROM (
                    SELECT s.session_uid AS session_uid,
                           s.session_type AS session_type,
                           s.mode_profile AS mode_profile,
                           s.started_at AS started_at,
                           MIN(l.lap_time_ms) AS best_lap_ms,
                           COUNT(l.id) AS valid_laps
                    FROM sessions s
                    JOIN laps l ON l.session_uid = s.session_uid
                    WHERE s.track_id=? AND l.valid=1 AND l.lap_time_ms>0
                    GROUP BY s.session_uid
                    ORDER BY s.started_at DESC
                    LIMIT ?
                )
                ORDER BY started_at ASC
                """,
                (track_id, limit),
            ).fetchall()
            sessions = [_restore_session_uid(dict(row)) for row in rows]
            first = sessions[0]["best_lap_ms"] if sessions else None
            latest = sessions[-1]["best_lap_ms"] if sessions else None
            return {
                "track_id": track_id,
                "sessions": sessions,
                "session_count": len(sessions),
                "first_best_ms": first,
                "latest_best_ms": latest,
                # Negative = improvement (latest lap is quicker than the first).
                "improvement_ms": (latest - first) if first and latest else None,
            }

    async def setup_correlation(
        self,
        track_id: int,
        profile: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._setup_correlation_sync, track_id, profile
            )

    def _setup_correlation_sync(
        self,
        track_id: int,
        profile: str | None,
    ) -> dict[str, Any]:
        """Best and worst stored setup runs so setup choices can be linked to
        measured performance. The deterministic layer returns the paired runs;
        the language model narrates which setting moved which outcome, rather
        than inventing a regression the sample size cannot support.
        """
        query = "SELECT * FROM setup_runs WHERE track_id=?"
        params: list[Any] = [track_id]
        if profile:
            query += " AND profile=?"
            params.append(profile)
        with self._connect() as db:
            rows = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    query + " ORDER BY score ASC", params
                ).fetchall()
            ]
        for row in rows:
            row["setup"] = json.loads(row.pop("setup_json") or "{}")
            row["performance"] = json.loads(row.pop("performance_json") or "{}")
        return {
            "track_id": track_id,
            "profile": profile,
            "run_count": len(rows),
            "best_run": rows[0] if rows else None,
            "worst_run": rows[-1] if len(rows) > 1 else None,
            "runs": rows[:10],
        }

    async def save_setup_run(
        self,
        session_uid: int,
        track_id: int,
        track_name: str,
        profile: str,
        setup: dict[str, Any],
        performance: dict[str, Any],
        score: float,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_setup_run_sync,
                session_uid,
                track_id,
                track_name,
                profile,
                setup,
                performance,
                score,
            )

    def _save_setup_run_sync(
        self,
        session_uid: int,
        track_id: int,
        track_name: str,
        profile: str,
        setup: dict[str, Any],
        performance: dict[str, Any],
        score: float,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO setup_runs(
                    session_uid, track_id, track_name, profile,
                    setup_json, performance_json, score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(session_uid),
                    track_id,
                    track_name,
                    profile,
                    json.dumps(setup),
                    json.dumps(performance),
                    score,
                    time.time(),
                ),
            )

    async def best_setup_runs(
        self,
        track_id: int,
        profile: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._best_setup_runs_sync, track_id, profile, limit
            )

    def _best_setup_runs_sync(
        self,
        track_id: int,
        profile: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM setup_runs
                WHERE track_id=? AND profile=?
                ORDER BY score ASC LIMIT ?
                """,
                (track_id, profile, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["setup"] = json.loads(item.pop("setup_json"))
                item["performance"] = json.loads(item.pop("performance_json"))
                result.append(item)
            return result

    async def save_setup_recommendation(
        self,
        track_id: int,
        track_name: str,
        profile: str,
        setup: dict[str, Any],
        rationale: list[str],
        confidence: str,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_setup_recommendation_sync,
                track_id,
                track_name,
                profile,
                setup,
                rationale,
                confidence,
            )

    def _save_setup_recommendation_sync(
        self,
        track_id: int,
        track_name: str,
        profile: str,
        setup: dict[str, Any],
        rationale: list[str],
        confidence: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO setup_recommendations(
                    track_id, track_name, profile, setup_json,
                    rationale_json, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track_id,
                    track_name,
                    profile,
                    json.dumps(setup),
                    json.dumps(rationale),
                    confidence,
                    time.time(),
                ),
            )

    async def add_feedback(
        self,
        session_uid: int,
        track_id: int,
        category: str,
        text: str,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._add_feedback_sync,
                session_uid,
                track_id,
                category,
                text,
            )

    def _add_feedback_sync(
        self,
        session_uid: int,
        track_id: int,
        category: str,
        text: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO feedback(session_uid, track_id, category, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_session_uid_to_sqlite(session_uid), track_id, category, text, time.time()),
            )

    async def save_preference(self, key: str, value: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_preference_sync, str(key), value)

    def _save_preference_sync(self, key: str, value: Any) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO user_preferences(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(value), time.time()),
            )

    async def load_preference(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return await asyncio.to_thread(self._load_preference_sync, str(key), default)

    def _load_preference_sync(self, key: str, default: Any = None) -> Any:
        with self._connect() as db:
            row = db.execute(
                "SELECT value_json FROM user_preferences WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except (json.JSONDecodeError, TypeError):
            return default

    async def tyre_history_model(
        self,
        track_id: int,
        limit: int = 300,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return condition-aware personal tyre evidence from prior valid laps.

        The regression is intentionally small and transparent: ridge regression
        uses tyre age, fuel, temperatures, session profile and setup values. It
        complements deterministic safety limits rather than replacing them.
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._tyre_history_model_sync,
                int(track_id),
                max(20, min(1000, int(limit))),
                dict(context or {}),
            )

    @staticmethod
    def _ridge_fit_predict(
        rows: list[list[float]], target: list[float], query: list[float]
    ) -> tuple[float | None, list[float] | None, float | None]:
        if len(rows) < 6:
            return None, None, None
        try:
            import numpy as np

            x = np.asarray(rows, dtype=float)
            y = np.asarray(target, dtype=float)
            q = np.asarray(query, dtype=float)
            means = x.mean(axis=0)
            scales = x.std(axis=0)
            scales[scales < 1e-6] = 1.0
            z = (x - means) / scales
            zq = (q - means) / scales
            design = np.column_stack([np.ones(len(z)), z])
            ridge = np.eye(design.shape[1]) * 0.35
            ridge[0, 0] = 0.0
            weights = np.linalg.solve(design.T @ design + ridge, design.T @ y)
            # One Huber-style robust refit limits outlier laps and traffic noise.
            residual = y - design @ weights
            scale = max(1e-6, float(np.median(np.abs(residual))) * 1.4826)
            robust = np.minimum(1.0, (1.5 * scale) / np.maximum(np.abs(residual), 1e-9))
            wd = design * robust[:, None]
            weights = np.linalg.solve(design.T @ wd + ridge, design.T @ (robust * y))
            prediction = float(np.r_[1.0, zq] @ weights)
            raw_coefficients = (weights[1:] / scales).tolist()
            fitted = design @ weights
            rmse = float(np.sqrt(np.mean((y - fitted) ** 2)))
            return prediction, raw_coefficients, rmse
        except Exception:
            return None, None, None

    def _tyre_history_model_sync(
        self, track_id: int, limit: int, context: dict[str, Any]
    ) -> dict[str, Any]:
        from statistics import median

        try:
            with self._connect() as db:
                rows = db.execute(
                    """
                    SELECT l.compound, l.tyre_age_start, l.tyre_age_end,
                           l.wear_start_json, l.wear_end_json, l.lap_time_ms,
                           l.fuel_start_kg, l.fuel_end_kg, l.track_temp_c, l.air_temp_c,
                           COALESCE(NULLIF(l.mode_profile, ''), s.mode_profile, '') AS mode_profile,
                           l.setup_json
                    FROM laps AS l
                    LEFT JOIN sessions AS s ON s.session_uid=l.session_uid
                    WHERE l.track_id=? AND l.valid=1 AND l.lap_time_ms>0 AND l.pit_status=0
                    ORDER BY l.created_at DESC
                    LIMIT ?
                    """,
                    (track_id, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            # A database whose schema has not been created yet (the endpoint
            # was called before the first connect) holds no tyre evidence.
            # "No evidence" is the honest deterministic answer, not a 500.
            rows = []

        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in rows:
            item = dict(raw)
            compound = str(item.get("compound") or "UNKNOWN").upper()
            if compound not in {"SOFT", "MEDIUM", "HARD", "INTER", "WET"}:
                continue
            try:
                item["wear_start"] = json.loads(item.get("wear_start_json") or "[]")
                item["wear_end"] = json.loads(item.get("wear_end_json") or "[]")
                item["setup"] = json.loads(item.get("setup_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["wear_start"], item["wear_end"], item["setup"] = [], [], {}
            grouped.setdefault(compound, []).append(item)

        tyre = context.get("tyre", {})
        setup_context = context.get("car_setup", {}) or {}
        current_mode = str(context.get("mode_profile", "race"))
        current_query_base = [
            float(tyre.get("age_laps", 1) or 1),
            float(context.get("fuel_kg", 0.0) or 0.0),
            float(context.get("track_temp_c", 0.0) or 0.0),
            float(context.get("air_temp_c", 0.0) or 0.0),
            1.0 if current_mode == "race" else 0.0,
            1.0 if current_mode == "qualifying" else 0.0,
            1.0 if current_mode == "practice" else 0.0,
            float(setup_context.get("front_wing", 0.0) or 0.0),
            float(setup_context.get("rear_wing", 0.0) or 0.0),
            float(setup_context.get("on_throttle", 0.0) or 0.0),
        ]
        result: dict[str, Any] = {
            "track_id": track_id, "compounds": {},
            "model": "personal_condition_ridge_v1",
        }
        for compound, laps in grouped.items():
            wear_rates: list[float] = []
            max_wear_rates: list[float] = []
            wheel_wear_rates: list[list[float]] = [[], [], [], []]
            features: list[list[float]] = []
            wear_targets: list[float] = []
            pace_targets: list[float] = []
            for lap in laps:
                age_delta = max(1, int(lap.get("tyre_age_end") or 0) - int(lap.get("tyre_age_start") or 0))
                start_wear, end_wear = lap.get("wear_start") or [], lap.get("wear_end") or []
                deltas: list[float] = []
                if len(start_wear) == 4 and len(end_wear) == 4:
                    deltas = [max(0.0, float(b) - float(a)) / age_delta for a, b in zip(start_wear, end_wear)]
                    wear_rates.append(sum(deltas) / 4.0)
                    max_wear_rates.append(max(deltas))
                    for index, delta in enumerate(deltas):
                        wheel_wear_rates[index].append(delta)
                mode = str(lap.get("mode_profile") or "")
                setup = lap.get("setup") or {}
                fuel = (float(lap.get("fuel_start_kg") or 0.0) + float(lap.get("fuel_end_kg") or 0.0)) / 2.0
                feature = [
                    (float(lap.get("tyre_age_start") or 0) + float(lap.get("tyre_age_end") or 0)) / 2.0,
                    fuel, float(lap.get("track_temp_c") or 0.0), float(lap.get("air_temp_c") or 0.0),
                    1.0 if mode == "race" else 0.0, 1.0 if mode == "qualifying" else 0.0,
                    1.0 if mode == "practice" else 0.0, float(setup.get("front_wing", 0.0) or 0.0),
                    float(setup.get("rear_wing", 0.0) or 0.0), float(setup.get("on_throttle", 0.0) or 0.0),
                ]
                if deltas:
                    features.append(feature)
                    wear_targets.append(max(deltas))
                    pace_targets.append(float(lap.get("lap_time_ms") or 0) / 1000.0)

            query = list(current_query_base)
            # Missing pre-migration context values use the compound sample median.
            for index in (1, 2, 3, 7, 8, 9):
                if query[index] == 0.0 and features:
                    query[index] = float(median([row[index] for row in features]))
            wear_prediction, _wear_coeffs, wear_rmse = self._ridge_fit_predict(features, wear_targets, query)
            _pace_prediction, pace_coeffs, pace_rmse = self._ridge_fit_predict(features, pace_targets, query)
            deg_slope = pace_coeffs[0] if pace_coeffs else None
            result["compounds"][compound] = {
                "sample_size": len(laps),
                "wear_sample_size": len(wear_rates),
                "wear_per_lap_pct": round(float(median(wear_rates)), 3) if wear_rates else None,
                "max_wear_per_lap_pct": round(float(median(max_wear_rates)), 3) if max_wear_rates else None,
                "wheel_wear_per_lap_pct": [round(float(median(values)), 3) if values else None for values in wheel_wear_rates],
                "condition_adjusted_wear_per_lap_pct": round(max(0.0, float(wear_prediction)), 3) if wear_prediction is not None else None,
                "condition_adjusted_deg_s_per_lap": round(max(-0.1, min(1.5, float(deg_slope))), 4) if deg_slope is not None else None,
                "slope_s_per_lap": round(max(-0.1, min(1.5, float(deg_slope))), 4) if deg_slope is not None else None,
                "regression_rmse_wear": round(float(wear_rmse), 3) if wear_rmse is not None else None,
                "regression_rmse_pace_s": round(float(pace_rmse), 3) if pace_rmse is not None else None,
                "source": "condition_adjusted_personal_regression" if wear_prediction is not None else "personal_track_history",
            }
        return result

    async def compound_run_summary(
        self, track_id: int, compound: str, mode_profile: str | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._compound_run_summary_sync, int(track_id), compound.upper(), mode_profile
            )

    def _compound_run_summary_sync(
        self, track_id: int, compound: str, mode_profile: str | None
    ) -> dict[str, Any]:
        from statistics import median
        query = """
            SELECT l.lap_time_ms, l.wear_start_json, l.wear_end_json,
                   COALESCE(NULLIF(l.mode_profile, ''), s.mode_profile, '') AS mode_profile
            FROM laps AS l
            LEFT JOIN sessions AS s ON s.session_uid=l.session_uid
            WHERE l.track_id=? AND l.compound=? AND l.valid=1 AND l.lap_time_ms>0
        """
        params: list[Any] = [track_id, compound]
        if mode_profile:
            query += " AND COALESCE(NULLIF(l.mode_profile, ''), s.mode_profile, '')=?"
            params.append(mode_profile)
        query += " ORDER BY created_at DESC LIMIT 100"
        with self._connect() as db:
            rows = db.execute(query, tuple(params)).fetchall()
        times: list[int] = []
        wears: list[float] = []
        for row in rows:
            times.append(int(row["lap_time_ms"]))
            try:
                start, end = json.loads(row["wear_start_json"] or "[]"), json.loads(row["wear_end_json"] or "[]")
                if len(start) == 4 and len(end) == 4:
                    wears.append(max(max(0.0, float(b) - float(a)) for a, b in zip(start, end)))
            except (json.JSONDecodeError, TypeError):
                pass
        if not times:
            return {"available": False, "laps": 0}
        seconds = int(median(times))
        minutes, rem = divmod(seconds, 60_000)
        sec, millis = divmod(rem, 1000)
        return {
            "available": True, "laps": len(times), "compound": compound,
            "median_lap": f"{minutes}:{sec:02d}.{millis:03d}",
            "max_wear_per_lap_pct": round(float(median(wears)), 3) if wears else 0.0,
            "confidence": "high" if len(times) >= 12 else "medium" if len(times) >= 5 else "low",
        }

    async def save_line_metrics(
        self,
        session_uid: int,
        track_id: int,
        lap_num: int,
        metrics: dict[str, Any],
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_line_metrics_sync,
                session_uid,
                track_id,
                lap_num,
                metrics,
            )

    def _save_line_metrics_sync(
        self,
        session_uid: int,
        track_id: int,
        lap_num: int,
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO line_metrics(
                    session_uid, track_id, lap_num, metrics_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_uid, lap_num) DO UPDATE SET
                    metrics_json=excluded.metrics_json,
                    created_at=excluded.created_at
                """,
                (
                    _session_uid_to_sqlite(session_uid),
                    int(track_id),
                    int(lap_num),
                    json.dumps(metrics, separators=(",", ":")),
                    time.time(),
                ),
            )

    async def save_radio_message(
        self,
        state: dict[str, Any],
        role: str,
        text: str,
        source: str = "radio",
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_radio_message_sync,
                int(state.get("session_uid", 0)),
                int(state.get("track_id", -1)),
                int(state.get("current_lap", 0)),
                role,
                text,
                source,
            )

    def _save_radio_message_sync(
        self,
        session_uid: int,
        track_id: int,
        lap_num: int,
        role: str,
        text: str,
        source: str,
    ) -> None:
        if not text.strip():
            return
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO radio_messages(
                    session_uid, track_id, lap_num, role, text, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(session_uid),
                    track_id,
                    lap_num,
                    role,
                    text,
                    source,
                    time.time(),
                ),
            )

    async def save_strategy_snapshot(
        self,
        state: dict[str, Any],
        strategy: dict[str, Any],
    ) -> None:
        if not state.get("session_uid"):
            return
        async with self._lock:
            await asyncio.to_thread(
                self._save_strategy_snapshot_sync,
                state,
                strategy,
            )

    @staticmethod
    def _storable_plans(strategy: dict[str, Any]) -> list[dict[str, Any]]:
        """Trim the ranked plan list before persisting it.

        A full ranked list with every plan's per-lap ``stint_models`` is roughly
        20 KB per snapshot. Only the top alternatives are ever re-read (to explain
        why the chosen call beat the next one), and the per-lap simulation can be
        regenerated, so it is not stored.
        """
        plans = strategy.get("plans", []) or []
        trimmed: list[dict[str, Any]] = []
        for plan in plans[:3]:
            if not isinstance(plan, dict):
                continue
            trimmed.append({key: value for key, value in plan.items() if key != "stint_models"})
        return trimmed

    def _save_strategy_snapshot_sync(
        self,
        state: dict[str, Any],
        strategy: dict[str, Any],
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO strategy_snapshots(
                    session_uid, track_id, lap_num, race_control_phase,
                    recommended_json, plans_json, model_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    int(state.get("current_lap", 0)),
                    str(state.get("race_control_phase", "green")),
                    json.dumps(strategy.get("recommended", {})),
                    json.dumps(self._storable_plans(strategy)),
                    json.dumps(
                        {
                            "confidence": strategy.get("confidence"),
                            "model_summary": strategy.get("model_summary", {}),
                            "compound_rule": strategy.get("compound_rule", {}),
                            "neutralisation": strategy.get("neutralisation", {}),
                        }
                    ),
                    time.time(),
                ),
            )

    async def save_proactive_call(
        self,
        state: dict[str, Any],
        event: dict[str, Any],
        text: str,
        delivered: bool,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_proactive_call_sync,
                state,
                event,
                text,
                delivered,
            )

    def _save_proactive_call_sync(
        self,
        state: dict[str, Any],
        event: dict[str, Any],
        text: str,
        delivered: bool,
    ) -> None:
        blocked = event.get("blocked_reasons", []) or []
        refusal_reason = ", ".join(dict.fromkeys(str(item) for item in blocked))
        outcome = str(event.get("delivery_outcome") or ("delivered" if delivered else text))
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO proactive_calls(
                    session_uid, track_id, lap_num, event_type,
                    payload_json, text, delivered, outcome, refusal_reason,
                    queued_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    int(state.get("current_lap", 0)),
                    str(event.get("type", "unknown")),
                    json.dumps(event.get("payload", {})),
                    text,
                    1 if delivered else 0,
                    outcome,
                    refusal_reason,
                    float(event.get("queued_at", 0.0) or 0.0),
                    time.time(),
                ),
            )

    async def save_briefing(
        self,
        state: dict[str, Any],
        kind: str,
        payload: dict[str, Any],
        text: str,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_briefing_sync, state, kind, payload, text
            )

    def _save_briefing_sync(
        self,
        state: dict[str, Any],
        kind: str,
        payload: dict[str, Any],
        text: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO briefings(
                    session_uid, track_id, kind, payload_json, text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    str(kind),
                    json.dumps(payload),
                    str(text),
                    time.time(),
                ),
            )

    async def save_queued_session_event(self, event: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_queued_session_event_sync, event)

    def _save_queued_session_event_sync(self, event: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO session_events(
                    session_uid, track_id, lap_num, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(event.get("session_uid", 0)),
                    int(event.get("track_id", -1)),
                    int(event.get("current_lap", 0)),
                    str(event.get("event_type", "unknown")),
                    json.dumps(event.get("payload", {})),
                    float(event.get("created_at", time.time())),
                ),
            )

    async def save_session_event(
        self,
        state: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_session_event_sync,
                state,
                event_type,
                payload,
            )

    def _save_session_event_sync(
        self,
        state: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO session_events(
                    session_uid, track_id, lap_num, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    int(state.get("current_lap", 0)),
                    event_type,
                    json.dumps(payload),
                    time.time(),
                ),
            )

    async def history_query(
        self,
        track_id: int | None = None,
        session_uid: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._history_query_sync,
                track_id,
                session_uid,
                max(1, min(250, int(limit))),
            )

    def _history_query_sync(
        self,
        track_id: int | None,
        session_uid: int | None,
        limit: int,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if track_id is not None:
            clauses.append("track_id=?")
            params.append(int(track_id))
        if session_uid is not None:
            clauses.append("session_uid=?")
            params.append(_session_uid_to_sqlite(session_uid))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            sessions = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM sessions{where} ORDER BY started_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]
            laps = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"""
                    SELECT session_uid, track_id, track_name, session_type,
                           lap_num, lap_time_ms, valid, compound,
                           tyre_age_start, tyre_age_end, position,
                           s1_ms, s2_ms, s3_ms, created_at
                    FROM laps{where}
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
            ]
            strategies = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM strategy_snapshots{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]
            radio = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM radio_messages{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]
            proactive = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM proactive_calls{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]
            briefings = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM briefings{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]
            line_rows = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM line_metrics{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]
            session_events = [
                _restore_session_uid(dict(row))
                for row in db.execute(
                    f"SELECT * FROM session_events{where} "
                    "ORDER BY created_at DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
            ]

        for item in strategies:
            item["recommended"] = json.loads(item.pop("recommended_json") or "{}")
            item["plans"] = json.loads(item.pop("plans_json") or "[]")
            item["model"] = json.loads(item.pop("model_json") or "{}")
        for item in proactive:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        for item in briefings:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        for item in line_rows:
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        for item in session_events:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return {
            "sessions": sessions,
            "laps": laps,
            "strategies": strategies,
            "radio": radio,
            "proactive_calls": proactive,
            "briefings": briefings,
            "line_metrics": line_rows,
            "session_events": session_events,
        }

    async def setup_learning_summary(
        self,
        track_id: int,
        profile: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        runs = await self.best_setup_runs(track_id, profile, limit)
        return {
            "track_id": int(track_id),
            "profile": profile,
            "samples": len(runs),
            "runs": runs,
        }
