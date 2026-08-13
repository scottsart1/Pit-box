"""Deterministic, persisted lap comparison and trace-query service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .telemetry.alignment import (
    GapRule,
    SignalKind,
    SignalSpec,
    align_distance_traces,
    clean_distance_axis,
    resample_distance,
)
from .telemetry.availability import Availability
from .telemetry.coaching import (
    CoachingFinding,
    MetricFact,
    SegmentEvidence,
    build_coaching_evidence,
    rank_findings,
)
from .telemetry.comparison import (
    ComparisonInput,
    CompatibilityClass,
    CompatibilityReport,
    LapContext,
    build_comparison_result,
    classify_compatibility,
)
from .telemetry.segments import (
    DetectionStatus,
    DistanceWindow,
    Segment,
    detect_extremum_event,
    detect_sustained_event,
    make_segment,
)
from .trace_archive import normalise_legacy_trace
from .trace_store import TraceFormatError, TraceManifestMissing, TraceStore

ALGORITHM_BUNDLE = "analysis_4.2.0"


class ComparisonServiceError(RuntimeError):
    code = "comparison_error"


class LapNotFoundError(ComparisonServiceError):
    code = "lap_not_found"


class TraceUnavailableError(ComparisonServiceError):
    code = "trace_unavailable"


class ReferenceCompatibilityError(ComparisonServiceError):
    code = "reference_not_strict"

    def __init__(self, report: CompatibilityReport) -> None:
        self.report = report
        detail = "; ".join(report.caveats) or report.classification.value
        super().__init__(f"Reference is {report.classification.value}: {detail}")


class UnsupportedReferenceError(ComparisonServiceError):
    code = "unsupported_reference"


@dataclass(frozen=True, slots=True)
class LapRecord:
    id: str
    session_id: str
    session_car_id: str
    legacy_lap_id: int | None
    trace_manifest_id: str | None
    trace_checksum: str | None
    lap_number: int
    lap_time_ms: int | None
    valid: bool
    coverage_ratio: float
    quality_score: float
    tyre_compound: str | None
    tyre_age_laps: int | None
    fuel_start_kg: float | None
    weather_class: str | None
    pit_context: bool
    flag_context: bool
    track_id: int | None
    layout_signature: str | None
    session_type: str | None
    packet_format: int | None
    started_at: str | None
    car_index: int
    team_id: int | None
    display_name: str | None
    is_player: bool


@dataclass(frozen=True, slots=True)
class LapTrace:
    lap_id: str
    distance_m: NDArray[np.float64]
    signals: dict[str, NDArray[np.float64]]
    checksum: str
    source: str
    coverage: dict[str, float]
    provenance: dict[str, str]


@dataclass(frozen=True, slots=True)
class _SegmentSelection:
    segments: tuple[Segment, ...]
    track_model_id: str | None
    track_model_version: int | None
    track_model_checksum: str | None
    segment_model_id: str | None
    segment_model_version: int | None
    segment_model_checksum: str | None
    model_quality: float
    segment_source: str

    @property
    def persisted(self) -> bool:
        return self.track_model_id is not None and self.segment_model_id is not None

    @property
    def track_hash_key(self) -> str:
        return self.track_model_id or "distance_axis_only_v1"

    @property
    def segment_hash_key(self) -> str:
        return self.segment_model_id or "uniform_review_v1"

    def hash_settings(self) -> dict[str, object]:
        settings: dict[str, object] = {
            "spacing_m": 0.5,
            "sign": "positive_candidate_later",
        }
        if self.persisted:
            settings.update(
                {
                    "track_model_id": self.track_model_id,
                    "track_model_version": self.track_model_version,
                    "track_model_checksum": self.track_model_checksum,
                    "segment_model_id": self.segment_model_id,
                    "segment_model_version": self.segment_model_version,
                    "segment_model_checksum": self.segment_model_checksum,
                    "model_quality": self.model_quality,
                }
            )
        return settings

    def projection(self) -> dict[str, Any]:
        return {
            "track_model_id": self.track_model_id,
            "track_model_version": self.track_model_version,
            "segment_model_id": self.segment_model_id,
            "segment_model_version": self.segment_model_version,
            "model_quality": self.model_quality,
            "segment_source": self.segment_source,
            "fallback": not self.persisted,
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _nan_array(length: int) -> NDArray[np.float64]:
    return np.full(length, np.nan, dtype=np.float64)


def _finite_array(values: Any, available: Any | None = None) -> NDArray[np.float64]:
    result = np.asarray(values, dtype=np.float64).copy()
    if available is not None:
        mask = np.asarray(available, dtype=bool)
        if mask.shape != result.shape:
            raise TraceFormatError("trace availability mask does not match its values")
        result[~mask] = np.nan
    return result


def _coverage(values: NDArray[np.float64]) -> float:
    return float(np.count_nonzero(np.isfinite(values)) / max(1, len(values)))


def _envelope_indices(
    length: int,
    max_points: int,
    primary: NDArray[np.float64] | None,
) -> NDArray[np.int64]:
    """Return a bounded min/max envelope that retains endpoints and extrema."""

    if length <= max_points:
        return np.arange(length, dtype=np.int64)
    target_bins = max(1, (max_points - 2) // 2)
    edges = np.linspace(1, length - 1, target_bins + 1, dtype=np.int64)
    selected = {0, length - 1}
    for start, end in pairwise(edges):
        if end <= start:
            continue
        if primary is None:
            selected.add(int(start))
            continue
        window = primary[start:end]
        finite = np.flatnonzero(np.isfinite(window))
        if not finite.size:
            selected.add(int(start))
            continue
        local = window[finite]
        selected.add(int(start + finite[int(np.argmin(local))]))
        selected.add(int(start + finite[int(np.argmax(local))]))
    ordered = np.asarray(sorted(selected), dtype=np.int64)
    if ordered.size > max_points:
        keep = np.linspace(0, ordered.size - 1, max_points, dtype=np.int64)
        ordered = ordered[keep]
    return ordered


def _metric_fact(
    key: str,
    candidate: float | None,
    reference: float | None,
    unit: str,
    confidence: float,
    segment_id: str,
) -> MetricFact:
    available = candidate is not None and reference is not None
    return MetricFact(
        key,
        candidate,
        reference,
        unit,
        confidence=confidence if available else 0.0,
        availability=(Availability.DERIVED if available else Availability.UNAVAILABLE),
        evidence_ids=(f"{segment_id}:{key}",) if available else (),
    )


def _event_value(event: Any) -> tuple[float | None, float]:
    if event.status is not DetectionStatus.DETECTED:
        return None, 0.0
    return event.distance_m, float(event.confidence)


class ComparisonService:
    """One source for Review, Lap Lab, tools, exports, and explanations."""

    def __init__(self, database_path: Path, trace_store: TraceStore) -> None:
        self.database_path = Path(database_path)
        self.trace_store = trace_store
        self._write_lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _load_lap_record_sync(self, lap_key: str) -> LapRecord:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT l.*, c.session_id, c.car_index, c.team_id,
                       c.display_name, c.is_player,
                       s.track_id, s.track_layout_signature, s.session_type,
                       s.packet_format, s.started_at,
                       tm.checksum AS trace_checksum
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                JOIN recorded_sessions s ON s.id=c.session_id
                LEFT JOIN trace_manifests tm ON tm.id=l.trace_manifest_id
                WHERE l.id=?
                """,
                (lap_key,),
            ).fetchone()
        if row is None:
            raise LapNotFoundError(f"Lap {lap_key!r} does not exist")
        return LapRecord(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            session_car_id=str(row["session_car_id"]),
            legacy_lap_id=(
                int(row["legacy_lap_id"]) if row["legacy_lap_id"] is not None else None
            ),
            trace_manifest_id=(
                str(row["trace_manifest_id"]) if row["trace_manifest_id"] else None
            ),
            trace_checksum=str(row["trace_checksum"])
            if row["trace_checksum"]
            else None,
            lap_number=int(row["lap_number"]),
            lap_time_ms=int(row["lap_time_ms"]) if row["lap_time_ms"] else None,
            valid=bool(row["valid"]),
            coverage_ratio=float(row["coverage_ratio"] or 0.0),
            quality_score=float(row["quality_score"] or 0.0),
            tyre_compound=str(row["tyre_compound"]) if row["tyre_compound"] else None,
            tyre_age_laps=(
                int(row["tyre_age_laps"]) if row["tyre_age_laps"] is not None else None
            ),
            fuel_start_kg=(
                float(row["fuel_start_kg"])
                if row["fuel_start_kg"] is not None
                else None
            ),
            weather_class=str(row["weather_class"]) if row["weather_class"] else None,
            pit_context=bool(row["pit_context"]),
            flag_context=bool(row["flag_context"]),
            track_id=int(row["track_id"]) if row["track_id"] is not None else None,
            layout_signature=(
                str(row["track_layout_signature"])
                if row["track_layout_signature"]
                else None
            ),
            session_type=str(row["session_type"]) if row["session_type"] else None,
            packet_format=(
                int(row["packet_format"]) if row["packet_format"] is not None else None
            ),
            started_at=str(row["started_at"]) if row["started_at"] else None,
            car_index=int(row["car_index"]),
            team_id=int(row["team_id"]) if row["team_id"] is not None else None,
            display_name=str(row["display_name"]) if row["display_name"] else None,
            is_player=bool(row["is_player"]),
        )

    async def lap_record(self, lap_key: str) -> LapRecord:
        return await asyncio.to_thread(self._load_lap_record_sync, lap_key)

    @staticmethod
    def _canonical_manifest_trace(record: LapRecord, trace_slice: Any) -> LapTrace:
        distance = _finite_array(trace_slice.axis_values, trace_slice.axis_available)
        raw = {
            name: _finite_array(series.values, series.available)
            for name, series in trace_slice.series.items()
        }
        length = len(distance)

        def first(*names: str) -> NDArray[np.float64]:
            for name in names:
                if name in raw:
                    return raw[name]
            return _nan_array(length)

        speed = first("speed_mps", "speed")
        if not np.any(np.isfinite(speed)) and "speed_kph" in raw:
            speed = raw["speed_kph"] / 3.6
        signals = {
            "time_s": first("time_s", "session_time_s", "elapsed_time_s"),
            "speed": speed,
            "brake": first("brake"),
            "throttle": first("throttle"),
            "steering": first("steering", "steer"),
            "gear": first("gear"),
            "line_n": first("line_n"),
            "world_x": first("world_x", "x"),
            "world_z": first("world_z", "z"),
        }
        checksum = (
            record.trace_checksum
            or hashlib.sha256(np.ascontiguousarray(distance).tobytes()).hexdigest()
        )
        return LapTrace(
            record.id,
            distance,
            signals,
            checksum,
            "trace_store",
            {name: _coverage(values) for name, values in signals.items()},
            {
                name: "observed" if np.any(np.isfinite(values)) else "unavailable"
                for name, values in signals.items()
            },
        )

    @staticmethod
    def _merge_motion_geometry(base: LapTrace, motion_slice: Any) -> LapTrace:
        """Project observed motion positions onto the telemetry distance axis.

        Full-field manifests store controls and world motion in separate sample
        groups with independent packet rates.  The telemetry axis stays
        canonical; motion values are interpolated only across bounded 8 metre
        gaps, never extrapolated, and are labelled derived in the projection.
        """

        source_distance = _finite_array(
            motion_slice.axis_values, motion_slice.axis_available
        )
        raw: dict[str, NDArray[np.float64]] = {}
        for canonical, aliases in {
            "world_x": ("world_x", "x"),
            "world_z": ("world_z", "z"),
        }.items():
            for alias in aliases:
                series = motion_slice.series.get(alias)
                if series is not None:
                    raw[canonical] = _finite_array(series.values, series.available)
                    break
        if not raw:
            return base
        clean = clean_distance_axis(source_distance, raw, epoch_policy="last")
        finite_target = np.isfinite(base.distance_m)
        if not np.any(finite_target) or not clean.distance_m.size:
            return base
        targets, inverse = np.unique(
            base.distance_m[finite_target], return_inverse=True
        )
        spec = SignalSpec(SignalKind.CONTINUOUS, GapRule(8.0, 0.0))
        resampled = resample_distance(
            clean,
            targets,
            specs={name: spec for name in raw},
        )
        signals = {name: values.copy() for name, values in base.signals.items()}
        provenance = dict(base.provenance)
        for name, values in resampled.signals.items():
            projected = _nan_array(len(base.distance_m))
            projected[finite_target] = values[inverse]
            existing = signals.get(name, _nan_array(len(base.distance_m)))
            fill = ~np.isfinite(existing) & np.isfinite(projected)
            if np.any(fill):
                existing[fill] = projected[fill]
                signals[name] = existing
                provenance[name] = "derived"
        return LapTrace(
            base.lap_id,
            base.distance_m,
            signals,
            base.checksum,
            base.source,
            {name: _coverage(values) for name, values in signals.items()},
            provenance,
        )

    def _legacy_trace_sync(self, record: LapRecord) -> LapTrace:
        if record.legacy_lap_id is None:
            raise TraceUnavailableError(
                f"Lap {record.id!r} has no trace manifest or legacy trace"
            )
        with self._connect() as db:
            row = db.execute(
                "SELECT trace_json FROM laps WHERE id=?", (record.legacy_lap_id,)
            ).fetchone()
        if row is None:
            raise TraceUnavailableError(
                f"Legacy trace for lap {record.id!r} is missing"
            )
        raw_json = str(row["trace_json"] or "[]")
        try:
            raw_trace = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise TraceUnavailableError(
                f"Legacy trace for lap {record.id!r} is corrupt"
            ) from exc
        rows = normalise_legacy_trace(raw_trace, track_length_m=None)
        if len(rows) < 2:
            raise TraceUnavailableError(
                f"Lap {record.id!r} has insufficient trace coverage"
            )
        distance = np.asarray([row["distance_m"] for row in rows], dtype=np.float64)

        def values(name: str) -> NDArray[np.float64]:
            return np.asarray(
                [np.nan if row.get(name) is None else row[name] for row in rows],
                dtype=np.float64,
            )

        signals = {
            "time_s": values("time_s"),
            "speed": values("speed_mps"),
            "brake": values("brake"),
            "throttle": values("throttle"),
            "steering": values("steering"),
            "gear": values("gear"),
            "line_n": _nan_array(len(rows)),
            "world_x": values("world_x"),
            "world_z": values("world_z"),
        }
        return LapTrace(
            record.id,
            distance,
            signals,
            hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
            "legacy_json",
            {name: _coverage(item) for name, item in signals.items()},
            {
                name: "observed" if np.any(np.isfinite(item)) else "unavailable"
                for name, item in signals.items()
            },
        )

    def _load_trace_sync(self, record: LapRecord) -> LapTrace:
        if record.trace_manifest_id:
            try:
                # A full-field manifest contains separate motion, telemetry,
                # status, damage, and lap-data chunks.  Comparison inputs must
                # come from the telemetry group; selecting the first ordinal
                # could otherwise choose a sparse damage chunk simply because
                # its name sorts first.
                try:
                    trace_slice = self.trace_store.read_range(
                        record.trace_manifest_id,
                        sample_group="telemetry",
                    )
                    trace = self._canonical_manifest_trace(record, trace_slice)
                    try:
                        motion_slice = self.trace_store.read_range(
                            record.trace_manifest_id,
                            fields=("world_x", "world_z"),
                            sample_group="motion",
                        )
                    except KeyError:
                        return trace
                    return self._merge_motion_geometry(trace, motion_slice)
                except KeyError:
                    # Player traces produced by older 4.2 development builds
                    # may contain a single unnamed/useful group.  Retain that
                    # additive compatibility path without masking corrupt files.
                    trace_slice = self.trace_store.read_range(record.trace_manifest_id)
                return self._canonical_manifest_trace(record, trace_slice)
            except (FileNotFoundError, KeyError, TraceFormatError, TraceManifestMissing) as exc:
                if record.legacy_lap_id is None:
                    # Report this as an unavailable trace rather than letting an
                    # OS error escape as a 500. The lap row is intact and the
                    # session stays listable; only this lap's telemetry is
                    # unreadable, and the caller needs to be told which.
                    raise TraceUnavailableError(
                        f"telemetry for lap {record.lap_id} cannot be read: {exc}"
                    ) from exc
        return self._legacy_trace_sync(record)

    async def lap_trace(self, lap_key: str) -> tuple[LapRecord, LapTrace]:
        record = await self.lap_record(lap_key)
        trace = await asyncio.to_thread(self._load_trace_sync, record)
        return record, trace

    @staticmethod
    def _context(record: LapRecord) -> LapContext:
        return LapContext(
            packet_format=record.packet_format,
            track_id=record.track_id,
            layout_signature=record.layout_signature,
            team_id=record.team_id,
            session_type=record.session_type,
            weather_class=record.weather_class,
            tyre_compound=record.tyre_compound,
            tyre_age_laps=record.tyre_age_laps,
            fuel_kg=record.fuel_start_kg,
            valid_lap=record.valid,
            pit_context=record.pit_context,
            flag_context=record.flag_context,
            coverage_ratio=record.coverage_ratio,
        )

    def _reference_rows_sync(self, candidate: LapRecord) -> list[LapRecord]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT l.id
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                JOIN recorded_sessions s ON s.id=c.session_id
                WHERE s.track_id IS ? AND l.id<>?
                  AND (l.trace_manifest_id IS NOT NULL OR l.legacy_lap_id IS NOT NULL)
                ORDER BY l.valid DESC, l.lap_time_ms ASC, l.created_at DESC
                LIMIT 200
                """,
                (candidate.track_id, candidate.id),
            ).fetchall()
        return [self._load_lap_record_sync(str(row["id"])) for row in rows]

    async def list_references(self, candidate_lap_id: str) -> dict[str, Any]:
        candidate = await self.lap_record(candidate_lap_id)
        rows = await asyncio.to_thread(self._reference_rows_sync, candidate)
        candidates: list[dict[str, Any]] = []
        for reference in rows:
            report = classify_compatibility(
                self._context(candidate), self._context(reference)
            )
            if report.classification is CompatibilityClass.INCOMPATIBLE:
                continue
            strict_rank = {
                CompatibilityClass.STRICT: 0,
                CompatibilityClass.COMPARABLE_WITH_CAVEATS: 1,
                CompatibilityClass.CONTEXT_ONLY: 2,
                CompatibilityClass.INCOMPATIBLE: 3,
            }[report.classification]
            candidates.append(
                {
                    "lap_id": reference.id,
                    "lap_number": reference.lap_number,
                    "lap_time_ms": reference.lap_time_ms,
                    "driver": reference.display_name
                    or f"Car {reference.car_index + 1}",
                    "session_id": reference.session_id,
                    "is_player": reference.is_player,
                    "compatibility": self._compatibility_dict(report),
                    "reasons": [
                        "same track/layout",
                        "valid lap" if reference.valid else "invalid/context lap",
                        "typed trace"
                        if reference.trace_manifest_id
                        else "legacy trace",
                    ],
                    "_rank": (
                        strict_rank,
                        0 if reference.valid else 1,
                        -(reference.coverage_ratio or 0.0),
                        reference.lap_time_ms or 2**31,
                    ),
                }
            )
        candidates.sort(key=lambda item: item.pop("_rank"))
        for index, item in enumerate(candidates):
            item["suggested"] = index == 0
        return {
            "schema_version": 1,
            "candidate_lap_id": candidate.id,
            "items": candidates,
        }

    def _resolve_reference_sync(
        self,
        candidate: LapRecord,
        kind: str,
        lap_key: str | None,
    ) -> LapRecord:
        if kind in {"lap", "field_driver", "saved_benchmark"}:
            if not lap_key:
                raise UnsupportedReferenceError(f"{kind} requires lap_id")
            if str(lap_key) == str(candidate.id):
                # Comparing a lap with itself produces 0.000 s everywhere and
                # 100% coverage — numbers that look like a perfect lap rather
                # than a meaningless comparison. The derived-reference SQL
                # already excludes the candidate; direct references must too.
                raise UnsupportedReferenceError(
                    "The reference lap is the candidate itself; pick a different lap."
                )
            return self._load_lap_record_sync(lap_key)
        with self._connect() as db:
            if kind == "session_pb":
                row = db.execute(
                    """
                    SELECT l.id FROM recorded_laps l
                    JOIN session_cars c ON c.id=l.session_car_id
                    WHERE c.session_id=? AND c.is_player=1 AND l.valid=1
                      AND l.lap_time_ms>0 AND l.id<>?
                    ORDER BY l.lap_time_ms LIMIT 1
                    """,
                    (candidate.session_id, candidate.id),
                ).fetchone()
            elif kind == "all_time_pb":
                row = db.execute(
                    """
                    SELECT l.id FROM recorded_laps l
                    JOIN session_cars c ON c.id=l.session_car_id
                    JOIN recorded_sessions s ON s.id=c.session_id
                    WHERE s.track_id IS ? AND c.is_player=1 AND l.valid=1
                      AND l.lap_time_ms>0 AND l.id<>?
                    ORDER BY l.lap_time_ms LIMIT 1
                    """,
                    (candidate.track_id, candidate.id),
                ).fetchone()
            elif kind == "recent_representative":
                rows = db.execute(
                    """
                    SELECT l.id, l.lap_time_ms FROM recorded_laps l
                    JOIN session_cars c ON c.id=l.session_car_id
                    JOIN recorded_sessions s ON s.id=c.session_id
                    WHERE s.track_id IS ? AND c.is_player=1 AND l.valid=1
                      AND l.lap_time_ms>0 AND l.id<>?
                    ORDER BY l.created_at DESC LIMIT 9
                    """,
                    (candidate.track_id, candidate.id),
                ).fetchall()
                if not rows:
                    row = None
                else:
                    ordered = sorted(rows, key=lambda item: int(item["lap_time_ms"]))
                    row = ordered[len(ordered) // 2]
            else:
                raise UnsupportedReferenceError(
                    f"Reference kind {kind!r} requires a derived reference builder"
                )
        if row is None:
            raise TraceUnavailableError(f"No {kind} reference is available")
        return self._load_lap_record_sync(str(row["id"]))

    @staticmethod
    def _uniform_segments(
        start_m: float, end_m: float, count: int = 10
    ) -> tuple[Segment, ...]:
        if end_m - start_m < 10.0:
            raise TraceUnavailableError("Comparable distance coverage is too short")
        edges = np.linspace(start_m, end_m, min(15, max(1, count)) + 1)
        return tuple(
            make_segment(
                "distance_review",
                index,
                f"Review sector {index + 1}",
                float(edges[index]),
                float(edges[index + 1]),
                confidence=0.65,
                source="uniform_distance_v1",
            )
            for index in range(len(edges) - 1)
        )

    @staticmethod
    def _clip_phase_window(
        value: Any,
        start_m: float,
        end_m: float,
    ) -> DistanceWindow | None:
        if value is None:
            return None
        if isinstance(value, dict):
            raw_start = value.get("start_m", value.get("start"))
            raw_end = value.get("end_m", value.get("end"))
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            raw_start, raw_end = value
        else:
            raise ValueError("invalid persisted segment phase window")
        lower = max(start_m, float(raw_start))
        upper = min(end_m, float(raw_end))
        return DistanceWindow(lower, upper) if upper > lower else None

    def _persisted_segment_selection_sync(
        self,
        candidate: LapRecord,
        start_m: float,
        end_m: float,
    ) -> _SegmentSelection | None:
        """Resolve one exact-layout active model or return the safe fallback signal."""

        if candidate.track_id is None or not candidate.layout_signature:
            return None
        try:
            with self._connect() as db:
                model = db.execute(
                    """
                    SELECT tm.id AS track_model_id,
                           tm.model_version AS track_model_version,
                           tm.checksum AS track_model_checksum,
                           tm.quality_score AS model_quality,
                           sm.id AS segment_model_id,
                           sm.version AS segment_model_version,
                           sm.checksum AS segment_model_checksum,
                           sm.source AS segment_source
                    FROM track_models tm
                    JOIN segment_models sm ON sm.track_model_id=tm.id
                    WHERE tm.track_id=? AND tm.layout_signature=?
                      AND tm.active=1 AND sm.active=1
                    ORDER BY tm.model_version DESC, sm.version DESC
                    LIMIT 1
                    """,
                    (candidate.track_id, candidate.layout_signature),
                ).fetchone()
                if model is None:
                    return None
                rows = db.execute(
                    """
                    SELECT * FROM segments
                    WHERE segment_model_id=?
                    ORDER BY ordinal, start_m, id
                    """,
                    (str(model["segment_model_id"]),),
                ).fetchall()
        except sqlite3.OperationalError:
            # A pre-4.2 database can still compare legacy laps after startup;
            # the migration path will supply these tables on its next run.
            return None
        quality = float(model["model_quality"])
        if (
            not np.isfinite(quality)
            or not 0.0 < quality <= 1.0
            or len(rows) > 64
        ):
            return None
        try:
            clipped: list[Segment] = []
            seen_ids: set[str] = set()
            seen_ordinals: set[int] = set()
            previous_end: float | None = None
            for row in rows:
                segment_id = str(row["id"])
                ordinal = int(row["ordinal"])
                if segment_id in seen_ids or ordinal in seen_ordinals:
                    raise ValueError("duplicate persisted segment identity")
                seen_ids.add(segment_id)
                seen_ordinals.add(ordinal)
                raw_start = float(row["start_m"])
                raw_end = float(row["end_m"])
                if not np.isfinite(raw_start) or not np.isfinite(raw_end):
                    raise ValueError("non-finite persisted segment boundary")
                if raw_end <= raw_start:
                    raise ValueError("persisted segment has an invalid boundary")
                if previous_end is not None and raw_start < previous_end - 1e-6:
                    raise ValueError("persisted segments overlap")
                previous_end = raw_end
                clipped_start = max(start_m, raw_start)
                clipped_end = min(end_m, raw_end)
                if clipped_end <= clipped_start:
                    continue
                phase = json.loads(str(row["phase_json"] or "{}"))
                if not isinstance(phase, dict):
                    raise TypeError("persisted segment phase metadata is invalid")
                confidence = float(row["confidence"])
                if not np.isfinite(confidence):
                    raise ValueError("persisted segment confidence is non-finite")
                clipped.append(
                    Segment(
                        id=segment_id,
                        label=str(row["label"]),
                        ordinal=ordinal,
                        start_m=clipped_start,
                        end_m=clipped_end,
                        brake_window=self._clip_phase_window(
                            phase.get("brake"), clipped_start, clipped_end
                        ),
                        turn_in_window=self._clip_phase_window(
                            phase.get("turn_in"), clipped_start, clipped_end
                        ),
                        apex_window=self._clip_phase_window(
                            phase.get("apex"), clipped_start, clipped_end
                        ),
                        exit_window=self._clip_phase_window(
                            phase.get("exit"), clipped_start, clipped_end
                        ),
                        direction=(
                            str(row["direction"])
                            if row["direction"] is not None
                            else None
                        ),
                        confidence=confidence,
                        source=str(phase.get("source") or model["segment_source"]),
                    )
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not clipped:
            return None
        return _SegmentSelection(
            segments=tuple(clipped),
            track_model_id=str(model["track_model_id"]),
            track_model_version=int(model["track_model_version"]),
            track_model_checksum=str(model["track_model_checksum"]),
            segment_model_id=str(model["segment_model_id"]),
            segment_model_version=int(model["segment_model_version"]),
            segment_model_checksum=str(model["segment_model_checksum"]),
            model_quality=quality,
            segment_source=str(model["segment_source"]),
        )

    def _segment_selection_sync(
        self,
        candidate: LapRecord,
        start_m: float,
        end_m: float,
    ) -> _SegmentSelection:
        persisted = self._persisted_segment_selection_sync(
            candidate, start_m, end_m
        )
        if persisted is not None:
            return persisted
        return _SegmentSelection(
            segments=self._uniform_segments(start_m, end_m),
            track_model_id=None,
            track_model_version=None,
            track_model_checksum=None,
            segment_model_id=None,
            segment_model_version=None,
            segment_model_checksum=None,
            model_quality=0.65,
            segment_source="uniform_distance_v1",
        )

    @staticmethod
    def _clean(trace: LapTrace, track_length_m: float | None = None) -> Any:
        return clean_distance_axis(
            trace.distance_m,
            trace.signals,
            track_length_m=track_length_m,
            epoch_policy="last",
        )

    @staticmethod
    def _segment_facts(
        segment: Segment,
        distance: NDArray[np.float64],
        candidate: dict[str, NDArray[np.float64]],
        reference: dict[str, NDArray[np.float64]],
    ) -> dict[str, MetricFact]:
        inside = (distance >= segment.start_m) & (distance <= segment.end_m)
        d = distance[inside]
        if d.size < 3:
            return {}
        candidate_speed = candidate["speed"][inside]
        reference_speed = reference["speed"][inside]
        candidate_brake = candidate["brake"][inside]
        reference_brake = reference["brake"][inside]
        candidate_throttle = candidate["throttle"][inside]
        reference_throttle = reference["throttle"][inside]
        candidate_min = detect_extremum_event(
            d, candidate_speed, kind="minimum_speed", mode="minimum"
        )
        reference_min = detect_extremum_event(
            d, reference_speed, kind="minimum_speed", mode="minimum"
        )
        candidate_brake_event = detect_sustained_event(
            d,
            candidate_brake,
            kind="brake_onset",
            enter_threshold=0.10,
            exit_threshold=0.05,
            min_duration_m=2.0,
        )
        reference_brake_event = detect_sustained_event(
            d,
            reference_brake,
            kind="brake_onset",
            enter_threshold=0.10,
            exit_threshold=0.05,
            min_duration_m=2.0,
        )
        candidate_throttle_event = detect_sustained_event(
            d,
            candidate_throttle,
            kind="throttle_pickup",
            enter_threshold=0.25,
            exit_threshold=0.15,
            min_duration_m=2.0,
            select="last",
        )
        reference_throttle_event = detect_sustained_event(
            d,
            reference_throttle,
            kind="throttle_pickup",
            enter_threshold=0.25,
            exit_threshold=0.15,
            min_duration_m=2.0,
            select="last",
        )
        brake_c, brake_cc = _event_value(candidate_brake_event)
        brake_r, brake_rc = _event_value(reference_brake_event)
        throttle_c, throttle_cc = _event_value(candidate_throttle_event)
        throttle_r, throttle_rc = _event_value(reference_throttle_event)
        min_c, min_cc = _event_value(candidate_min)
        min_r, min_rc = _event_value(reference_min)

        def min_value(event: Any) -> float | None:
            return (
                float(event.value) if event.status is DetectionStatus.DETECTED else None
            )

        def steering_corrections(values: NDArray[np.float64]) -> float | None:
            finite = values[np.isfinite(values)]
            if finite.size < 5:
                return None
            changes = np.diff(finite)
            meaningful = changes[np.abs(changes) >= 0.03]
            if meaningful.size < 2:
                return 0.0
            return float(
                np.count_nonzero(np.sign(meaningful[1:]) != np.sign(meaningful[:-1]))
            )

        def gear_at(
            values: NDArray[np.float64], distance_value: float | None
        ) -> float | None:
            if distance_value is None:
                return None
            finite = np.flatnonzero(np.isfinite(values))
            if not finite.size:
                return None
            index = finite[int(np.argmin(np.abs(d[finite] - distance_value)))]
            return float(values[index])

        def elapsed_time(values: NDArray[np.float64]) -> float | None:
            finite = np.flatnonzero(np.isfinite(values))
            if finite.size < 2:
                return None
            elapsed = float(values[finite[-1]] - values[finite[0]])
            return elapsed if elapsed > 0 else None

        confidence = min(
            _coverage(candidate_speed),
            _coverage(reference_speed),
        )
        return {
            "segment_time_s": _metric_fact(
                "segment_time_s",
                elapsed_time(candidate["time_s"][inside]),
                elapsed_time(reference["time_s"][inside]),
                "s",
                min(
                    _coverage(candidate["time_s"][inside]),
                    _coverage(reference["time_s"][inside]),
                ),
                segment.id,
            ),
            "brake_onset_m": _metric_fact(
                "brake_onset_m",
                brake_c,
                brake_r,
                "m",
                min(brake_cc, brake_rc),
                segment.id,
            ),
            "minimum_speed_distance_m": _metric_fact(
                "minimum_speed_distance_m",
                min_c,
                min_r,
                "m",
                min(min_cc, min_rc),
                segment.id,
            ),
            "minimum_speed_mps": _metric_fact(
                "minimum_speed_mps",
                min_value(candidate_min),
                min_value(reference_min),
                "m/s",
                confidence,
                segment.id,
            ),
            "throttle_pickup_m": _metric_fact(
                "throttle_pickup_m",
                throttle_c,
                throttle_r,
                "m",
                min(throttle_cc, throttle_rc),
                segment.id,
            ),
            "steering_corrections": _metric_fact(
                "steering_corrections",
                steering_corrections(candidate["steering"][inside]),
                steering_corrections(reference["steering"][inside]),
                "count",
                min(
                    _coverage(candidate["steering"][inside]),
                    _coverage(reference["steering"][inside]),
                ),
                segment.id,
            ),
            "gear_at_apex": _metric_fact(
                "gear_at_apex",
                gear_at(candidate["gear"][inside], min_c),
                gear_at(reference["gear"][inside], min_r),
                "gear",
                min(
                    _coverage(candidate["gear"][inside]),
                    _coverage(reference["gear"][inside]),
                ),
                segment.id,
            ),
        }

    @staticmethod
    def _compatibility_dict(report: CompatibilityReport) -> dict[str, Any]:
        return {
            "class": report.classification.value,
            "compatibility_weight": report.compatibility_weight,
            "allows_coaching": report.allows_coaching,
            "caveats": list(report.caveats),
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "severity": issue.severity.value,
                }
                for issue in report.issues
            ],
        }

    @staticmethod
    def _finding_dict(
        finding: CoachingFinding, rank: int | None = None
    ) -> dict[str, Any]:
        result = {
            "finding_id": finding.id,
            "type": finding.finding_type.value,
            "segment_id": finding.segment_id,
            "segment_label": finding.segment_label,
            "phase": finding.phase,
            # None, not 0.0, when the segment's time delta was unmeasurable:
            # "Measured: 0.000 s" beside an improvement instruction reads as a
            # contradiction, and it was one.
            "measured_loss_s": (
                finding.measured_loss_s if finding.loss_measured else None
            ),
            "attributed_low_s": finding.attributed_low_s,
            "attributed_high_s": finding.attributed_high_s,
            "confidence": finding.confidence,
            "repeatability": finding.repeatability,
            "opportunity_score": finding.opportunity_score,
            "facts": [
                {
                    "key": fact.key,
                    "candidate": fact.candidate,
                    "reference": fact.reference,
                    "delta": fact.delta,
                    "unit": fact.unit,
                    "confidence": fact.confidence,
                    "availability": fact.availability.value,
                    "evidence_ids": list(fact.evidence_ids),
                }
                for fact in finding.facts
            ],
            "evidence": list(finding.evidence_node_ids),
            "action": finding.action,
            "drill": finding.drill,
            "positive": finding.positive,
            "algorithm_version": finding.algorithm_version,
        }
        if rank is not None:
            result["rank"] = rank
        return result

    async def create_comparison(
        self,
        candidate_lap_id: str,
        *,
        reference_kind: str,
        reference_lap_id: str | None,
        allow_caveated_reference: bool = False,
    ) -> dict[str, Any]:
        candidate = await self.lap_record(candidate_lap_id)
        reference = await asyncio.to_thread(
            self._resolve_reference_sync,
            candidate,
            reference_kind,
            reference_lap_id,
        )
        compatibility = classify_compatibility(
            self._context(candidate), self._context(reference)
        )
        if compatibility.classification is CompatibilityClass.INCOMPATIBLE:
            raise ReferenceCompatibilityError(compatibility)
        if (
            compatibility.classification is not CompatibilityClass.STRICT
            and not allow_caveated_reference
        ):
            raise ReferenceCompatibilityError(compatibility)
        candidate_trace, reference_trace = await asyncio.gather(
            asyncio.to_thread(self._load_trace_sync, candidate),
            asyncio.to_thread(self._load_trace_sync, reference),
        )
        candidate_clean = self._clean(candidate_trace)
        reference_clean = self._clean(reference_trace)
        aligned = align_distance_traces(
            candidate_clean,
            reference_clean,
            spacing_m=0.5,
        )
        selection = await asyncio.to_thread(
            self._segment_selection_sync,
            candidate,
            float(aligned.distance_m[0]),
            float(aligned.distance_m[-1]),
        )
        segments = selection.segments
        comparison_input = ComparisonInput(
            candidate_lap_id=candidate.id,
            reference_kind=reference_kind,
            reference_key=reference.id,
            candidate_checksum=candidate_trace.checksum,
            reference_checksum=reference_trace.checksum,
            track_model_version=selection.track_hash_key,
            segment_model_version=selection.segment_hash_key,
            algorithm_bundle=ALGORITHM_BUNDLE,
            settings=selection.hash_settings(),
        )
        result = build_comparison_result(
            comparison_input,
            compatibility,
            aligned.distance_m,
            aligned.candidate.signals["time_s"],
            aligned.reference.signals["time_s"],
            segments,
            model_quality=selection.model_quality,
        )
        segment_by_id = {segment.id: segment for segment in segments}
        evidence_rows: list[tuple[Segment, dict[str, MetricFact], float, float]] = []
        all_findings: list[CoachingFinding] = []
        for segment_result in result.segment_results:
            segment = segment_by_id[segment_result.segment_id]
            facts = self._segment_facts(
                segment,
                aligned.distance_m,
                dict(aligned.candidate.signals),
                dict(aligned.reference.signals),
            )
            loss_measured = segment_result.delta_s is not None
            loss = float(segment_result.delta_s) if loss_measured else 0.0
            evidence_rows.append((segment, facts, loss, segment_result.coverage))
            if compatibility.allows_coaching:
                coaching = build_coaching_evidence(
                    SegmentEvidence(
                        segment.id,
                        segment.label,
                        loss,
                        facts,
                        loss_measured=loss_measured,
                        repeatability=0.0,
                        sample_count=1,
                        data_coverage=segment_result.coverage,
                        model_quality=selection.model_quality,
                        compatibility_weight=compatibility.compatibility_weight,
                        gear_outcome_supported=True,
                    )
                )
                all_findings.extend(coaching.findings)
        ranked = rank_findings(all_findings, limit=3)
        findings = [self._finding_dict(item.finding, item.rank) for item in ranked]
        await self._persist(
            result,
            candidate,
            reference,
            evidence_rows,
            findings,
            selection,
        )
        return {
            "schema_version": 1,
            "comparison_id": result.comparison_id,
            "candidate": self._lap_card(candidate),
            "reference": {"kind": reference_kind, **self._lap_card(reference)},
            "compatibility": self._compatibility_dict(compatibility),
            "algorithm_bundle": result.algorithm_bundle,
            "coverage_ratio": result.coverage_ratio,
            "quality_score": result.quality_score,
            "lap_delta_s": result.lap_delta_s,
            "sign_convention": "positive means candidate arrived later",
            "reconciled": result.reconciled,
            "reconciliation_error_s": result.reconciliation_error_s,
            "analysis_model": selection.projection(),
            "segments": [
                {
                    "segment_id": item.segment_id,
                    "label": segment_by_id[item.segment_id].label,
                    "ordinal": segment_by_id[item.segment_id].ordinal,
                    "start_m": item.start_m,
                    "end_m": item.end_m,
                    "delta_s": item.delta_s,
                    "coverage": item.coverage,
                    "model_source": segment_by_id[item.segment_id].source,
                }
                for item in result.segment_results
            ],
            "findings": findings,
            "created_at": _utc_now(),
        }

    @staticmethod
    def _lap_card(record: LapRecord) -> dict[str, Any]:
        return {
            "lap_id": record.id,
            "session_id": record.session_id,
            "lap_number": record.lap_number,
            "lap_time_ms": record.lap_time_ms,
            "driver": record.display_name or f"Car {record.car_index + 1}",
            "car_index": record.car_index,
            "valid": record.valid,
            "tyre_compound": record.tyre_compound,
            "weather_class": record.weather_class,
            "coverage_ratio": record.coverage_ratio,
        }

    async def _persist(
        self,
        result: Any,
        candidate: LapRecord,
        reference: LapRecord,
        segments: list[tuple[Segment, dict[str, MetricFact], float, float]],
        findings: list[dict[str, Any]],
        selection: _SegmentSelection,
    ) -> None:
        compatibility_json = json.dumps(
            self._compatibility_dict(result.compatibility),
            sort_keys=True,
            separators=(",", ":"),
        )

        def write() -> None:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                    INSERT INTO comparisons(
                        id, candidate_lap_id, reference_kind, reference_key,
                        compatibility_class, compatibility_json, track_model_id,
                        segment_model_id, algorithm_bundle, input_hash,
                        lap_delta_ms, coverage_ratio, quality_score, state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                    ON CONFLICT(input_hash) DO UPDATE SET
                        compatibility_class=excluded.compatibility_class,
                        compatibility_json=excluded.compatibility_json,
                        track_model_id=excluded.track_model_id,
                        segment_model_id=excluded.segment_model_id,
                        lap_delta_ms=excluded.lap_delta_ms,
                        coverage_ratio=excluded.coverage_ratio,
                        quality_score=excluded.quality_score,
                        state='ready'
                    """,
                    (
                        result.comparison_id,
                        candidate.id,
                        result.comparison_input.reference_kind,
                        reference.id,
                        result.compatibility.classification.value,
                        compatibility_json,
                        selection.track_model_id,
                        selection.segment_model_id,
                        result.algorithm_bundle,
                        result.input_hash,
                        None
                        if result.lap_delta_s is None
                        else round(result.lap_delta_s * 1000),
                        result.coverage_ratio,
                        result.quality_score,
                        _utc_now(),
                    ),
                )
                db.execute(
                    "DELETE FROM comparison_segment_results WHERE comparison_id=?",
                    (result.comparison_id,),
                )
                by_id = {item.segment_id: item for item in result.segment_results}
                for segment, facts, _loss, coverage in segments:
                    item = by_id[segment.id]
                    metrics = {
                        key: {
                            "candidate": fact.candidate,
                            "reference": fact.reference,
                            "delta": fact.delta,
                            "unit": fact.unit,
                            "availability": fact.availability.value,
                            "confidence": fact.confidence,
                            "evidence_ids": list(fact.evidence_ids),
                        }
                        for key, fact in facts.items()
                    }
                    db.execute(
                        """
                        INSERT INTO comparison_segment_results(
                            comparison_id, ordinal, segment_key, label, start_m,
                            end_m, delta_s, coverage_ratio, metrics_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result.comparison_id,
                            segment.ordinal,
                            segment.id,
                            segment.label,
                            segment.start_m,
                            segment.end_m,
                            item.delta_s,
                            coverage,
                            json.dumps(metrics, sort_keys=True, separators=(",", ":")),
                        ),
                    )
                db.execute(
                    "DELETE FROM findings WHERE comparison_id=?",
                    (result.comparison_id,),
                )
                for finding in findings:
                    db.execute(
                        """
                        INSERT INTO findings(
                            id, comparison_id, segment_id, finding_type, rank,
                            measured_loss_ms, attributed_low_ms, attributed_high_ms,
                            confidence, repeatability, opportunity_score, facts_json,
                            evidence_json, action_key, algorithm_version
                        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(finding["finding_id"]),
                            result.comparison_id,
                            str(finding["type"]),
                            int(finding["rank"]),
                            (
                                None
                                if finding["measured_loss_s"] is None
                                else round(float(finding["measured_loss_s"]) * 1000)
                            ),
                            round(float(finding["attributed_low_s"]) * 1000),
                            round(float(finding["attributed_high_s"]) * 1000),
                            float(finding["confidence"]),
                            float(finding["repeatability"]),
                            float(finding["opportunity_score"]),
                            json.dumps(
                                {
                                    "segment_id": finding["segment_id"],
                                    "segment_label": finding["segment_label"],
                                    "phase": finding["phase"],
                                    "facts": finding["facts"],
                                    "drill": finding["drill"],
                                    "positive": finding["positive"],
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            json.dumps(finding["evidence"], separators=(",", ":")),
                            str(finding["action"]),
                            str(finding["algorithm_version"]),
                        ),
                    )
                db.commit()

        async with self._write_lock:
            await asyncio.to_thread(write)

    @staticmethod
    def _stored_analysis_model(
        db: sqlite3.Connection,
        comparison: sqlite3.Row,
    ) -> dict[str, Any]:
        track_model_id = comparison["track_model_id"]
        segment_model_id = comparison["segment_model_id"]
        if track_model_id is None or segment_model_id is None:
            return {
                "track_model_id": None,
                "track_model_version": None,
                "segment_model_id": None,
                "segment_model_version": None,
                "model_quality": 0.65,
                "segment_source": "uniform_distance_v1",
                "fallback": True,
            }
        row = db.execute(
            """
            SELECT tm.model_version AS track_model_version,
                   tm.quality_score AS model_quality,
                   sm.version AS segment_model_version,
                   sm.source AS segment_source
            FROM track_models tm
            JOIN segment_models sm ON sm.track_model_id=tm.id
            WHERE tm.id=? AND sm.id=?
            """,
            (str(track_model_id), str(segment_model_id)),
        ).fetchone()
        if row is None:
            return {
                "track_model_id": str(track_model_id),
                "track_model_version": None,
                "segment_model_id": str(segment_model_id),
                "segment_model_version": None,
                "model_quality": None,
                "segment_source": "persisted_unknown",
                "fallback": False,
            }
        return {
            "track_model_id": str(track_model_id),
            "track_model_version": int(row["track_model_version"]),
            "segment_model_id": str(segment_model_id),
            "segment_model_version": int(row["segment_model_version"]),
            "model_quality": float(row["model_quality"]),
            "segment_source": str(row["segment_source"]),
            "fallback": False,
        }

    def _get_comparison_sync(self, comparison_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM comparisons WHERE id=?", (comparison_id,)
            ).fetchone()
            if row is None:
                raise ComparisonServiceError(
                    f"Comparison {comparison_id!r} does not exist"
                )
            segments = db.execute(
                """
                SELECT * FROM comparison_segment_results
                WHERE comparison_id=? ORDER BY ordinal
                """,
                (comparison_id,),
            ).fetchall()
            finding_rows = db.execute(
                "SELECT * FROM findings WHERE comparison_id=? ORDER BY rank",
                (comparison_id,),
            ).fetchall()
            analysis_model = self._stored_analysis_model(db, row)
        candidate = self._load_lap_record_sync(str(row["candidate_lap_id"]))
        reference = self._load_lap_record_sync(str(row["reference_key"]))
        findings = []
        for finding in finding_rows:
            facts = json.loads(str(finding["facts_json"]))
            findings.append(
                {
                    "finding_id": str(finding["id"]),
                    "type": str(finding["finding_type"]),
                    "rank": int(finding["rank"]),
                    "segment_id": facts.get("segment_id"),
                    "segment_label": facts.get("segment_label"),
                    "phase": facts.get("phase"),
                    "measured_loss_s": (
                        None
                        if finding["measured_loss_ms"] is None
                        else int(finding["measured_loss_ms"]) / 1000.0
                    ),
                    "attributed_low_s": (
                        None
                        if finding["attributed_low_ms"] is None
                        else int(finding["attributed_low_ms"]) / 1000.0
                    ),
                    "attributed_high_s": (
                        None
                        if finding["attributed_high_ms"] is None
                        else int(finding["attributed_high_ms"]) / 1000.0
                    ),
                    "confidence": float(finding["confidence"]),
                    "repeatability": finding["repeatability"],
                    "opportunity_score": float(finding["opportunity_score"]),
                    "facts": facts.get("facts", []),
                    "evidence": json.loads(str(finding["evidence_json"])),
                    "action": str(finding["action_key"]),
                    "drill": facts.get("drill"),
                    "positive": bool(facts.get("positive")),
                    "algorithm_version": str(finding["algorithm_version"]),
                }
            )
        return {
            "schema_version": 1,
            "comparison_id": str(row["id"]),
            "candidate": self._lap_card(candidate),
            "reference": {
                "kind": str(row["reference_kind"]),
                **self._lap_card(reference),
            },
            "compatibility": json.loads(str(row["compatibility_json"])),
            "algorithm_bundle": str(row["algorithm_bundle"]),
            "coverage_ratio": float(row["coverage_ratio"]),
            "quality_score": float(row["quality_score"]),
            "lap_delta_s": (
                None
                if row["lap_delta_ms"] is None
                else int(row["lap_delta_ms"]) / 1000.0
            ),
            "sign_convention": "positive means candidate arrived later",
            "analysis_model": analysis_model,
            "segments": [
                {
                    "segment_id": str(item["segment_key"]),
                    "ordinal": int(item["ordinal"]),
                    "label": str(item["label"]),
                    "start_m": float(item["start_m"]),
                    "end_m": float(item["end_m"]),
                    "delta_s": item["delta_s"],
                    "coverage": float(item["coverage_ratio"]),
                    "model_source": analysis_model["segment_source"],
                    "metrics": json.loads(str(item["metrics_json"])),
                }
                for item in segments
            ],
            "findings": findings,
            "created_at": str(row["created_at"]),
        }

    async def get_comparison(self, comparison_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_comparison_sync, comparison_id)

    async def get_findings(self, comparison_id: str) -> dict[str, Any]:
        result = await self.get_comparison(comparison_id)
        return {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "findings": result["findings"],
        }

    @staticmethod
    def _trace_projection(
        lap_id: str,
        trace: LapTrace,
        *,
        fields: list[str],
        start_m: float | None,
        end_m: float | None,
        max_points: int,
    ) -> dict[str, Any]:
        selected = np.ones(len(trace.distance_m), dtype=bool)
        if start_m is not None:
            selected &= trace.distance_m >= start_m
        if end_m is not None:
            selected &= trace.distance_m <= end_m
        distance = trace.distance_m[selected]
        canonical = {
            "speed": ("m/s", trace.signals["speed"][selected]),
            "brake": ("ratio", trace.signals["brake"][selected]),
            "throttle": ("ratio", trace.signals["throttle"][selected]),
            "steering": ("ratio", trace.signals["steering"][selected]),
            "gear": ("gear", trace.signals["gear"][selected]),
            "time": ("s", trace.signals["time_s"][selected]),
            "line_n": ("m", trace.signals["line_n"][selected]),
            "world_x": ("m", trace.signals["world_x"][selected]),
            "world_z": ("m", trace.signals["world_z"][selected]),
        }
        requested = list(
            dict.fromkeys(fields or ["speed", "brake", "throttle", "steering", "gear"])
        )
        primary = next(
            (
                canonical[name][1]
                for name in requested
                if name in canonical and np.any(np.isfinite(canonical[name][1]))
            ),
            None,
        )
        indices = _envelope_indices(len(distance), max_points, primary)
        series: dict[str, Any] = {}
        for name in requested:
            if name not in canonical:
                values = _nan_array(len(distance))
                unit = ""
            else:
                unit, values = canonical[name]
            selected_values = values[indices]
            available = np.isfinite(selected_values)
            series[name] = {
                "unit": unit,
                "values": [
                    float(value) if finite else None
                    for value, finite in zip(selected_values, available)
                ],
                "availability": (
                    trace.provenance.get(name, "observed")
                    if np.any(available)
                    else "unavailable"
                ),
                "coverage": _coverage(selected_values),
            }
        return {
            "schema_version": 1,
            "lap_id": lap_id,
            "axis": {
                "name": "distance",
                "unit": "m",
                "values": distance[indices].tolist(),
            },
            "series": series,
            "coverage": _coverage(distance),
            "downsample": {
                "method": "minmax_envelope_v1",
                "source_points": len(distance),
                "returned_points": len(indices),
            },
            "source": trace.source,
        }

    async def get_lap_trace(
        self,
        lap_id: str,
        *,
        fields: list[str],
        start_m: float | None = None,
        end_m: float | None = None,
        max_points: int = 1600,
    ) -> dict[str, Any]:
        _record, trace = await self.lap_trace(lap_id)
        return self._trace_projection(
            lap_id,
            trace,
            fields=fields,
            start_m=start_m,
            end_m=end_m,
            max_points=max_points,
        )

    async def get_lap_analysis(self, lap_id: str) -> dict[str, Any]:
        """Describe one lap on its own terms, with no reference lap.

        Lap Lab could only ever answer "how does this lap differ from that
        one", so a driver with a single interesting lap — a one-off session,
        a first visit to a circuit, a lap nobody has a counterpart for — had
        nothing to look at. Everything here is measured from the lap's own
        trace: no model, no comparison, no opinion about what a better lap
        would have looked like.
        """
        record, trace = await self.lap_trace(lap_id)
        distance = trace.distance_m
        if distance.size < 2:
            raise TraceUnavailableError("Lap trace is too short to analyze")
        speed = trace.signals.get("speed")
        if speed is None or speed.size != distance.size:
            raise TraceUnavailableError("Lap trace carries no speed channel")

        throttle = trace.signals.get("throttle")
        brake = trace.signals.get("brake")
        # Traces are stored in SI, so speed arrives in m/s; every value this
        # returns is labelled kph and converted here rather than leaving the
        # caller to guess which unit it received.
        speed_kph = speed * 3.6
        # Time is derived from distance and speed rather than assumed: a trace
        # is distance-indexed, and sample spacing is not uniform in time.
        steps = np.diff(distance)
        mid_speed = np.maximum((speed[:-1] + speed[1:]) / 2.0, 1.0)
        step_seconds = steps / mid_speed

        def _fraction(signal: NDArray[np.float64] | None, mask: Any) -> float | None:
            if signal is None or signal.size != distance.size:
                return None
            weighted = float(np.sum(step_seconds[mask[:-1]]))
            total = float(np.sum(step_seconds))
            return round(weighted / total * 100.0, 1) if total > 0 else None

        segments = await asyncio.to_thread(
            self._segment_selection_sync,
            record,
            float(distance[0]),
            float(distance[-1]),
        )
        segment_rows: list[dict[str, Any]] = []
        for segment in segments.segments:
            inside = (distance >= segment.start_m) & (distance <= segment.end_m)
            if int(np.count_nonzero(inside)) < 2:
                continue
            window = np.zeros_like(step_seconds, dtype=bool)
            window[inside[:-1] & inside[1:]] = True
            segment_rows.append(
                {
                    "segment_id": segment.id,
                    "label": segment.label,
                    "start_m": round(float(segment.start_m), 1),
                    "end_m": round(float(segment.end_m), 1),
                    "time_s": round(float(np.sum(step_seconds[window])), 3),
                    "minimum_speed_kph": round(float(np.min(speed_kph[inside])), 1),
                    "entry_speed_kph": round(float(speed_kph[inside][0]), 1),
                    "exit_speed_kph": round(float(speed_kph[inside][-1]), 1),
                    "availability": "observed",
                }
            )

        braking_events = 0
        if brake is not None and brake.size == distance.size:
            engaged = brake > 0.15
            braking_events = int(np.count_nonzero(engaged[1:] & ~engaged[:-1]))

        return {
            "lap_id": record.id,
            "lap_number": record.lap_number,
            "lap_time_ms": record.lap_time_ms,
            "valid": record.valid,
            "tyre_compound": record.tyre_compound,
            "tyre_age_laps": record.tyre_age_laps,
            "coverage_ratio": round(float(record.coverage_ratio), 3),
            "quality_score": round(float(record.quality_score), 3),
            "trace_source": trace.source,
            "distance_covered_m": round(float(distance[-1] - distance[0]), 1),
            "top_speed_kph": round(float(np.max(speed_kph)), 1),
            "minimum_speed_kph": round(float(np.min(speed_kph)), 1),
            "average_speed_kph": round(float(np.mean(speed_kph)), 1),
            "full_throttle_pct": _fraction(throttle, (throttle > 0.98) if throttle is not None else None),
            "braking_pct": _fraction(brake, (brake > 0.15) if brake is not None else None),
            "braking_events": braking_events,
            "segments": segment_rows,
            "segment_source": segments.segment_source,
            "provenance": dict(trace.provenance),
            "availability_note": (
                "Measured from this lap alone; no reference lap is involved, so "
                "there is no delta and no coaching verdict."
            ),
        }

    async def get_comparison_trace(
        self,
        comparison_id: str,
        *,
        fields: list[str],
        start_m: float | None = None,
        end_m: float | None = None,
        max_points: int = 1600,
    ) -> dict[str, Any]:
        comparison = await self.get_comparison(comparison_id)
        candidate_id = str(comparison["candidate"]["lap_id"])
        reference_id = str(comparison["reference"]["lap_id"])
        candidate, reference = await asyncio.gather(
            self.lap_trace(candidate_id), self.lap_trace(reference_id)
        )
        candidate_clean = self._clean(candidate[1])
        reference_clean = self._clean(reference[1])
        aligned = align_distance_traces(candidate_clean, reference_clean, spacing_m=0.5)
        delta = (
            aligned.candidate.signals["time_s"] - aligned.candidate.signals["time_s"][0]
        ) - (
            aligned.reference.signals["time_s"] - aligned.reference.signals["time_s"][0]
        )
        selected = np.ones(len(aligned.distance_m), dtype=bool)
        if start_m is not None:
            selected &= aligned.distance_m >= start_m
        if end_m is not None:
            selected &= aligned.distance_m <= end_m
        distance = aligned.distance_m[selected]
        candidate_signals = {
            **dict(aligned.candidate.signals),
            "delta": delta,
        }
        reference_signals = {
            **dict(aligned.reference.signals),
            "delta": np.zeros(delta.shape, dtype=np.float64),
        }
        aliases = {
            "speed": "speed",
            "brake": "brake",
            "throttle": "throttle",
            "steering": "steering",
            "gear": "gear",
            "line_n": "line_n",
            "delta": "delta",
        }
        units = {
            "speed": "m/s",
            "brake": "ratio",
            "throttle": "ratio",
            "steering": "ratio",
            "gear": "gear",
            "line_n": "m",
            "delta": "s",
        }
        requested = list(
            dict.fromkeys(
                fields or ["speed", "delta", "brake", "throttle", "steering", "gear"]
            )
        )
        primary_name = next(
            (aliases[name] for name in requested if name in aliases), "speed"
        )
        primary = candidate_signals[primary_name][selected]
        indices = _envelope_indices(len(distance), max_points, primary)

        def project(signals: dict[str, NDArray[np.float64]]) -> dict[str, Any]:
            output: dict[str, Any] = {}
            for name in requested:
                key = aliases.get(name)
                values = (
                    _nan_array(len(aligned.distance_m)) if key is None else signals[key]
                )
                values = values[selected][indices]
                finite = np.isfinite(values)
                output[name] = {
                    "unit": units.get(name, ""),
                    "values": [
                        float(value) if ok else None
                        for value, ok in zip(values, finite)
                    ],
                    "availability": "derived"
                    if name == "delta" and np.any(finite)
                    else "observed"
                    if np.any(finite)
                    else "unavailable",
                    "coverage": _coverage(values),
                }
            return output

        return {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "axis": {
                "name": "distance",
                "unit": "m",
                "values": distance[indices].tolist(),
            },
            "candidate": {"lap_id": candidate_id, "series": project(candidate_signals)},
            "reference": {"lap_id": reference_id, "series": project(reference_signals)},
            "coverage": float(min(aligned.common_coverage.values(), default=0.0)),
            "downsample": {
                "method": "minmax_envelope_v1",
                "source_points": len(distance),
                "returned_points": len(indices),
            },
            "sign_convention": "positive delta means candidate arrived later",
        }

    async def explain(self, comparison_id: str) -> dict[str, Any]:
        """Render typed findings without allowing language to recalculate them."""

        comparison = await self.get_comparison(comparison_id)
        findings = comparison["findings"]
        if not findings:
            text = "No prescriptive coaching finding passed the current evidence threshold."
        else:
            finding = findings[0]
            loss = finding.get("measured_loss_s")
            measured = (
                f"{float(loss):.2f} s was measured through {finding['segment_label']}. "
                if loss is not None
                else ""
            )
            text = measured + str(finding["action"])
        return {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "text": text,
            "finding_ids": [item["finding_id"] for item in findings],
            "source": "deterministic_fallback",
        }


__all__ = [
    "ComparisonService",
    "ComparisonServiceError",
    "LapNotFoundError",
    "ReferenceCompatibilityError",
    "TraceUnavailableError",
    "UnsupportedReferenceError",
]
