from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pitwall.database import PitWallDatabase
from pitwall.trace_archive import TraceArchiveService
from pitwall.trace_store import TraceStore


@pytest.mark.asyncio
async def test_completed_lap_dual_writes_typed_trace_and_catalog(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    service = TraceArchiveService(database, trace_store)
    lap = {
        "session_uid": (1 << 63) + 42,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "player_car_index": 7,
        "track_id": 4,
        "track_name": "Test Circuit",
        "track_length_m": 1000,
        "session_type": "Time Trial",
        "mode_profile": "time_trial",
        "lap_num": 3,
        "lap_time_ms": 60_000,
        "valid": True,
        "compound": "SOFT",
        "trace": [
            {"d": 0.0, "t": 0.0, "speed": 180, "throttle": 1.0, "brake": 0.0, "gear": 7},
            {"d": 50.0, "t": 1.0, "speed": 175, "throttle": 0.2, "brake": 0.6, "gear": 6},
            {"d": 100.0, "t": 2.0, "speed": 150, "throttle": 0.0, "brake": 0.8, "gear": 5},
        ],
    }
    await database.upsert_session(lap)
    recorded_lap_id = await database.save_lap(lap, [])
    assert recorded_lap_id is not None

    result = await service.archive_player_lap(lap, recorded_lap_id=recorded_lap_id)
    assert result.state == "ready"
    assert result.sample_count == 3
    assert result.manifest_id
    trace = trace_store.read_range(
        result.manifest_id,
        fields=["time_s", "speed_mps", "brake"],
    )
    assert trace.axis_name == "distance_m"
    assert trace.series["speed_mps"].values.tolist()[0] == pytest.approx(50.0)

    with sqlite3.connect(database.path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT trace_manifest_id FROM recorded_laps WHERE id=?",
            (recorded_lap_id,),
        ).fetchone()
        assert row is not None
        assert row["trace_manifest_id"] == result.manifest_id


@pytest.mark.asyncio
async def test_trace_archive_keeps_final_flashback_epoch_only(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    service = TraceArchiveService(database, TraceStore(tmp_path / "traces"))
    lap = {
        "session_uid": 88,
        "track_id": 1,
        "track_name": "Epoch Track",
        "track_length_m": 500,
        "session_type": "Practice",
        "lap_num": 1,
        "valid": True,
        "trace": [
            {"d": 0.0, "t": 0.0, "speed": 100},
            {"d": 100.0, "t": 2.0, "speed": 120},
            {"d": 20.0, "t": 0.4, "speed": 105},
            {"d": 120.0, "t": 2.4, "speed": 125},
        ],
    }
    await database.upsert_session(lap)
    recorded_lap_id = await database.save_lap(lap, [])
    result = await service.archive_player_lap(lap, recorded_lap_id=recorded_lap_id)
    assert result.sample_count == 2
    sliced = service.trace_store.read_range(result.manifest_id or "")
    assert sliced.axis_values.tolist() == [20.0, 120.0]
