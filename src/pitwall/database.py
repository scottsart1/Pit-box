from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


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

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_sync(self) -> None:
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
                    created_at REAL NOT NULL
                );

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

    async def upsert_session(self, state: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_session_sync, state)

    def _upsert_session_sync(self, state: dict[str, Any]) -> None:
        if not state.get("session_uid"):
            return
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sessions(
                    session_uid, track_id, track_name, session_type,
                    mode_profile, started_at, total_laps, setup_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_uid) DO UPDATE SET
                    track_id=excluded.track_id,
                    track_name=excluded.track_name,
                    session_type=excluded.session_type,
                    mode_profile=excluded.mode_profile,
                    total_laps=excluded.total_laps,
                    setup_json=excluded.setup_json
                """,
                (
                    _session_uid_to_sqlite(state["session_uid"]),
                    int(state.get("track_id", -1)),
                    state.get("track_name", "—"),
                    state.get("session_type", "Unknown"),
                    state.get("mode_profile", "idle"),
                    time.time(),
                    int(state.get("total_laps", 0)),
                    json.dumps(state.get("car_setup", {})),
                ),
            )

    async def save_lap(
        self,
        lap: dict[str, Any],
        corner_metrics: list[dict[str, Any]],
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_lap_sync, lap, corner_metrics)

    def _save_lap_sync(
        self,
        lap: dict[str, Any],
        corner_metrics: list[dict[str, Any]],
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO laps(
                    session_uid, track_id, track_name, session_type, lap_num,
                    lap_time_ms, valid, compound, tyre_age_start, tyre_age_end,
                    wear_start_json, wear_end_json, temps_end_json,
                    fuel_start_kg, fuel_end_kg, position,
                    s1_ms, s2_ms, s3_ms, pit_status, pit_lane_time_ms,
                    setup_json, trace_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_uid, lap_num) DO UPDATE SET
                    lap_time_ms=excluded.lap_time_ms,
                    valid=excluded.valid,
                    compound=excluded.compound,
                    tyre_age_start=excluded.tyre_age_start,
                    tyre_age_end=excluded.tyre_age_end,
                    wear_start_json=excluded.wear_start_json,
                    wear_end_json=excluded.wear_end_json,
                    temps_end_json=excluded.temps_end_json,
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
            db.execute(
                "DELETE FROM corner_metrics WHERE session_uid=? AND lap_num=?",
                (_session_uid_to_sqlite(lap["session_uid"]), int(lap["lap_num"])),
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
                        _session_uid_to_sqlite(lap["session_uid"]),
                        int(lap["track_id"]),
                        int(lap["lap_num"]),
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

    async def tyre_history_model(
        self,
        track_id: int,
        limit: int = 300,
    ) -> dict[str, Any]:
        """Return personal tyre wear/pace evidence from prior valid laps.

        This deliberately returns compact aggregates rather than traces so it
        is cheap enough to load whenever strategy is recomputed.
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._tyre_history_model_sync,
                int(track_id),
                max(20, min(1000, int(limit))),
            )

    def _tyre_history_model_sync(
        self,
        track_id: int,
        limit: int,
    ) -> dict[str, Any]:
        from statistics import median

        def robust_slope(points: list[tuple[float, float]]) -> float | None:
            if len(points) < 3:
                return None
            slopes: list[float] = []
            for index, (x1, y1) in enumerate(points):
                for x2, y2 in points[index + 1 :]:
                    if x2 != x1:
                        slopes.append((y2 - y1) / (x2 - x1))
            return float(median(slopes)) if slopes else None

        with self._connect() as db:
            rows = db.execute(
                """
                SELECT compound, tyre_age_start, tyre_age_end,
                       wear_start_json, wear_end_json,
                       lap_time_ms, fuel_start_kg, fuel_end_kg
                FROM laps
                WHERE track_id=? AND valid=1 AND lap_time_ms>0
                  AND pit_status=0
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (track_id, limit),
            ).fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            compound = str(item.get("compound") or "UNKNOWN").upper()
            if compound not in {"SOFT", "MEDIUM", "HARD", "INTER", "WET"}:
                continue
            try:
                start = json.loads(item.get("wear_start_json") or "[]")
                end = json.loads(item.get("wear_end_json") or "[]")
            except json.JSONDecodeError:
                start, end = [], []
            item["wear_start"] = start
            item["wear_end"] = end
            grouped.setdefault(compound, []).append(item)

        result: dict[str, Any] = {"track_id": track_id, "compounds": {}}
        for compound, laps in grouped.items():
            wear_rates: list[float] = []
            max_wear_rates: list[float] = []
            wheel_wear_rates: list[list[float]] = [[], [], [], []]
            pace_points: list[tuple[float, float]] = []
            max_fuel = max(
                (
                    (
                        float(lap.get("fuel_start_kg") or 0)
                        + float(lap.get("fuel_end_kg") or 0)
                    )
                    / 2
                    for lap in laps
                ),
                default=0.0,
            )
            for lap in laps:
                start = lap.get("wear_start") or []
                end = lap.get("wear_end") or []
                age_delta = max(
                    1,
                    int(lap.get("tyre_age_end") or 0)
                    - int(lap.get("tyre_age_start") or 0),
                )
                if len(start) == 4 and len(end) == 4:
                    deltas = [
                        max(0.0, float(b) - float(a)) / age_delta
                        for a, b in zip(start, end)
                    ]
                    wear_rates.append(sum(deltas) / 4)
                    max_wear_rates.append(max(deltas))
                    for wheel_index, delta in enumerate(deltas):
                        wheel_wear_rates[wheel_index].append(delta)
                age = float(lap.get("tyre_age_end") or 0)
                lap_s = float(lap.get("lap_time_ms") or 0) / 1000.0
                fuel_avg = (
                    float(lap.get("fuel_start_kg") or 0)
                    + float(lap.get("fuel_end_kg") or 0)
                ) / 2
                # Normalize later, lighter-fuel laps back toward the heaviest
                # fuel state so fuel burn does not masquerade as low tyre deg.
                normalized_lap_s = lap_s + max(0.0, max_fuel - fuel_avg) * 0.030
                if age > 0 and normalized_lap_s > 0:
                    pace_points.append((age, normalized_lap_s))

            slope = robust_slope(pace_points)
            result["compounds"][compound] = {
                "sample_size": len(laps),
                "wear_sample_size": len(wear_rates),
                "wear_per_lap_pct": round(float(median(wear_rates)), 3)
                if wear_rates
                else None,
                "max_wear_per_lap_pct": round(float(median(max_wear_rates)), 3)
                if max_wear_rates
                else None,
                "wheel_wear_per_lap_pct": [
                    round(float(median(values)), 3) if values else None
                    for values in wheel_wear_rates
                ],
                "slope_s_per_lap": (
                    round(max(-0.1, min(1.5, float(slope))), 4)
                    if slope is not None
                    else None
                ),
                "source": "personal_track_history",
            }
        return result

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
                    json.dumps(strategy.get("plans", [])),
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
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO proactive_calls(
                    session_uid, track_id, lap_num, event_type,
                    payload_json, text, delivered, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _session_uid_to_sqlite(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    int(state.get("current_lap", 0)),
                    str(event.get("type", "unknown")),
                    json.dumps(event.get("payload", {})),
                    text,
                    1 if delivered else 0,
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
