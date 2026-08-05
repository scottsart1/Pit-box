from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capture import CaptureScanReport
from .trace_store import TraceManifest


def opaque_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{prefix}_{digest}"


def session_id(game_session_uid: int | str, restart_epoch: int = 0) -> str:
    return opaque_id("ses", str(game_session_uid), int(restart_epoch))


def session_car_id(
    session_key: str,
    car_index: int,
    identity_revision: int = 0,
) -> str:
    return opaque_id("car", session_key, int(car_index), int(identity_revision))


def lap_id(
    car_key: str,
    lap_number: int,
    timeline_epoch: int = 0,
) -> str:
    return opaque_id("lap", car_key, int(lap_number), int(timeline_epoch))


def _game_uid_from_sqlite(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


def _iso_from_unix(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), UTC).isoformat()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class DeletePreviewError(PermissionError):
    """A destructive request did not match a current, unexpired preview."""


class ActiveSessionDeleteError(RuntimeError):
    """An actively recording session cannot be deleted safely."""


def _encode_cursor(started_at: str, item_id: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([started_at, item_id], separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        return str(value[0]), str(value[1])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session page cursor") from exc


class SessionCatalog:
    """Additive 4.2 session/lap catalog over the existing SQLite file.

    Legacy persistence remains authoritative during the compatibility period.
    This catalog gives the Library and comparison layers stable opaque IDs,
    restart/timeline epochs, participant identity, and resumable backfill.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._delete_previews: dict[str, tuple[str, str, float]] = {}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    async def sync_legacy(self, *, batch_size: int = 250) -> dict[str, int]:
        if not 1 <= int(batch_size) <= 10_000:
            raise ValueError("batch_size must be between 1 and 10000")
        async with self._lock:
            return await asyncio.to_thread(self._sync_legacy_sync, int(batch_size))

    def _ensure_legacy_session(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row | dict[str, Any],
    ) -> tuple[str, str]:
        raw_uid = int(row["session_uid"])
        game_uid = _game_uid_from_sqlite(raw_uid)
        key = session_id(game_uid, 0)
        now = _utc_now()
        available_keys = set(row.keys())
        started = (
            _iso_from_unix(row["started_at"])
            if "started_at" in available_keys
            else now
        )
        ended = (
            _iso_from_unix(row["ended_at"])
            if "ended_at" in available_keys
            else None
        )
        track_id = int(row["track_id"] if row["track_id"] is not None else -1)
        session_type = str(row["session_type"] or "Unknown")
        mode_profile = (
            str(row["mode_profile"] or "idle")
            if "mode_profile" in available_keys
            else "idle"
        )
        db.execute(
            """
            INSERT INTO recorded_sessions(
                id, legacy_session_uid, game_session_uid, restart_epoch,
                track_id, track_layout_signature, session_type, mode_profile,
                started_at, ended_at, status, packet_format, capture_mode,
                created_at, updated_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, NULL, 'minimal', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                legacy_session_uid=COALESCE(
                    recorded_sessions.legacy_session_uid,
                    excluded.legacy_session_uid
                ),
                track_id=excluded.track_id,
                session_type=excluded.session_type,
                mode_profile=excluded.mode_profile,
                ended_at=COALESCE(recorded_sessions.ended_at, excluded.ended_at),
                status=CASE WHEN excluded.ended_at IS NOT NULL THEN 'complete'
                            ELSE recorded_sessions.status END,
                updated_at=excluded.updated_at
            """,
            (
                key,
                raw_uid,
                str(game_uid),
                track_id,
                f"legacy:{track_id}",
                session_type,
                mode_profile,
                started or now,
                ended,
                "complete" if ended else "incomplete",
                now,
                now,
            ),
        )
        car_key = session_car_id(key, 0, 0)
        db.execute(
            """
            INSERT INTO session_cars(
                id, session_id, car_index, identity_revision, display_name,
                anonymized_name, is_ai, is_player, change_reason,
                identity_confidence
            ) VALUES (?, ?, 0, 0, 'Player', 'Driver 01', 0, 1,
                      'legacy_backfill', 0.5)
            ON CONFLICT(id) DO NOTHING
            """,
            (car_key, key),
        )
        return key, car_key

    def _sync_legacy_sync(self, batch_size: int) -> dict[str, int]:
        inserted_sessions = 0
        inserted_laps = 0
        with self._connect() as db:
            sessions = db.execute("SELECT * FROM sessions ORDER BY started_at, session_uid").fetchall()
            for row in sessions:
                before = db.total_changes
                self._ensure_legacy_session(db, row)
                inserted_sessions += int(db.total_changes > before)

            laps = db.execute(
                """
                SELECT l.*, s.started_at, s.ended_at
                FROM laps l
                LEFT JOIN sessions s ON s.session_uid=l.session_uid
                LEFT JOIN recorded_laps rl ON rl.legacy_lap_id=l.id
                WHERE rl.id IS NULL
                ORDER BY l.id
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
            for row in laps:
                inserted_laps += int(self._insert_legacy_lap(db, row))
            remaining = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM laps l
                    LEFT JOIN recorded_laps rl ON rl.legacy_lap_id=l.id
                    WHERE rl.id IS NULL
                    """
                ).fetchone()[0]
            )
        return {
            "sessions_touched": inserted_sessions,
            "laps_inserted": inserted_laps,
            "laps_remaining": remaining,
        }

    def _insert_legacy_lap(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        _, car_key = self._ensure_legacy_session(db, row)
        key = lap_id(car_key, int(row["lap_num"]), 0)
        db.execute(
            """
            INSERT OR IGNORE INTO recorded_laps(
                id, session_car_id, legacy_lap_id, lap_number,
                timeline_epoch, lap_time_ms, valid, tyre_compound,
                tyre_age_laps, fuel_start_kg, fuel_end_kg,
                weather_class, pit_context, flag_context,
                coverage_ratio, quality_score, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                car_key,
                int(row["id"]),
                int(row["lap_num"]),
                int(row["lap_time_ms"] or 0),
                int(row["valid"] or 0),
                str(row["compound"] or "UNKNOWN"),
                int(row["tyre_age_end"] or 0),
                row["fuel_start_kg"],
                row["fuel_end_kg"],
                str(row["weather"] or "Unknown"),
                int(row["pit_status"] or 0),
                1 if int(row["valid"] or 0) == 0 else 0,
                1.0 if str(row["trace_json"] or "[]") not in {"", "[]"} else 0.0,
                0.8 if int(row["valid"] or 0) else 0.4,
                _iso_from_unix(row["created_at"]) or _utc_now(),
            ),
        )
        return bool(db.execute("SELECT changes()").fetchone()[0])

    async def record_legacy_lap(self, legacy_lap_id: int) -> str | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._record_legacy_lap_sync, int(legacy_lap_id)
            )

    def _record_legacy_lap_sync(self, legacy_lap_id: int) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT l.*, s.started_at, s.ended_at
                FROM laps l
                LEFT JOIN sessions s ON s.session_uid=l.session_uid
                WHERE l.id=?
                """,
                (legacy_lap_id,),
            ).fetchone()
            if row is None:
                return None
            self._insert_legacy_lap(db, row)
            game_uid = _game_uid_from_sqlite(int(row["session_uid"]))
            car_key = session_car_id(session_id(game_uid), 0)
            return lap_id(car_key, int(row["lap_num"]), 0)

    async def record_player_lap(
        self,
        lap: dict[str, Any],
        *,
        legacy_lap_id: int | None = None,
    ) -> str:
        async with self._lock:
            return await asyncio.to_thread(
                self._record_player_lap_sync, lap, legacy_lap_id
            )

    def _record_player_lap_sync(
        self,
        lap: dict[str, Any],
        legacy_lap_id: int | None,
    ) -> str:
        session_key = self._upsert_live_session_sync(lap)
        car_index = int(lap.get("player_car_index", 0) or 0)
        identity_revision = int(lap.get("identity_revision", 0) or 0)
        car_key = session_car_id(session_key, car_index, identity_revision)
        timeline_epoch = int(lap.get("timeline_epoch", 0) or 0)
        key = lap_id(car_key, int(lap["lap_num"]), timeline_epoch)
        trace = lap.get("trace") or []
        coverage = 1.0 if trace else 0.0
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO recorded_laps(
                    id, session_car_id, legacy_lap_id, lap_number,
                    timeline_epoch, lap_time_ms, valid, invalid_reason_mask,
                    tyre_compound, tyre_age_laps, fuel_start_kg, fuel_end_kg,
                    weather_class, pit_context, flag_context, coverage_ratio,
                    quality_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    legacy_lap_id=COALESCE(excluded.legacy_lap_id,
                                           recorded_laps.legacy_lap_id),
                    lap_time_ms=excluded.lap_time_ms,
                    valid=excluded.valid,
                    invalid_reason_mask=excluded.invalid_reason_mask,
                    tyre_compound=excluded.tyre_compound,
                    tyre_age_laps=excluded.tyre_age_laps,
                    fuel_start_kg=excluded.fuel_start_kg,
                    fuel_end_kg=excluded.fuel_end_kg,
                    weather_class=excluded.weather_class,
                    pit_context=excluded.pit_context,
                    flag_context=excluded.flag_context,
                    coverage_ratio=MAX(recorded_laps.coverage_ratio,
                                       excluded.coverage_ratio),
                    quality_score=MAX(recorded_laps.quality_score,
                                      excluded.quality_score)
                """,
                (
                    key,
                    car_key,
                    legacy_lap_id,
                    int(lap["lap_num"]),
                    timeline_epoch,
                    int(lap.get("lap_time_ms", 0) or 0),
                    1 if lap.get("valid") else 0,
                    int(lap.get("invalid_reason_mask", 0) or 0),
                    str(lap.get("compound", "UNKNOWN")),
                    int(lap.get("tyre_age_end", 0) or 0),
                    lap.get("fuel_start_kg"),
                    lap.get("fuel_end_kg"),
                    str(lap.get("weather", "Unknown")),
                    int(lap.get("pit_status", 0) or 0),
                    int(lap.get("flag_context", 0) or 0),
                    coverage,
                    0.9 if lap.get("valid") and trace else 0.5 if trace else 0.2,
                    _iso_from_unix(float(lap.get("created_at", 0) or 0))
                    or _utc_now(),
                ),
            )
        return key

    async def upsert_live_session(self, state: dict[str, Any]) -> str | None:
        if not int(state.get("session_uid", 0) or 0):
            return None
        async with self._lock:
            return await asyncio.to_thread(self._upsert_live_session_sync, state)

    def _upsert_live_session_sync(self, state: dict[str, Any]) -> str:
        game_uid = int(state["session_uid"])
        restart_epoch = int(state.get("restart_epoch", 0) or 0)
        key = session_id(game_uid, restart_epoch)
        car_index = int(state.get("player_car_index", 0) or 0)
        car_key = session_car_id(key, car_index, 0)
        now = _utc_now()
        classification = state.get("final_classification") or {}
        finished = int(classification.get("position", 0) or 0) > 0
        track_id = int(state.get("track_id", -1) or -1)
        length = int(state.get("track_length_m", 0) or 0)
        layout = f"f1:{int(state.get('packet_format', 0) or 0)}:{track_id}:{length}"
        drivers = state.get("drivers") or []
        participant = next(
            (
                item
                for item in drivers
                if int(item.get("car_idx", -1)) == car_index
            ),
            {},
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO recorded_sessions(
                    id, legacy_session_uid, game_session_uid, restart_epoch,
                    track_id, track_layout_signature, session_type, mode_profile,
                    started_at, ended_at, status, packet_format, capture_mode,
                    quality_score, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    track_id=excluded.track_id,
                    track_layout_signature=excluded.track_layout_signature,
                    session_type=excluded.session_type,
                    mode_profile=excluded.mode_profile,
                    ended_at=COALESCE(recorded_sessions.ended_at, excluded.ended_at),
                    status=CASE
                        WHEN recorded_sessions.status='complete' THEN 'complete'
                        ELSE excluded.status
                    END,
                    packet_format=excluded.packet_format,
                    capture_mode=excluded.capture_mode,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    str(game_uid),
                    restart_epoch,
                    track_id,
                    layout,
                    str(state.get("session_type", "Unknown")),
                    str(state.get("mode_profile", "idle")),
                    now,
                    now if finished else None,
                    "complete" if finished else "recording",
                    int(state.get("packet_format", 0) or 0),
                    str(state.get("capture_mode", "balanced")),
                    now,
                    now,
                ),
            )
            display_name = str(participant.get("name") or "Player")
            db.execute(
                """
                INSERT INTO session_cars(
                    id, session_id, car_index, identity_revision, driver_id,
                    display_name, anonymized_name, race_number, team_id,
                    is_ai, is_player, first_frame, last_frame, change_reason,
                    identity_confidence
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 1, ?, ?,
                          'live_observation', ?)
                ON CONFLICT(id) DO UPDATE SET
                    driver_id=COALESCE(excluded.driver_id, session_cars.driver_id),
                    display_name=excluded.display_name,
                    race_number=COALESCE(excluded.race_number, session_cars.race_number),
                    team_id=COALESCE(excluded.team_id, session_cars.team_id),
                    last_frame=MAX(session_cars.last_frame, excluded.last_frame),
                    identity_confidence=MAX(
                        session_cars.identity_confidence,
                        excluded.identity_confidence
                    )
                """,
                (
                    car_key,
                    key,
                    car_index,
                    participant.get("driver_id"),
                    display_name,
                    f"Driver {car_index + 1:02d}",
                    participant.get("race_number"),
                    participant.get("team_id"),
                    1 if participant.get("ai_controlled") else 0,
                    int(state.get("frame_identifier", 0) or 0),
                    int(state.get("frame_identifier", 0) or 0),
                    0.95 if participant else 0.6,
                ),
            )
        return key

    async def finalize_session(
        self,
        key: str,
        *,
        status: str = "incomplete",
    ) -> bool:
        """Close one recording without ever downgrading a completed result."""

        normalized = str(status).strip().casefold()
        if normalized not in {"complete", "incomplete"}:
            raise ValueError("session status must be 'complete' or 'incomplete'")
        async with self._lock:
            return await asyncio.to_thread(
                self._finalize_session_sync, str(key), normalized
            )

    def _finalize_session_sync(self, key: str, status: str) -> bool:
        now = _utc_now()
        with self._connect() as db:
            result = db.execute(
                """
                UPDATE recorded_sessions
                SET ended_at=COALESCE(ended_at, ?),
                    status=CASE
                        WHEN status='complete' THEN 'complete'
                        WHEN ?='complete' THEN 'complete'
                        ELSE 'incomplete'
                    END,
                    updated_at=?
                WHERE id=?
                """,
                (now, status, now, key),
            )
            return result.rowcount == 1

    async def finalize_recording_sessions(
        self,
        *,
        exclude_session_id: str | None = None,
    ) -> int:
        """Mark stale process-owned recordings incomplete during recovery/switch."""

        async with self._lock:
            return await asyncio.to_thread(
                self._finalize_recording_sessions_sync,
                None if exclude_session_id is None else str(exclude_session_id),
            )

    def _finalize_recording_sessions_sync(
        self,
        exclude_session_id: str | None,
    ) -> int:
        now = _utc_now()
        query = """
            UPDATE recorded_sessions
            SET ended_at=COALESCE(ended_at, ?),
                status='incomplete',
                updated_at=?
            WHERE status='recording'
        """
        parameters: list[Any] = [now, now]
        if exclude_session_id is not None:
            query += " AND id<>?"
            parameters.append(exclude_session_id)
        with self._connect() as db:
            result = db.execute(query, parameters)
            return max(0, int(result.rowcount))

    async def list_sessions(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        track_id: int | None = None,
        session_type: str | None = None,
        starred: bool | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 200:
            raise ValueError("limit must be between 1 and 200")
        async with self._lock:
            return await asyncio.to_thread(
                self._list_sessions_sync,
                int(limit),
                cursor,
                track_id,
                session_type,
                starred,
                search,
            )

    def _list_sessions_sync(
        self,
        limit: int,
        cursor: str | None,
        track_id: int | None,
        session_type: str | None,
        starred: bool | None,
        search: str | None,
    ) -> dict[str, Any]:
        conditions: list[str] = []
        parameters: list[Any] = []
        if track_id is not None:
            conditions.append("s.track_id=?")
            parameters.append(int(track_id))
        if session_type:
            conditions.append("LOWER(s.session_type)=LOWER(?)")
            parameters.append(session_type.strip())
        if starred is not None:
            conditions.append("s.starred=?")
            parameters.append(1 if starred else 0)
        if search:
            conditions.append(
                "(LOWER(COALESCE(s.display_name,'')) LIKE ? OR LOWER(s.tags_json) LIKE ?)"
            )
            needle = f"%{search.strip().lower()}%"
            parameters.extend((needle, needle))
        if cursor:
            started_at, item_id = _decode_cursor(cursor)
            conditions.append("(s.started_at < ? OR (s.started_at = ? AND s.id < ?))")
            parameters.extend((started_at, started_at, item_id))
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT s.*,
                       (SELECT COUNT(*) FROM session_cars c
                        WHERE c.session_id=s.id) AS drivers_observed,
                       (SELECT COUNT(*) FROM recorded_laps l
                        JOIN session_cars c ON c.id=l.session_car_id
                        WHERE c.session_id=s.id) AS lap_count,
                       COALESCE((SELECT SUM(tc.byte_count)
                                 FROM trace_manifests tm
                                 JOIN trace_chunks tc ON tc.manifest_id=tm.id
                                 WHERE tm.session_id=s.id), 0) AS trace_bytes,
                       COALESCE((SELECT SUM(rc.byte_count) FROM raw_captures rc
                                 WHERE rc.session_id=s.id), 0) AS capture_bytes
                FROM recorded_sessions s
                {where}
                ORDER BY s.started_at DESC, s.id DESC
                LIMIT ?
                """,
                (*parameters, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        items = []
        for row in page:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.pop("tags_json"))
            except (TypeError, json.JSONDecodeError):
                item["tags"] = []
                item.pop("tags_json", None)
            item["size_bytes"] = int(item.pop("trace_bytes")) + int(
                item.pop("capture_bytes")
            )
            item["starred"] = bool(item["starred"])
            items.append(item)
        next_cursor = (
            _encode_cursor(str(page[-1]["started_at"]), str(page[-1]["id"]))
            if has_more and page
            else None
        )
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    async def get_session(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_session_sync, key)

    def _get_session_sync(self, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM recorded_sessions WHERE id=?", (key,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["starred"] = bool(result["starred"])
            try:
                result["tags"] = json.loads(result.pop("tags_json"))
            except (TypeError, json.JSONDecodeError):
                result["tags"] = []
                result.pop("tags_json", None)
            result["participants"] = [
                dict(item)
                for item in db.execute(
                    "SELECT * FROM session_cars WHERE session_id=? ORDER BY car_index, identity_revision",
                    (key,),
                ).fetchall()
            ]
            result["derived"] = {
                "comparisons": int(
                    db.execute(
                        """
                        SELECT COUNT(*) FROM comparisons cmp
                        JOIN recorded_laps l ON l.id=cmp.candidate_lap_id
                        JOIN session_cars c ON c.id=l.session_car_id
                        WHERE c.session_id=?
                        """,
                        (key,),
                    ).fetchone()[0]
                ),
                "jobs": [
                    dict(item)
                    for item in db.execute(
                        "SELECT * FROM analysis_jobs WHERE session_id=? ORDER BY created_at DESC",
                        (key,),
                    ).fetchall()
                ],
            }
            return result

    async def list_laps(
        self,
        session_key: str,
        *,
        car_key: str | None = None,
        valid: bool | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_laps_sync, session_key, car_key, valid
            )

    def _list_laps_sync(
        self,
        session_key: str,
        car_key: str | None,
        valid: bool | None,
    ) -> list[dict[str, Any]]:
        conditions = ["c.session_id=?"]
        parameters: list[Any] = [session_key]
        if car_key:
            conditions.append("l.session_car_id=?")
            parameters.append(car_key)
        if valid is not None:
            conditions.append("l.valid=?")
            parameters.append(1 if valid else 0)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT l.*, c.display_name, c.car_index, c.is_player,
                       tm.state AS trace_state, tm.field_mask, tm.checksum AS trace_checksum
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                LEFT JOIN trace_manifests tm ON tm.id=l.trace_manifest_id
                WHERE {' AND '.join(conditions)}
                ORDER BY l.lap_number, l.timeline_epoch, c.car_index
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    async def patch_session(
        self,
        key: str,
        *,
        display_name: str | None = None,
        tags: list[str] | None = None,
        starred: bool | None = None,
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._patch_session_sync, key, display_name, tags, starred
            )

    def _patch_session_sync(
        self,
        key: str,
        display_name: str | None,
        tags: list[str] | None,
        starred: bool | None,
    ) -> bool:
        assignments = ["updated_at=?"]
        parameters: list[Any] = [_utc_now()]
        if display_name is not None:
            assignments.append("display_name=?")
            parameters.append(display_name.strip() or None)
        if tags is not None:
            normalized = sorted({tag.strip() for tag in tags if tag.strip()})
            assignments.append("tags_json=?")
            parameters.append(json.dumps(normalized, separators=(",", ":")))
        if starred is not None:
            assignments.append("starred=?")
            parameters.append(1 if starred else 0)
        parameters.append(key)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE recorded_sessions SET {', '.join(assignments)} WHERE id=?",
                parameters,
            )
            return result.rowcount == 1

    async def register_trace_manifest(
        self,
        session_key: str,
        manifest: TraceManifest,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._register_trace_manifest_sync, session_key, manifest
            )

    def _register_trace_manifest_sync(
        self,
        session_key: str,
        manifest: TraceManifest,
    ) -> None:
        fields = sorted(
            {field for chunk in manifest.chunks for field in chunk.fields}
        )
        total_samples = sum(chunk.sample_count for chunk in manifest.chunks)
        weighted_coverage = sum(
            chunk.sample_count
            * (
                sum(chunk.coverage.values()) / len(chunk.coverage)
                if chunk.coverage
                else 0.0
            )
            for chunk in manifest.chunks
        )
        coverage = weighted_coverage / total_samples if total_samples else 0.0
        manifest_bytes = json.dumps(
            manifest.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        checksum = hashlib.sha256(manifest_bytes).hexdigest()
        created_at = datetime.fromtimestamp(
            manifest.created_wall_ns / 1_000_000_000, UTC
        ).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """
                    INSERT INTO trace_manifests(
                        id, session_id, session_car_id, lap_id,
                        encoding_version, axis_type, field_mask, sample_count,
                        coverage_ratio, checksum, state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        field_mask=excluded.field_mask,
                        sample_count=excluded.sample_count,
                        coverage_ratio=excluded.coverage_ratio,
                        checksum=excluded.checksum,
                        state='ready'
                    """,
                    (
                        manifest.id,
                        session_key,
                        manifest.session_car_id,
                        manifest.lap_id,
                        manifest.encoding_version,
                        manifest.chunks[0].axis_field if manifest.chunks else "distance",
                        json.dumps(fields, separators=(",", ":")).encode("utf-8"),
                        total_samples,
                        coverage,
                        checksum,
                        created_at,
                    ),
                )
                db.execute(
                    "DELETE FROM trace_chunks WHERE manifest_id=?", (manifest.id,)
                )
                for chunk in manifest.chunks:
                    db.execute(
                        """
                        INSERT INTO trace_chunks(
                            id, manifest_id, ordinal, relative_path,
                            start_axis, end_axis, sample_count, byte_count,
                            checksum, state
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')
                        """,
                        (
                            opaque_id("chk", manifest.id, chunk.ordinal),
                            manifest.id,
                            chunk.ordinal,
                            chunk.relative_path,
                            float(chunk.axis_min or 0.0),
                            float(chunk.axis_max or 0.0),
                            chunk.sample_count,
                            chunk.byte_count,
                            chunk.checksum_sha256,
                        ),
                    )
                db.execute(
                    "UPDATE recorded_laps SET trace_manifest_id=? WHERE id=?",
                    (manifest.id, manifest.lap_id),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

    async def register_raw_capture(
        self,
        session_key: str | None,
        relative_path: str,
        report: CaptureScanReport,
        *,
        privacy_mode: str = "private",
    ) -> str:
        async with self._lock:
            return await asyncio.to_thread(
                self._register_raw_capture_sync,
                session_key,
                relative_path,
                report,
                privacy_mode,
            )

    def _register_raw_capture_sync(
        self,
        session_key: str | None,
        relative_path: str,
        report: CaptureScanReport,
        privacy_mode: str,
    ) -> str:
        capture_id = opaque_id("cap", relative_path, report.file_size)
        footer = report.footer or {}
        checksum = str(footer.get("content_sha256") or "") or None
        started_at = _iso_from_unix(
            float(report.metadata.get("created_wall_ns", 0)) / 1_000_000_000
        ) or _utc_now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO raw_captures(
                    id, session_id, relative_path, format_version, started_at,
                    ended_at, byte_count, packet_count, checksum, clean_close,
                    recovered, privacy_mode, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    session_id=COALESCE(excluded.session_id, raw_captures.session_id),
                    byte_count=excluded.byte_count,
                    packet_count=excluded.packet_count,
                    checksum=excluded.checksum,
                    clean_close=excluded.clean_close,
                    recovered=excluded.recovered,
                    privacy_mode=excluded.privacy_mode,
                    metadata_json=excluded.metadata_json
                """,
                (
                    capture_id,
                    session_key,
                    relative_path,
                    int(report.metadata.get("format_version", 1)),
                    started_at,
                    _utc_now() if report.clean_close else None,
                    report.file_size,
                    report.packet_count,
                    checksum,
                    1 if report.clean_close else 0,
                    1 if report.recovered else 0,
                    privacy_mode,
                    json.dumps(report.metadata, sort_keys=True, separators=(",", ":")),
                ),
            )
        return capture_id

    async def get_quality(self, key: str) -> dict[str, Any] | None:
        """Return an honest persisted-data quality projection for one session."""

        async with self._lock:
            return await asyncio.to_thread(self._get_quality_sync, key)

    def _get_quality_sync(self, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            session = db.execute(
                "SELECT status, quality_score FROM recorded_sessions WHERE id=?",
                (key,),
            ).fetchone()
            if session is None:
                return None
            laps = db.execute(
                """
                SELECT COUNT(*) AS lap_count,
                       COALESCE(SUM(l.valid), 0) AS valid_laps,
                       COALESCE(AVG(l.coverage_ratio), 0) AS mean_coverage,
                       COALESCE(AVG(l.quality_score), 0) AS mean_quality,
                       COALESCE(SUM(CASE WHEN l.trace_manifest_id IS NOT NULL
                                         THEN 1 ELSE 0 END), 0) AS traced_laps
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                WHERE c.session_id=?
                """,
                (key,),
            ).fetchone()
            participant_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM session_cars WHERE session_id=?", (key,)
                ).fetchone()[0]
            )
            traces = {
                str(row["state"]): int(row["amount"])
                for row in db.execute(
                    """
                    SELECT state, COUNT(*) AS amount FROM trace_manifests
                    WHERE session_id=? GROUP BY state
                    """,
                    (key,),
                ).fetchall()
            }
            capture = db.execute(
                """
                SELECT COUNT(*) AS amount,
                       COALESCE(SUM(packet_count), 0) AS packets,
                       COALESCE(SUM(byte_count), 0) AS bytes,
                       COALESCE(SUM(CASE WHEN clean_close=0 THEN 1 ELSE 0 END), 0)
                           AS incomplete,
                       COALESCE(SUM(recovered), 0) AS recovered
                FROM raw_captures WHERE session_id=?
                """,
                (key,),
            ).fetchone()
        lap_count = int(laps["lap_count"])
        warnings: list[str] = []
        if lap_count == 0:
            warnings.append("No completed laps have been catalogued for this session.")
        if int(capture["incomplete"]):
            warnings.append("One or more raw captures did not close cleanly.")
        if traces.get("corrupt", 0) or traces.get("error", 0):
            warnings.append("One or more trace manifests require recovery or reprocessing.")
        if not int(capture["amount"]):
            warnings.append(
                "Detailed packet-health history is unavailable because no raw capture "
                "is catalogued."
            )
        return {
            "session_id": key,
            "status": str(session["status"]),
            "quality_score": (
                float(session["quality_score"])
                if session["quality_score"] is not None
                else (float(laps["mean_quality"]) if lap_count else None)
            ),
            "participants_observed": participant_count,
            "laps": {
                "total": lap_count,
                "valid": int(laps["valid_laps"]),
                "with_trace": int(laps["traced_laps"]),
                "mean_coverage": round(float(laps["mean_coverage"]), 6),
            },
            "trace_manifests": traces,
            "raw_captures": {
                "count": int(capture["amount"]),
                "packets": int(capture["packets"]),
                "bytes": int(capture["bytes"]),
                "incomplete": int(capture["incomplete"]),
                "recovered": int(capture["recovered"]),
            },
            "packet_health_available": bool(capture["amount"]),
            "warnings": warnings,
        }

    async def request_reprocess(
        self,
        key: str,
        *,
        algorithm_bundle: str = "analysis_4.2.0",
    ) -> dict[str, Any] | None:
        """Create or reuse the durable reprocessing job for current inputs."""

        bundle = algorithm_bundle.strip()
        if not bundle:
            raise ValueError("algorithm_bundle cannot be blank")
        async with self._lock:
            return await asyncio.to_thread(self._request_reprocess_sync, key, bundle)

    def _request_reprocess_sync(
        self,
        key: str,
        algorithm_bundle: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            session = db.execute(
                "SELECT id, updated_at FROM recorded_sessions WHERE id=?", (key,)
            ).fetchone()
            if session is None:
                return None
            inputs = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT l.id, l.timeline_epoch, l.valid, l.coverage_ratio,
                           l.trace_manifest_id, tm.checksum
                    FROM recorded_laps l
                    JOIN session_cars c ON c.id=l.session_car_id
                    LEFT JOIN trace_manifests tm ON tm.id=l.trace_manifest_id
                    WHERE c.session_id=? ORDER BY l.id
                    """,
                    (key,),
                ).fetchall()
            ]
            input_hash = hashlib.sha256(
                json.dumps(
                    {
                        "session_id": key,
                        "updated_at": session["updated_at"],
                        "algorithm_bundle": algorithm_bundle,
                        "laps": inputs,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            job_id = opaque_id("job", "session_reprocess", input_hash)
            now = _utc_now()
            result = db.execute(
                """
                INSERT OR IGNORE INTO analysis_jobs(
                    id, session_id, job_kind, input_hash, state, progress, created_at
                ) VALUES (?, ?, 'session_reprocess', ?, 'queued', 0, ?)
                """,
                (job_id, key, input_hash, now),
            )
            reused = result.rowcount == 0
            row = db.execute(
                "SELECT * FROM analysis_jobs WHERE job_kind='session_reprocess' "
                "AND input_hash=?",
                (input_hash,),
            ).fetchone()
        return {"job": dict(row), "reused": reused} if row is not None else None

    async def preview_delete(
        self,
        key: str,
        *,
        ttl_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """Issue a short-lived, one-use token for the exact current impact."""

        if not 30 <= int(ttl_seconds) <= 900:
            raise ValueError("delete preview lifetime must be between 30 and 900 seconds")
        async with self._lock:
            impact = await asyncio.to_thread(self._delete_impact_sync, key)
            if impact is None:
                return None
            if impact["status"] == "recording":
                raise ActiveSessionDeleteError(
                    "Stop or finalize the active recording before deleting this session."
                )
            fingerprint = self._impact_fingerprint(impact)
            token = secrets.token_urlsafe(32)
            expires_at = time.time() + int(ttl_seconds)
            self._delete_previews[token] = (key, fingerprint, expires_at)
            self._purge_delete_previews()
        return {
            "session_id": key,
            "confirmation_token": token,
            "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
            "irreversible": True,
            "impact": impact,
        }

    def _purge_delete_previews(self) -> None:
        now = time.time()
        expired = [
            token
            for token, (_key, _fingerprint, expires_at) in self._delete_previews.items()
            if expires_at <= now
        ]
        for token in expired:
            self._delete_previews.pop(token, None)

    @staticmethod
    def _impact_fingerprint(impact: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(impact, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _delete_impact_sync(self, key: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return self._delete_impact_from_db(db, key)

    def _delete_impact_from_db(
        self,
        db: sqlite3.Connection,
        key: str,
    ) -> dict[str, Any] | None:
        session = db.execute(
            "SELECT id, status, starred, legacy_session_uid, updated_at "
            "FROM recorded_sessions WHERE id=?",
            (key,),
        ).fetchone()
        if session is None:
            return None
        counts = db.execute(
            """
            SELECT (SELECT COUNT(*) FROM session_cars WHERE session_id=?) AS cars,
                   (SELECT COUNT(*) FROM recorded_laps l JOIN session_cars c
                     ON c.id=l.session_car_id WHERE c.session_id=?) AS laps,
                   (SELECT COUNT(*) FROM trace_manifests WHERE session_id=?) AS manifests,
                   (SELECT COUNT(*) FROM comparisons cmp JOIN recorded_laps l
                     ON l.id=cmp.candidate_lap_id JOIN session_cars c
                     ON c.id=l.session_car_id WHERE c.session_id=?) AS comparisons,
                   (SELECT COUNT(*) FROM findings f JOIN comparisons cmp
                     ON cmp.id=f.comparison_id JOIN recorded_laps l
                     ON l.id=cmp.candidate_lap_id JOIN session_cars c
                     ON c.id=l.session_car_id WHERE c.session_id=?) AS findings,
                   (SELECT COUNT(*) FROM analysis_jobs WHERE session_id=?) AS jobs
            """,
            (key, key, key, key, key, key),
        ).fetchone()
        artifacts = [
            {
                "kind": "trace_chunk",
                "relative_path": str(row["relative_path"]),
                "byte_count": int(row["byte_count"]),
            }
            for row in db.execute(
                """
                SELECT tc.relative_path, tc.byte_count
                FROM trace_chunks tc JOIN trace_manifests tm ON tm.id=tc.manifest_id
                WHERE tm.session_id=? ORDER BY tc.relative_path
                """,
                (key,),
            ).fetchall()
        ]
        for row in db.execute(
            "SELECT id FROM trace_manifests WHERE session_id=? ORDER BY id", (key,)
        ).fetchall():
            artifacts.append(
                {
                    "kind": "trace_manifest",
                    "relative_path": f"manifests/{row['id']}.json",
                    "byte_count": 0,
                }
            )
        for row in db.execute(
            """
            SELECT relative_path, byte_count FROM raw_captures
            WHERE session_id=? ORDER BY relative_path
            """,
            (key,),
        ).fetchall():
            artifacts.append(
                {
                    "kind": "raw_capture",
                    "relative_path": str(row["relative_path"]),
                    "byte_count": int(row["byte_count"]),
                }
            )
        legacy_counts: dict[str, int] = {}
        legacy_uid = session["legacy_session_uid"]
        if legacy_uid is not None:
            existing_tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for table in self._legacy_session_tables():
                if table in existing_tables:
                    legacy_counts[table] = int(
                        db.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE session_uid=?",
                            (legacy_uid,),
                        ).fetchone()[0]
                    )
        return {
            "status": str(session["status"]),
            "starred": bool(session["starred"]),
            "session_revision": str(session["updated_at"]),
            "records": {
                "session_cars": int(counts["cars"]),
                "laps": int(counts["laps"]),
                "trace_manifests": int(counts["manifests"]),
                "comparisons": int(counts["comparisons"]),
                "findings": int(counts["findings"]),
                "analysis_jobs": int(counts["jobs"]),
                "legacy": legacy_counts,
            },
            "artifacts": artifacts,
            "total_artifact_bytes": sum(
                int(item["byte_count"]) for item in artifacts
            ),
        }

    @staticmethod
    def _legacy_session_tables() -> tuple[str, ...]:
        # Explicit allow-list: never discover deletion targets dynamically.
        return (
            "corner_metrics",
            "setup_runs",
            "feedback",
            "line_metrics",
            "radio_messages",
            "strategy_snapshots",
            "proactive_calls",
            "briefings",
            "session_events",
            "laps",
            "sessions",
        )

    @staticmethod
    def _safe_artifact_path(root: Path, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise DeletePreviewError(
                f"Refusing unsafe catalogued artifact path: {relative_path}"
            )
        resolved_root = root.resolve()
        resolved = (resolved_root / relative).resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise DeletePreviewError(
                f"Refusing artifact path outside its configured root: {relative_path}"
            ) from exc
        return resolved

    async def delete_session(
        self,
        key: str,
        confirmation_token: str,
        *,
        trace_root: Path | None = None,
        capture_root: Path | None = None,
    ) -> dict[str, Any] | None:
        """Delete only the rows and files named by an unchanged preview."""

        async with self._lock:
            self._purge_delete_previews()
            preview = self._delete_previews.pop(confirmation_token, None)
            if preview is None:
                raise DeletePreviewError(
                    "The deletion token is invalid, expired, or has already been used."
                )
            preview_key, expected_fingerprint, expires_at = preview
            if preview_key != key or expires_at <= time.time():
                raise DeletePreviewError(
                    "The deletion token is expired or belongs to another session."
                )
            return await asyncio.to_thread(
                self._delete_session_sync,
                key,
                expected_fingerprint,
                confirmation_token,
                trace_root or (self.path.parent / "traces"),
                capture_root or (self.path.parent / "captures"),
            )

    def _delete_session_sync(
        self,
        key: str,
        expected_fingerprint: str,
        confirmation_token: str,
        trace_root: Path,
        capture_root: Path,
    ) -> dict[str, Any] | None:
        staged: list[tuple[Path, Path, str]] = []
        missing: list[str] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                impact = self._delete_impact_from_db(db, key)
                if impact is None:
                    db.rollback()
                    return None
                if impact["status"] == "recording":
                    raise ActiveSessionDeleteError(
                        "Stop or finalize the active recording before deleting this session."
                    )
                if self._impact_fingerprint(impact) != expected_fingerprint:
                    raise DeletePreviewError(
                        "The session changed after preview. Review the deletion impact again."
                    )
                suffix = confirmation_token[:12]
                seen: set[Path] = set()
                for artifact in impact["artifacts"]:
                    root = (
                        capture_root
                        if artifact["kind"] == "raw_capture"
                        else trace_root
                    )
                    original = self._safe_artifact_path(
                        root, str(artifact["relative_path"])
                    )
                    if original in seen:
                        continue
                    seen.add(original)
                    if not original.exists():
                        missing.append(str(artifact["relative_path"]))
                        continue
                    if not original.is_file():
                        raise DeletePreviewError(
                            f"Refusing to delete non-file artifact: {artifact['relative_path']}"
                        )
                    staged_path = original.with_name(
                        f"{original.name}.pitwall-delete-{suffix}"
                    )
                    if staged_path.exists():
                        raise DeletePreviewError(
                            f"A prior deletion staging file already exists: {staged_path.name}"
                        )
                    original.replace(staged_path)
                    staged.append((original, staged_path, str(artifact["relative_path"])))

                legacy_uid = db.execute(
                    "SELECT legacy_session_uid FROM recorded_sessions WHERE id=?", (key,)
                ).fetchone()[0]
                if legacy_uid is not None:
                    existing_tables = {
                        str(row[0])
                        for row in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    for table in self._legacy_session_tables():
                        if table in existing_tables:
                            db.execute(
                                f"DELETE FROM {table} WHERE session_uid=?", (legacy_uid,)
                            )
                db.execute("DELETE FROM raw_captures WHERE session_id=?", (key,))
                deleted = db.execute(
                    "DELETE FROM recorded_sessions WHERE id=?", (key,)
                ).rowcount
                if deleted != 1:
                    raise RuntimeError("Session disappeared while deletion was in progress.")
                db.execute(
                    """
                    INSERT INTO audit_events(event_type, subject_id, detail_json, created_at)
                    VALUES ('session_deleted', ?, ?, ?)
                    """,
                    (
                        key,
                        json.dumps(
                            {
                                "records": impact["records"],
                                "artifact_count": len(impact["artifacts"]),
                                "missing_artifacts": missing,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        _utc_now(),
                    ),
                )
                db.commit()
            except Exception:
                db.rollback()
                for original, staged_path, _relative in reversed(staged):
                    if staged_path.exists() and not original.exists():
                        staged_path.replace(original)
                raise

        cleanup_errors: list[str] = []
        removed: list[str] = []
        for _original, staged_path, relative in staged:
            try:
                staged_path.unlink()
                removed.append(relative)
            except OSError as exc:
                cleanup_errors.append(f"{relative}: {exc}")
        return {
            "session_id": key,
            "deleted": True,
            "records": impact["records"],
            "removed_artifacts": removed,
            "missing_artifacts": missing,
            "cleanup_errors": cleanup_errors,
        }
