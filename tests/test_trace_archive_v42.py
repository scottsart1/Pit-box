from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
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


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_change", [
    {"session_uid": 102}, {"lap_num": 2}, {"player_car_index": 7},
    {"restart_epoch": 1}, {"timeline_epoch": 1},
], ids=["session", "lap", "driver", "restart", "flashback"])
async def test_identical_samples_keep_distinct_lap_ownership(tmp_path, identity_change):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    service = TraceArchiveService(database, TraceStore(tmp_path / "traces"))
    first = {
        "session_uid": 101, "track_id": 11, "track_name": "Monza",
        "track_length_m": 5793, "session_type": "Race", "lap_num": 1,
        "player_car_index": 0, "restart_epoch": 0, "timeline_epoch": 0,
        "lap_time_ms": 82_350, "valid": True, "compound": "MEDIUM",
        "trace": [{"d": 10.0, "t": 1.0, "speed": 100},
                  {"d": 20.0, "t": 2.0, "speed": 110}],
    }
    second = {**first, **identity_change}
    outcomes = []
    for lap in (first, second):
        await database.upsert_session(lap)
        key = await database.save_lap(lap, [])
        outcome = await service.archive_player_lap(lap, recorded_lap_id=key)
        assert outcome.state == "ready"
        outcomes.append(outcome)
    assert outcomes[0].lap_id != outcomes[1].lap_id
    assert outcomes[0].manifest_id != outcomes[1].manifest_id
    for lap, outcome in zip((first, second), outcomes):
        session_key, car_key, lap_key = service.identifiers(lap)
        manifest = service.trace_store.load_manifest(outcome.manifest_id)
        assert (manifest.lap_id, manifest.session_car_id) == (lap_key, car_key)
        with closing(sqlite3.connect(database.path)) as db:
            row = db.execute("""SELECT tm.session_id, tm.session_car_id, tm.lap_id
                                FROM recorded_laps l JOIN trace_manifests tm
                                ON tm.id=l.trace_manifest_id WHERE l.id=?""", (lap_key,)).fetchone()
            assert row == (session_key, car_key, lap_key)
    repeated = await service.archive_player_lap(second, recorded_lap_id=outcomes[1].lap_id)
    assert repeated.manifest_id == outcomes[1].manifest_id
    assert len(list((tmp_path / "traces/manifests").glob("*.json"))) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_owner", ["session", "manifest_id"])
async def test_catalog_rejects_cross_lap_manifest_registration(tmp_path, wrong_owner):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    service = TraceArchiveService(database, TraceStore(tmp_path / "traces"))
    laps = [{"session_uid": uid, "track_id": 11, "track_name": "Monza", "session_type": "Race", "lap_num": 1,
             "lap_time_ms": 80_000, "valid": True,
             "trace": [{"d": 10, "t": 1, "speed": 100}, {"d": 20, "t": 2, "speed": 110}]}
            for uid in (201, 202)]
    for lap in laps:
        await database.upsert_session(lap)
        await database.save_lap(lap, [])
    archived = await service.archive_player_lap(laps[0])
    manifest = service.trace_store.load_manifest(archived.manifest_id)
    session_key, car_key, lap_key = service.identifiers(laps[1])
    if wrong_owner == "manifest_id":
        manifest = replace(manifest, session_car_id=car_key, lap_id=lap_key)
    with pytest.raises(ValueError, match="ownership"):
        await database.catalog.register_trace_manifest(session_key, manifest)
    with closing(sqlite3.connect(database.path)) as db:
        assert db.execute("SELECT trace_manifest_id FROM recorded_laps WHERE id=?", (lap_key,)).fetchone() == (None,)
        assert db.execute("SELECT COUNT(*) FROM trace_manifests").fetchone() == (1,)
