"""Bridge completed legacy laps into the 4.2 typed trace store.

The current application already assembles a dependable player trace in
``StateStore`` and persists it in the legacy ``laps`` table.  This service is
an additive write path: a failure here never invalidates that proven save, and
the catalog can always backfill or reprocess the legacy JSON later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .catalog import lap_id, session_car_id, session_id
from .database import PitWallDatabase
from .telemetry.alignment import clean_distance_axis
from .trace_store import TraceManifest, TraceStore


@dataclass(frozen=True, slots=True)
class TraceArchiveResult:
    lap_id: str
    manifest_id: str | None
    sample_count: int
    coverage_ratio: float
    state: str
    reason: str | None = None


_FIELD_METADATA: dict[str, dict[str, str]] = {
    "distance_m": {"unit": "m", "provenance": "observed", "dtype": "float64"},
    "time_s": {"unit": "s", "provenance": "observed", "dtype": "float64"},
    "speed_mps": {"unit": "m/s", "provenance": "derived", "dtype": "float32"},
    "speed_kph": {"unit": "km/h", "provenance": "observed", "dtype": "float32"},
    "throttle": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "brake": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "steering": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "gear": {"unit": "gear", "provenance": "observed", "dtype": "int8"},
    "lateral_g": {"unit": "g", "provenance": "observed", "dtype": "float32"},
    "longitudinal_g": {"unit": "g", "provenance": "observed", "dtype": "float32"},
    "world_x": {"unit": "m", "provenance": "observed", "dtype": "float32"},
    "world_y": {"unit": "m", "provenance": "observed", "dtype": "float32"},
    "world_z": {"unit": "m", "provenance": "observed", "dtype": "float32"},
    "forward_x": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "forward_z": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "slip_fl": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "slip_fr": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "slip_rl": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
    "slip_rr": {"unit": "ratio", "provenance": "observed", "dtype": "float32"},
}


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalise_legacy_trace(
    trace: list[dict[str, Any]],
    *,
    track_length_m: float | None,
) -> list[dict[str, int | float | None]]:
    if not trace:
        return []
    distances = [_finite(point.get("d")) for point in trace]
    times = [_finite(point.get("t")) for point in trace]
    distance_array = np.asarray(
        [np.nan if value is None else value for value in distances], dtype=np.float64
    )
    time_array = np.asarray(
        [np.nan if value is None else value for value in times], dtype=np.float64
    )
    cleaned = clean_distance_axis(
        distance_array,
        {"time_s": time_array},
        valid_mask=np.isfinite(time_array),
        track_length_m=(
            float(track_length_m)
            if track_length_m is not None and float(track_length_m) > 0
            else None
        ),
        epoch_policy="last",
    )
    rows: list[dict[str, int | float | None]] = []
    for source_index, distance in zip(
        cleaned.source_indices.tolist(), cleaned.distance_m.tolist()
    ):
        point = trace[int(source_index)]
        speed_kph = _finite(point.get("speed"))
        slip = point.get("slip")
        slip_values = list(slip) if isinstance(slip, (list, tuple)) else []
        row: dict[str, int | float | None] = {
            "distance_m": float(distance),
            "time_s": _finite(point.get("t")),
            "speed_mps": None if speed_kph is None else speed_kph / 3.6,
            "speed_kph": speed_kph,
            "throttle": _finite(point.get("throttle")),
            "brake": _finite(point.get("brake")),
            "steering": _finite(point.get("steer")),
            "gear": (
                int(point["gear"])
                if point.get("gear") is not None
                else None
            ),
            "lateral_g": _finite(point.get("lat_g")),
            "longitudinal_g": _finite(point.get("long_g")),
            "world_x": _finite(point.get("x")),
            "world_y": _finite(point.get("y")),
            "world_z": _finite(point.get("z")),
            "forward_x": _finite(point.get("fx", point.get("forward_x"))),
            "forward_z": _finite(point.get("fz", point.get("forward_z"))),
        }
        for wheel, offset in zip(("fl", "fr", "rl", "rr"), range(4)):
            row[f"slip_{wheel}"] = (
                _finite(slip_values[offset]) if offset < len(slip_values) else None
            )
        rows.append(row)
    return rows


class TraceArchiveService:
    """Persist completed laps with stable IDs and non-blocking file I/O."""

    def __init__(self, database: PitWallDatabase, trace_store: TraceStore) -> None:
        self.database = database
        self.trace_store = trace_store

    @staticmethod
    def identifiers(lap: dict[str, Any]) -> tuple[str, str, str]:
        session_key = session_id(
            int(lap["session_uid"]), int(lap.get("restart_epoch", 0) or 0)
        )
        car_key = session_car_id(
            session_key,
            int(lap.get("player_car_index", 0) or 0),
            int(lap.get("identity_revision", 0) or 0),
        )
        key = lap_id(
            car_key,
            int(lap["lap_num"]),
            int(lap.get("timeline_epoch", 0) or 0),
        )
        return session_key, car_key, key

    async def archive_player_lap(
        self,
        lap: dict[str, Any],
        *,
        recorded_lap_id: str | None = None,
    ) -> TraceArchiveResult:
        session_key, car_key, computed_lap_id = self.identifiers(lap)
        resolved_lap_id = recorded_lap_id or computed_lap_id
        if resolved_lap_id != computed_lap_id:
            return TraceArchiveResult(
                computed_lap_id,
                None,
                0,
                0.0,
                "deferred",
                "catalog lap identity did not match the completed-lap identity",
            )
        rows = normalise_legacy_trace(
            list(lap.get("trace") or []),
            track_length_m=_finite(lap.get("track_length_m")),
        )
        if len(rows) < 2:
            return TraceArchiveResult(
                computed_lap_id,
                None,
                len(rows),
                0.0,
                "unavailable",
                "fewer than two valid monotonic distance samples",
            )
        fingerprint = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest_id = f"tm_{fingerprint[:24]}"

        def write() -> TraceManifest:
            try:
                return self.trace_store.load_manifest(manifest_id)
            except FileNotFoundError:
                pass
            self.trace_store.append_samples(
                car_key,
                "telemetry",
                rows,
                axis_field="distance_m",
                axis_unit="m",
                field_metadata=_FIELD_METADATA,
            )
            return self.trace_store.finalize_lap(
                computed_lap_id,
                session_car_id=car_key,
                manifest_id=manifest_id,
            )

        manifest = await asyncio.to_thread(write)
        await self.database.catalog.register_trace_manifest(session_key, manifest)
        coverage = min(
            (
                value
                for chunk in manifest.chunks
                for value in chunk.coverage.values()
            ),
            default=0.0,
        )
        return TraceArchiveResult(
            computed_lap_id,
            manifest.id,
            manifest.sample_count,
            float(coverage),
            "ready",
        )


__all__ = [
    "TraceArchiveResult",
    "TraceArchiveService",
    "normalise_legacy_trace",
]
