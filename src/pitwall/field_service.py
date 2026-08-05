"""Read-only, coverage-aware analytics for a persisted full field.

This service deliberately reads the additive 4.2 catalog rather than live state.
It therefore works when the game is not running and never fills missing opponent
data with zeroes.  Statistical aggregation is delegated to
``telemetry.field_analysis`` so the API, future tools, and exports share one
implementation.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from .telemetry.field_analysis import (
    CornerRecord,
    PaceRecord,
    build_corner_matrix,
    build_pace_matrix,
)
from .trace_store import TraceFormatError, TraceStore, TraceStoreError


class FieldServiceError(RuntimeError):
    """Base class for stable service errors exposed by the HTTP adapter."""

    code = "field_error"


class SessionNotFoundError(FieldServiceError):
    code = "session_not_found"


class DriverNotFoundError(FieldServiceError):
    code = "driver_not_found"


class ContextMask(IntFlag):
    """Stable v1 reasons why a lap is muted from comparable pace analysis."""

    INVALID_LAP = 1 << 0
    PIT_CONTEXT = 1 << 1
    FLAG_CONTEXT = 1 << 2
    LOW_COVERAGE = 1 << 3
    LAP_TIME_UNAVAILABLE = 1 << 4


CONTEXT_MASK_VERSION = 1
CONTEXT_MASK_LABELS: dict[ContextMask, str] = {
    ContextMask.INVALID_LAP: "invalid_lap",
    ContextMask.PIT_CONTEXT: "pit_context",
    ContextMask.FLAG_CONTEXT: "flag_context",
    ContextMask.LOW_COVERAGE: "low_coverage",
    ContextMask.LAP_TIME_UNAVAILABLE: "lap_time_unavailable",
}


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _matrix_values(values: np.ndarray[Any, Any]) -> list[list[float | None]]:
    return [
        [_finite_or_none(value) for value in row]
        for row in np.asarray(values).tolist()
    ]


def _vector_values(values: np.ndarray[Any, Any]) -> list[float | None]:
    return [_finite_or_none(value) for value in np.asarray(values).tolist()]


def _metric(
    value: str | float | bool | None,
    *,
    unit: str | None = None,
    availability: str,
    reason: str | None = None,
    n: int = 0,
) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "availability": availability,
        "reason": reason,
        "n": max(0, int(n)),
    }


@dataclass(frozen=True, slots=True)
class _StoredLap:
    id: str
    car_id: str
    car_index: int
    identity_revision: int
    display_name: str
    lap_number: int
    timeline_epoch: int
    lap_time_ms: int | None
    valid: bool
    pit_context: bool
    flag_context: bool
    coverage: float
    quality: float
    compound: str | None
    tyre_age_laps: int | None
    fuel_start_kg: float | None
    fuel_end_kg: float | None
    weather_class: str | None
    legacy_position: int | None
    trace_manifest_id: str | None


class FieldAnalysisService:
    """Bounded, app-agnostic read model for one saved session's field."""

    def __init__(
        self,
        database_path: Path,
        *,
        trace_store: TraceStore | None = None,
        min_coverage: float = 0.80,
        min_cars_per_lap: int = 2,
        min_cars_per_segment: int = 2,
        max_lap_rows: int = 10_000,
        max_comparison_rows: int = 25_000,
    ) -> None:
        if not 0.0 <= min_coverage <= 1.0:
            raise ValueError("min_coverage must be between zero and one")
        if min_cars_per_lap < 1 or min_cars_per_segment < 1:
            raise ValueError("minimum car counts must be positive")
        if max_lap_rows < 24 or max_comparison_rows < 24:
            raise ValueError("query bounds must be at least 24 rows")
        self.database_path = Path(database_path)
        self.trace_store = trace_store
        self.min_coverage = float(min_coverage)
        self.min_cars_per_lap = int(min_cars_per_lap)
        self.min_cars_per_segment = int(min_cars_per_segment)
        self.max_lap_rows = int(max_lap_rows)
        self.max_comparison_rows = int(max_comparison_rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _session_row(
        db: sqlite3.Connection, session_id: str
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM recorded_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise SessionNotFoundError(
                f"Saved session {session_id!r} was not found"
            )
        return row

    @staticmethod
    def _car_rows(
        db: sqlite3.Connection, session_id: str
    ) -> list[sqlite3.Row]:
        return db.execute(
            """
            SELECT * FROM session_cars
            WHERE session_id=?
            ORDER BY car_index, identity_revision
            LIMIT 97
            """,
            (session_id,),
        ).fetchall()

    def _lap_rows(
        self, db: sqlite3.Connection, session_id: str
    ) -> tuple[list[sqlite3.Row], bool]:
        rows = db.execute(
            """
            SELECT l.*, c.session_id, c.car_index, c.identity_revision,
                   c.display_name, c.anonymized_name, c.is_player,
                   old.position AS legacy_position
            FROM recorded_laps l
            JOIN session_cars c ON c.id=l.session_car_id
            LEFT JOIN laps old ON old.id=l.legacy_lap_id
            WHERE c.session_id=?
              AND NOT EXISTS (
                  SELECT 1 FROM recorded_laps newer
                  WHERE newer.session_car_id=l.session_car_id
                    AND newer.lap_number=l.lap_number
                    AND newer.timeline_epoch>l.timeline_epoch
              )
            ORDER BY c.car_index, c.identity_revision, l.lap_number
            LIMIT ?
            """,
            (session_id, self.max_lap_rows + 1),
        ).fetchall()
        return rows[: self.max_lap_rows], len(rows) > self.max_lap_rows

    @staticmethod
    def _lap(row: sqlite3.Row) -> _StoredLap:
        display_name = row["display_name"] or row["anonymized_name"]
        return _StoredLap(
            id=str(row["id"]),
            car_id=str(row["session_car_id"]),
            car_index=int(row["car_index"]),
            identity_revision=int(row["identity_revision"]),
            display_name=str(display_name or f"Car {int(row['car_index']) + 1}"),
            lap_number=int(row["lap_number"]),
            timeline_epoch=int(row["timeline_epoch"]),
            lap_time_ms=(
                int(row["lap_time_ms"])
                if row["lap_time_ms"] is not None
                else None
            ),
            valid=bool(row["valid"]),
            pit_context=bool(row["pit_context"]),
            flag_context=bool(row["flag_context"]),
            coverage=float(row["coverage_ratio"] or 0.0),
            quality=float(row["quality_score"] or 0.0),
            compound=str(row["tyre_compound"]) if row["tyre_compound"] else None,
            tyre_age_laps=(
                int(row["tyre_age_laps"])
                if row["tyre_age_laps"] is not None
                else None
            ),
            fuel_start_kg=(
                float(row["fuel_start_kg"])
                if row["fuel_start_kg"] is not None
                else None
            ),
            fuel_end_kg=(
                float(row["fuel_end_kg"])
                if row["fuel_end_kg"] is not None
                else None
            ),
            weather_class=(
                str(row["weather_class"]) if row["weather_class"] else None
            ),
            legacy_position=(
                int(row["legacy_position"])
                if row["legacy_position"] is not None
                and int(row["legacy_position"]) > 0
                else None
            ),
            trace_manifest_id=(
                str(row["trace_manifest_id"])
                if row["trace_manifest_id"]
                else None
            ),
        )

    def _context_mask(self, lap: _StoredLap) -> ContextMask:
        mask = ContextMask(0)
        if not lap.valid:
            mask |= ContextMask.INVALID_LAP
        if lap.pit_context:
            mask |= ContextMask.PIT_CONTEXT
        if lap.flag_context:
            mask |= ContextMask.FLAG_CONTEXT
        if lap.coverage < self.min_coverage:
            mask |= ContextMask.LOW_COVERAGE
        if lap.lap_time_ms is None or lap.lap_time_ms <= 0:
            mask |= ContextMask.LAP_TIME_UNAVAILABLE
        return mask

    @staticmethod
    def _context_labels(mask: int | ContextMask) -> list[str]:
        value = ContextMask(mask)
        return [
            label for bit, label in CONTEXT_MASK_LABELS.items() if value & bit
        ]

    @staticmethod
    def context_schema() -> dict[str, Any]:
        return {
            "version": CONTEXT_MASK_VERSION,
            "bits": {str(int(bit)): label for bit, label in CONTEXT_MASK_LABELS.items()},
            "zero_means": "comparable clean-lap context",
        }

    @staticmethod
    def _car_card(row: sqlite3.Row) -> dict[str, Any]:
        index = int(row["car_index"])
        name = row["display_name"] or row["anonymized_name"] or f"Car {index + 1}"
        return {
            "car_id": str(row["id"]),
            "car_index": index,
            "identity_revision": int(row["identity_revision"]),
            "display_name": str(name),
            "team_id": int(row["team_id"]) if row["team_id"] is not None else None,
            "driver_id": (
                int(row["driver_id"]) if row["driver_id"] is not None else None
            ),
            "race_number": (
                int(row["race_number"])
                if row["race_number"] is not None
                else None
            ),
            "is_player": bool(row["is_player"]),
            "is_ai": bool(row["is_ai"]) if row["is_ai"] is not None else None,
        }

    @staticmethod
    def _clean_times(laps: list[_StoredLap], masks: dict[str, int]) -> list[int]:
        return [
            int(lap.lap_time_ms)
            for lap in laps
            if masks[lap.id] == 0 and lap.lap_time_ms is not None
        ]

    def _classification_rows(
        self,
        cars: list[sqlite3.Row],
        laps: list[_StoredLap],
    ) -> list[dict[str, Any]]:
        by_car: dict[str, list[_StoredLap]] = defaultdict(list)
        masks = {lap.id: int(self._context_mask(lap)) for lap in laps}
        for lap in laps:
            by_car[lap.car_id].append(lap)
        result: list[dict[str, Any]] = []
        for car in cars:
            card = self._car_card(car)
            car_laps = by_car[card["car_id"]]
            clean_times = self._clean_times(car_laps, masks)
            last = max(car_laps, key=lambda item: item.lap_number, default=None)
            pit_runs = 0
            previous_was_pit = False
            for lap in sorted(car_laps, key=lambda item: item.lap_number):
                if lap.pit_context and not previous_was_pit:
                    pit_runs += 1
                previous_was_pit = lap.pit_context
            result.append(
                {
                    **card,
                    "position": _metric(
                        None,
                        unit="place",
                        availability="unavailable",
                        reason=(
                            "The saved catalog has no official full-field "
                            "classification position."
                        ),
                    ),
                    "gap_ms": _metric(
                        None,
                        unit="ms",
                        availability="unavailable",
                        reason="No official saved full-field gap samples are catalogued.",
                    ),
                    "last_lap_ms": _metric(
                        last.lap_time_ms if last else None,
                        unit="ms",
                        availability=(
                            "observed"
                            if last is not None and last.lap_time_ms is not None
                            else "unavailable"
                        ),
                        reason=(
                            None
                            if last is not None and last.lap_time_ms is not None
                            else "The latest stored lap has no observed lap time."
                        ),
                        n=1 if last and last.lap_time_ms is not None else 0,
                    ),
                    "best_lap_ms": _metric(
                        min(clean_times) if clean_times else None,
                        unit="ms",
                        availability="derived" if clean_times else "unavailable",
                        reason=None if clean_times else "No comparable clean lap is available.",
                        n=len(clean_times),
                    ),
                    "median_clean_pace_ms": _metric(
                        float(median(clean_times)) if clean_times else None,
                        unit="ms",
                        availability="derived" if clean_times else "unavailable",
                        reason=None if clean_times else "No comparable clean lap is available.",
                        n=len(clean_times),
                    ),
                    "laps_recorded": _metric(
                        len(car_laps), availability="derived", n=len(car_laps)
                    ),
                    "pit_context_runs": _metric(
                        pit_runs,
                        availability="derived",
                        reason="Counts contiguous stored laps marked with pit context.",
                        n=len(car_laps),
                    ),
                    "compound": _metric(
                        last.compound if last else None,
                        availability=(
                            "observed" if last and last.compound else "unavailable"
                        ),
                        reason=(
                            None
                            if last and last.compound
                            else "No tyre compound was stored for the latest lap."
                        ),
                        n=1 if last and last.compound else 0,
                    ),
                    "tyre_age_laps": _metric(
                        last.tyre_age_laps if last else None,
                        unit="laps",
                        availability=(
                            "observed"
                            if last and last.tyre_age_laps is not None
                            else "unavailable"
                        ),
                        reason=(
                            None
                            if last and last.tyre_age_laps is not None
                            else "No tyre age was stored for the latest lap."
                        ),
                        n=1 if last and last.tyre_age_laps is not None else 0,
                    ),
                    "status": _metric(
                        None,
                        availability="unavailable",
                        reason="Final retirement/finish status is not in the saved catalog.",
                    ),
                    "freshness_ms": _metric(
                        None,
                        unit="ms",
                        availability="unavailable",
                        reason="This is an offline saved-session projection.",
                    ),
                }
            )
        return result

    def _summary_sync(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            session = self._session_row(db, session_id)
            cars = self._car_rows(db, session_id)
            rows, truncated = self._lap_rows(db, session_id)
        laps = [self._lap(row) for row in rows]
        classification = self._classification_rows(cars[:96], laps)
        warnings: list[str] = []
        if len(cars) > 96:
            warnings.append("Participant identity revisions exceed the 96-row safety bound.")
        if truncated:
            warnings.append(
                f"Lap query reached its {self.max_lap_rows}-row safety bound."
            )
        if not cars:
            warnings.append("No participants have been catalogued for this session.")
        return {
            "schema_version": 1,
            "session_id": session_id,
            "session": {
                "track_id": session["track_id"],
                "session_type": session["session_type"],
                "status": session["status"],
                "started_at": session["started_at"],
                "ended_at": session["ended_at"],
                "quality_score": session["quality_score"],
            },
            "classification_availability": "unavailable",
            "classification_reason": (
                "Official saved full-field position, gap, and finish status are not "
                "present in the current catalog; rows are ordered by car index."
            ),
            "cars_observed": len(cars),
            "lap_rows": len(laps),
            "classification": classification,
            "context_schema": self.context_schema(),
            "truncated": truncated or len(cars) > 96,
            "warnings": warnings,
        }

    async def summary(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._summary_sync, session_id)

    def _pace_sync(
        self, session_id: str, include_context: bool
    ) -> dict[str, Any]:
        with self._connect() as db:
            self._session_row(db, session_id)
            rows, truncated = self._lap_rows(db, session_id)
        laps = [self._lap(row) for row in rows]
        masks = {lap.id: int(self._context_mask(lap)) for lap in laps}
        records = [
            PaceRecord(
                driver_id=lap.car_id,
                lap_number=lap.lap_number,
                lap_time_s=(
                    None if lap.lap_time_ms is None else lap.lap_time_ms / 1000.0
                ),
                lap_id=lap.id,
                valid=lap.valid,
                coverage=lap.coverage,
                context_ok=include_context
                or not (lap.pit_context or lap.flag_context),
            )
            for lap in laps
        ]
        matrix = build_pace_matrix(
            records,
            min_coverage=self.min_coverage,
            min_cars_per_lap=self.min_cars_per_lap,
        )
        by_key = {(lap.car_id, lap.lap_number): lap for lap in laps}
        ordered_drivers = sorted(
            matrix.drivers, key=lambda item: self._driver_sort_key(item, laps)
        )
        row_by_driver = {
            driver_id: index for index, driver_id in enumerate(matrix.drivers)
        }
        cells: list[list[dict[str, Any]]] = []
        for car_id in ordered_drivers:
            row_index = row_by_driver[car_id]
            cell_row: list[dict[str, Any]] = []
            for column, lap_number in enumerate(matrix.lap_numbers):
                lap = by_key.get((car_id, lap_number))
                included = bool(matrix.valid_mask[row_index, column])
                mask = masks.get(lap.id, 0) if lap else int(
                    ContextMask.LAP_TIME_UNAVAILABLE
                )
                cell_row.append(
                    {
                        "lap_id": lap.id if lap else None,
                        "raw_lap_time_s": (
                            None
                            if lap is None or lap.lap_time_ms is None
                            else lap.lap_time_ms / 1000.0
                        ),
                        "lap_time_s": _finite_or_none(
                            matrix.lap_times_s[row_index, column]
                        ),
                        "delta_to_lap_median_s": _finite_or_none(
                            matrix.delta_to_lap_median_s[row_index, column]
                        ),
                        "performance_percentile": _finite_or_none(
                            matrix.performance_percentile[row_index, column]
                        ),
                        "coverage": lap.coverage if lap else 0.0,
                        "context_mask": mask,
                        "context": self._context_labels(mask),
                        "included": included,
                        "availability": "derived" if included else "unavailable",
                        "reason": (
                            None
                            if included
                            else (
                                ", ".join(self._context_labels(mask))
                                or "too few comparable cars on this lap"
                            )
                        ),
                    }
                )
            cells.append(cell_row)
        return {
            "schema_version": 1,
            "session_id": session_id,
            "availability": "derived" if matrix.total_valid else "unavailable",
            "reason": (
                None
                if matrix.total_valid
                else "No lap has enough comparable, covered field observations."
            ),
            "drivers": [
                self._driver_ref(car_id, laps) for car_id in ordered_drivers
            ],
            "lap_numbers": list(matrix.lap_numbers),
            "cells": cells,
            "lap_median_s": _vector_values(matrix.lap_median_s),
            "lap_mad_s": _vector_values(matrix.lap_mad_s),
            "n_by_lap": matrix.n_by_lap.tolist(),
            "coverage_by_driver": {
                car_id: matrix.coverage_by_driver[car_id]
                for car_id in ordered_drivers
            },
            "total_valid": matrix.total_valid,
            "total_cells": matrix.total_cells,
            "context_schema": self.context_schema(),
            "include_context": include_context,
            "source_rows": len(laps),
            "truncated": truncated,
        }

    @staticmethod
    def _driver_ref(car_id: str, laps: list[_StoredLap]) -> dict[str, Any]:
        lap = next(item for item in laps if item.car_id == car_id)
        return {
            "car_id": car_id,
            "car_index": lap.car_index,
            "identity_revision": lap.identity_revision,
            "display_name": lap.display_name,
        }

    @staticmethod
    def _driver_sort_key(car_id: str, laps: list[_StoredLap]) -> tuple[int, int, str]:
        lap = next(item for item in laps if item.car_id == car_id)
        return lap.car_index, lap.identity_revision, car_id

    async def pace(
        self, session_id: str, *, include_context: bool = False
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._pace_sync, session_id, bool(include_context)
        )

    @staticmethod
    def _candidate_segment_time(metrics: dict[str, Any]) -> float | None:
        for key in ("segment_time_s", "candidate_segment_time_s", "time_s"):
            value = metrics.get(key)
            if isinstance(value, dict):
                value = value.get("candidate")
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0:
                return number
        return None

    def _corner_rows(
        self, db: sqlite3.Connection, session_id: str
    ) -> tuple[list[sqlite3.Row], bool]:
        rows = db.execute(
            """
            SELECT csr.*, cmp.candidate_lap_id, cmp.created_at,
                   l.session_car_id, l.valid, l.pit_context,
                   l.flag_context, l.coverage_ratio
            FROM comparison_segment_results csr
            JOIN comparisons cmp ON cmp.id=csr.comparison_id
            JOIN recorded_laps l ON l.id=cmp.candidate_lap_id
            JOIN session_cars c ON c.id=l.session_car_id
            WHERE c.session_id=? AND cmp.state='ready'
              AND NOT EXISTS (
                  SELECT 1 FROM comparisons newer
                  WHERE newer.candidate_lap_id=cmp.candidate_lap_id
                    AND newer.state='ready'
                    AND (newer.created_at>cmp.created_at OR
                         (newer.created_at=cmp.created_at AND newer.id>cmp.id))
              )
            ORDER BY c.car_index, csr.ordinal
            LIMIT ?
            """,
            (session_id, self.max_comparison_rows + 1),
        ).fetchall()
        return rows[: self.max_comparison_rows], len(rows) > self.max_comparison_rows

    def _corners_sync(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._session_row(db, session_id)
            lap_rows, lap_truncated = self._lap_rows(db, session_id)
            rows, truncated = self._corner_rows(db, session_id)
        laps = [self._lap(row) for row in lap_rows]
        lap_by_id = {lap.id: lap for lap in laps}
        records: list[CornerRecord] = []
        labels: dict[str, str] = {}
        ordinals: dict[str, int] = {}
        for row in rows:
            lap = lap_by_id.get(str(row["candidate_lap_id"]))
            if lap is None:
                continue
            labels[str(row["segment_key"])] = str(row["label"])
            segment_key = str(row["segment_key"])
            ordinals[segment_key] = min(
                int(row["ordinal"]), ordinals.get(segment_key, int(row["ordinal"]))
            )
            try:
                metrics = json.loads(str(row["metrics_json"] or "{}"))
            except json.JSONDecodeError:
                metrics = {}
            records.append(
                CornerRecord(
                    lap.car_id,
                    str(row["segment_key"]),
                    self._candidate_segment_time(metrics),
                    lap_id=lap.id,
                    coverage=min(lap.coverage, float(row["coverage_ratio"] or 0.0)),
                    valid=lap.valid,
                    context_ok=not (lap.pit_context or lap.flag_context),
                )
            )
        matrix = build_corner_matrix(
            records,
            min_coverage=self.min_coverage,
            min_cars_per_segment=self.min_cars_per_segment,
        )
        has_values = bool(np.any(matrix.valid_mask))
        ordered_drivers = sorted(
            matrix.drivers, key=lambda item: self._driver_sort_key(item, laps)
        )
        row_by_driver = {
            driver_id: index for index, driver_id in enumerate(matrix.drivers)
        }
        row_order = np.asarray(
            [row_by_driver[item] for item in ordered_drivers], dtype=np.int64
        )
        ordered_segments = sorted(
            matrix.segment_ids, key=lambda item: (ordinals.get(item, 2**31), item)
        )
        column_by_segment = {
            segment_id: index
            for index, segment_id in enumerate(matrix.segment_ids)
        }
        column_order = np.asarray(
            [column_by_segment[item] for item in ordered_segments], dtype=np.int64
        )
        ordered_values = np.ix_(row_order, column_order)
        return {
            "schema_version": 1,
            "session_id": session_id,
            "availability": "derived" if has_values else "unavailable",
            "reason": (
                None
                if has_values
                else (
                    "No persisted comparison contains an absolute candidate "
                    "segment time with adequate field coverage. Relative deltas "
                    "are not treated as interchangeable segment times."
                )
            ),
            "drivers": [
                self._driver_ref(car_id, laps) for car_id in ordered_drivers
            ],
            "segments": [
                {"segment_id": item, "label": labels.get(item, item)}
                for item in ordered_segments
            ],
            "median_time_s": _matrix_values(matrix.median_time_s[ordered_values]),
            "delta_to_field_median_s": _matrix_values(
                matrix.delta_to_field_median_s[ordered_values]
            ),
            "performance_percentile": _matrix_values(
                matrix.performance_percentile[ordered_values]
            ),
            "rank": _matrix_values(matrix.rank[ordered_values]),
            "valid_mask": matrix.valid_mask[ordered_values].tolist(),
            "field_median_s": _vector_values(matrix.field_median_s[column_order]),
            "field_mad_s": _vector_values(matrix.field_mad_s[column_order]),
            "n_by_segment": matrix.n_by_segment[column_order].tolist(),
            "sample_count": matrix.sample_count[ordered_values].tolist(),
            "coverage_by_driver": {
                car_id: matrix.coverage_by_driver[car_id]
                for car_id in ordered_drivers
            },
            "source_rows": len(rows),
            "truncated": truncated or lap_truncated,
        }

    async def corners(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._corners_sync, session_id)

    def _stored_position(self, lap: _StoredLap) -> int | None:
        if self.trace_store is None or not lap.trace_manifest_id:
            return None
        try:
            trace = self.trace_store.read_range(
                lap.trace_manifest_id,
                fields=("position",),
                sample_group="lap_data",
            )
            series = trace.series["position"]
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            TraceFormatError,
            TraceStoreError,
        ):
            return None
        values = np.asarray(series.values, dtype=np.float64)
        available = np.asarray(series.available, dtype=bool)
        usable = np.flatnonzero(
            available & np.isfinite(values) & (values >= 1) & (values <= 24)
        )
        return round(float(values[usable[-1]])) if usable.size else None

    def _positions_sync(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._session_row(db, session_id)
            cars = self._car_rows(db, session_id)[:96]
            rows, truncated = self._lap_rows(db, session_id)
        laps = [self._lap(row) for row in rows]
        by_car: dict[str, list[dict[str, Any]]] = defaultdict(list)
        events: list[dict[str, Any]] = []
        for lap in laps:
            position = lap.legacy_position or self._stored_position(lap)
            if position is not None:
                by_car[lap.car_id].append(
                    {
                        "lap_number": lap.lap_number,
                        "position": position,
                        "availability": "observed",
                        "context_mask": int(self._context_mask(lap)),
                    }
                )
            if lap.pit_context:
                events.append(
                    {
                        "type": "pit_context",
                        "car_id": lap.car_id,
                        "lap_number": lap.lap_number,
                        "availability": "observed",
                    }
                )
        series = []
        available_cars = 0
        for car in cars:
            card = self._car_card(car)
            points = by_car[card["car_id"]]
            if points:
                available_cars += 1
            series.append(
                {
                    **card,
                    "availability": "observed" if points else "unavailable",
                    "reason": (
                        None
                        if points
                        else "No persisted lap-end position samples exist for this car."
                    ),
                    "n": len(points),
                    "points": points,
                }
            )
        return {
            "schema_version": 1,
            "session_id": session_id,
            "availability": "observed" if available_cars else "unavailable",
            "reason": (
                None
                if available_cars
                else (
                    "No valid lap-end positions were found in saved lap-data "
                    "traces or legacy lap rows."
                )
            ),
            "series": series,
            "events": events,
            "cars_with_data": available_cars,
            "cars_observed": len(cars),
            "context_schema": self.context_schema(),
            "truncated": truncated,
        }

    async def positions(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._positions_sync, session_id)

    def _pace_slope(self, laps: list[_StoredLap]) -> tuple[float | None, int]:
        clean = [
            lap
            for lap in laps
            if lap.valid
            and not lap.pit_context
            and not lap.flag_context
            and lap.lap_time_ms is not None
            and lap.coverage >= self.min_coverage
        ]
        if len(clean) < 3:
            return None, len(clean)
        x = np.asarray([lap.lap_number for lap in clean], dtype=np.float64)
        y = np.asarray([lap.lap_time_ms / 1000.0 for lap in clean], dtype=np.float64)
        slope = float(np.polyfit(x, y, 1)[0])
        return (slope if math.isfinite(slope) else None), len(clean)

    def _stints_for_laps(self, laps: list[_StoredLap]) -> list[dict[str, Any]]:
        ordered = sorted(laps, key=lambda item: item.lap_number)
        groups: list[list[_StoredLap]] = []
        for lap in ordered:
            if lap.compound is None:
                continue
            start_new = not groups
            if groups:
                previous = groups[-1][-1]
                start_new = (
                    lap.lap_number != previous.lap_number + 1
                    or lap.compound != previous.compound
                    or (
                        lap.tyre_age_laps is not None
                        and previous.tyre_age_laps is not None
                        and lap.tyre_age_laps < previous.tyre_age_laps
                    )
                )
            if start_new:
                groups.append([])
            groups[-1].append(lap)
        result: list[dict[str, Any]] = []
        for ordinal, group in enumerate(groups, 1):
            clean_times = [
                lap.lap_time_ms / 1000.0
                for lap in group
                if lap.valid
                and not lap.pit_context
                and not lap.flag_context
                and lap.lap_time_ms is not None
                and lap.coverage >= self.min_coverage
            ]
            slope, slope_n = self._pace_slope(group)
            result.append(
                {
                    "ordinal": ordinal,
                    "compound": group[0].compound,
                    "start_lap": group[0].lap_number,
                    "end_lap": group[-1].lap_number,
                    "lap_count": len(group),
                    "clean_lap_count": len(clean_times),
                    "median_clean_pace_s": _metric(
                        float(median(clean_times)) if clean_times else None,
                        unit="s",
                        availability="derived" if clean_times else "unavailable",
                        reason=None if clean_times else "No comparable clean lap in stint.",
                        n=len(clean_times),
                    ),
                    "pace_slope_s_per_lap": _metric(
                        slope,
                        unit="s/lap",
                        availability="derived" if slope is not None else "unavailable",
                        reason=(
                            None
                            if slope is not None
                            else "At least three timed clean laps are required."
                        ),
                        n=slope_n,
                    ),
                    "fuel_context": _metric(
                        None,
                        unit="kg",
                        availability="unavailable",
                        reason=(
                            "No validated fuel correction model is applied to "
                            "stored field pace."
                        ),
                    ),
                }
            )
        return result

    def _stints_sync(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._session_row(db, session_id)
            cars = self._car_rows(db, session_id)[:96]
            rows, truncated = self._lap_rows(db, session_id)
        laps = [self._lap(row) for row in rows]
        by_car: dict[str, list[_StoredLap]] = defaultdict(list)
        for lap in laps:
            by_car[lap.car_id].append(lap)
        drivers: list[dict[str, Any]] = []
        available = 0
        for car in cars:
            card = self._car_card(car)
            stints = self._stints_for_laps(by_car[card["car_id"]])
            if stints:
                available += 1
            drivers.append(
                {
                    **card,
                    "availability": "derived" if stints else "unavailable",
                    "reason": (
                        None
                        if stints
                        else "No tyre compound sequence is stored for this car."
                    ),
                    "n": len(stints),
                    "stints": stints,
                }
            )
        return {
            "schema_version": 1,
            "session_id": session_id,
            "availability": "derived" if available else "unavailable",
            "reason": (
                None
                if available
                else "No stored car has enough compound data to derive a stint."
            ),
            "drivers": drivers,
            "cars_with_data": available,
            "cars_observed": len(cars),
            "truncated": truncated,
        }

    async def stints(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._stints_sync, session_id)

    def _driver_corner_profile(
        self, session_id: str, car_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        corners = self._corners_sync(session_id)
        driver_index = next(
            (
                index
                for index, driver in enumerate(corners["drivers"])
                if driver["car_id"] == car_id
            ),
            None,
        )
        items: list[dict[str, Any]] = []
        if driver_index is not None:
            for column, segment in enumerate(corners["segments"]):
                percentile = corners["performance_percentile"][driver_index][column]
                if percentile is None:
                    continue
                items.append(
                    {
                        **segment,
                        "median_time_s": corners["median_time_s"][driver_index][column],
                        "delta_to_field_median_s": corners[
                            "delta_to_field_median_s"
                        ][driver_index][column],
                        "performance_percentile": percentile,
                        "rank": corners["rank"][driver_index][column],
                        "sample_n": corners["sample_count"][driver_index][column],
                        "field_n": corners["n_by_segment"][column],
                    }
                )
        reason = (
            "Driver strengths require persisted absolute segment times for an "
            "adequately observed field benchmark."
        )
        strengths = {
            "availability": "derived" if items else "unavailable",
            "reason": None if items else reason,
            "n": len(items),
            "items": sorted(
                items,
                key=lambda item: (
                    -float(item["performance_percentile"]),
                    float(item["delta_to_field_median_s"] or 0.0),
                ),
            )[:3],
        }
        weaknesses = {
            "availability": "derived" if items else "unavailable",
            "reason": None if items else reason.replace("strengths", "weaknesses"),
            "n": len(items),
            "items": sorted(
                items,
                key=lambda item: (
                    float(item["performance_percentile"]),
                    -float(item["delta_to_field_median_s"] or 0.0),
                ),
            )[:3],
        }
        return strengths, weaknesses, bool(corners["truncated"])

    def _driver_sync(self, session_id: str, car_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._session_row(db, session_id)
            car = db.execute(
                "SELECT * FROM session_cars WHERE id=? AND session_id=?",
                (car_id, session_id),
            ).fetchone()
            if car is None:
                raise DriverNotFoundError(
                    f"Driver {car_id!r} was not observed in session {session_id!r}"
                )
            rows, truncated = self._lap_rows(db, session_id)
        all_laps = [self._lap(row) for row in rows]
        laps = [lap for lap in all_laps if lap.car_id == car_id]
        masks = {lap.id: int(self._context_mask(lap)) for lap in laps}
        clean_times = self._clean_times(laps, masks)
        progression = [
            {
                "lap_id": lap.id,
                "lap_number": lap.lap_number,
                "lap_time_ms": lap.lap_time_ms,
                "valid": lap.valid,
                "coverage": lap.coverage,
                "quality": lap.quality,
                "compound": lap.compound,
                "tyre_age_laps": lap.tyre_age_laps,
                "weather_class": lap.weather_class,
                "context_mask": masks[lap.id],
                "context": self._context_labels(masks[lap.id]),
            }
            for lap in laps
        ]
        strengths, weaknesses, corner_truncated = self._driver_corner_profile(
            session_id, car_id
        )
        return {
            "schema_version": 1,
            "session_id": session_id,
            "driver": self._car_card(car),
            "laps": progression,
            "summary": {
                "lap_count": len(laps),
                "comparable_lap_count": len(clean_times),
                "best_lap_ms": min(clean_times) if clean_times else None,
                "median_clean_pace_ms": (
                    float(median(clean_times)) if clean_times else None
                ),
            },
            "stints": self._stints_for_laps(laps),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "context_schema": self.context_schema(),
            "truncated": truncated or corner_truncated,
        }

    async def driver(self, session_id: str, car_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._driver_sync, session_id, car_id)


__all__ = [
    "CONTEXT_MASK_LABELS",
    "CONTEXT_MASK_VERSION",
    "ContextMask",
    "DriverNotFoundError",
    "FieldAnalysisService",
    "FieldServiceError",
    "SessionNotFoundError",
]
