from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.trace_store import TraceStore


@pytest.mark.asyncio
async def test_lap_trace_merges_motion_geometry_onto_telemetry_distance_axis(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.db")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    session_id = "ses_multigroup"
    car_id = "car_multigroup"
    lap_id = "lap_multigroup"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO recorded_sessions(
                id, game_session_uid, restart_epoch, track_id,
                track_layout_signature, session_type, status, packet_format,
                capture_mode, created_at, updated_at
            ) VALUES (?, '9001', 0, 7, 'multigroup-layout', 'Practice',
                      'complete', 2026, 'balanced', ?, ?)
            """,
            (session_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO session_cars(
                id, session_id, car_index, identity_revision, display_name,
                anonymized_name, is_ai, is_player, identity_confidence
            ) VALUES (?, ?, 0, 0, 'Player', 'Driver 01', 0, 1, 1.0)
            """,
            (car_id, session_id),
        )
        connection.execute(
            """
            INSERT INTO recorded_laps(
                id, session_car_id, lap_number, timeline_epoch, lap_time_ms,
                valid, pit_context, flag_context, coverage_ratio,
                quality_score, created_at
            ) VALUES (?, ?, 1, 0, 90000, 1, 0, 0, 1.0, 0.99, ?)
            """,
            (lap_id, car_id, now),
        )

    telemetry_distance = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
    trace_store.append_samples(
        car_id,
        "telemetry",
        {
            "lap_distance_m": telemetry_distance,
            "session_time_s": [distance / 50.0 for distance in telemetry_distance],
            "speed_mps": [50.0] * len(telemetry_distance),
            "brake": [0.0, 0.0, 0.2, 0.8, 0.3, 0.0, 0.0, 0.0],
            "throttle": [1.0, 1.0, 0.5, 0.0, 0.2, 0.8, 1.0, 1.0],
            "steering": [0.0, 0.0, 0.1, 0.3, 0.4, 0.2, 0.0, 0.0],
            "gear": [6, 6, 5, 4, 4, 5, 6, 6],
        },
        axis_field="lap_distance_m",
        axis_unit="m",
    )
    # The 14 -> 30 m hole is intentionally too large for the 8 m geometry
    # bridge rule. Exact observations and short-gap interpolation remain valid.
    motion_distance = [0.0, 7.0, 14.0, 30.0, 35.0]
    trace_store.append_samples(
        car_id,
        "motion",
        {
            "lap_distance_m": motion_distance,
            "world_x": [distance * 2.0 for distance in motion_distance],
            "world_z": [100.0 + distance for distance in motion_distance],
        },
        axis_field="lap_distance_m",
        axis_unit="m",
    )
    manifest = trace_store.finalize_lap(
        lap_id,
        session_car_id=car_id,
        manifest_id="tm_multigroup",
    )
    await database.catalog.register_trace_manifest(session_id, manifest)

    result = await ComparisonService(database.path, trace_store).get_lap_trace(
        lap_id,
        fields=[
            "speed",
            "brake",
            "throttle",
            "steering",
            "gear",
            "world_x",
            "world_z",
        ],
        max_points=100,
    )

    assert result["axis"]["values"] == telemetry_distance
    assert result["series"]["brake"]["values"] == pytest.approx(
        [0.0, 0.0, 0.2, 0.8, 0.3, 0.0, 0.0, 0.0]
    )
    assert result["series"]["brake"]["availability"] == "observed"
    assert result["series"]["world_x"]["values"] == [
        0.0,
        10.0,
        20.0,
        None,
        None,
        None,
        60.0,
        70.0,
    ]
    assert result["series"]["world_z"]["values"] == [
        100.0,
        105.0,
        110.0,
        None,
        None,
        None,
        130.0,
        135.0,
    ]
    assert result["series"]["world_x"]["availability"] == "derived"
    assert result["series"]["world_x"]["coverage"] == pytest.approx(5 / 8)

