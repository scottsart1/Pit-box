import json
import sqlite3

import pytest

from pitwall.catalog import lap_id
from pitwall.database import PitWallDatabase
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.session_assembler import BranchInvalidation, EventStamp, LapEvent, ParticipantEvent, SampleEvent, SessionAssembler, SessionEvent
from pitwall.trace_store import TraceStore


def fixture_batch(*, full=False, lap_ms=229427):
    batches = []
    assembler = SessionAssembler(batch_sink=batches.append)
    def stamp(frame):
        return EventStamp(123, frame, frame, frame / 10, frame * 100000000, frame * 100000000)
    assembler.consume(SessionEvent(stamp(1), track_id=12, session_type=16, packet_format=2026, player_car_index=6,
                                   metadata={"track_length_m": 100}))
    assembler.consume(ParticipantEvent(stamp(2), 0, {"name": "Opponent", "is_player": False, "driver_id": 58}))
    for frame, distance in enumerate([0, 50, 100] if full else [0, 5, 10], 3):
        assembler.consume(SampleEvent(stamp(frame), 0, 4, "telemetry",
            {"lap_distance_m": distance, "speed_mps": 50.0}, units={"lap_distance_m": "m", "speed_mps": "m/s"}))
    assembler.consume(LapEvent(stamp(8), 0, 4, 6, lap_ms, True))
    return batches[0]


def context(batch):
    return {"session_uid": 123, "restart_epoch": 0, "timeline_epoch": 0,
            "player_car_index": 0, "identity_revision": batch.identity.identity_revision,
            "history_is_player": False, "track_id": 12, "track_name": "Singapore",
            "session_type": "Race 2", "mode_profile": "race", "packet_format": 2026,
            "drivers": [{"car_idx": 0, "name": "Opponent", "driver_id": 58}]}


def history():
    return [{"lap_num": 4, "lap_ms": 203881, "s1_ms": 70123, "s2_ms": 70000, "s3_ms": 63758, "valid_flags": 15},
            {"lap_num": 5, "lap_ms": 229427, "s1_ms": 80123, "s2_ms": 80000, "s3_ms": 69304, "valid_flags": 14}]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_first", [False, True])
async def test_all_field_history_repairs_missed_lap_and_survives_late_archive_batch(tmp_path, history_first):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    archive = FullFieldArchiveService(db.path, TraceStore(tmp_path / "traces"))
    batch = fixture_batch()
    await archive.start()
    try:
        if history_first:
            assert archive.submit_history(context(batch), history())
        assert archive.submit(batch)
        if not history_first:
            assert archive.submit_history(context(batch), history())
        await archive.queue.join()
        # Repeat a previously assembled batch after history: its stale lap time
        # must not win, and a saved trace must stay linked without duplication.
        assert archive.submit(batch)
        await archive.queue.join()
    finally:
        await archive.stop()
    assert archive.snapshot().write_errors == 0
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT l.*,c.is_player FROM recorded_laps l JOIN session_cars c ON c.id=l.session_car_id ORDER BY lap_number").fetchall()
        assert [(r["lap_number"], r["lap_time_ms"], r["valid"], r["is_player"]) for r in rows] == [(4,203881,1,0),(5,229427,0,0)]
        first, missing = rows
        assert first["trace_manifest_id"]
        assert first["coverage_ratio"] == 0
        frozen = json.loads(first["engineering_json"])
        assert frozen["s1_ms"] == 70123 and frozen["s3_ms"] == 63758
        assert frozen["telemetry_timing_mismatch"] is True
        assert missing["trace_manifest_id"] is None and missing["coverage_ratio"] == 0
        assert json.loads(missing["engineering_json"])["context_observed"] is False
        assert conn.execute("SELECT COUNT(*) FROM trace_manifests").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_history_survives_narrow_trace_scope_and_is_frozen_at_enqueue(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    archive = FullFieldArchiveService(db.path, TraceStore(tmp_path / "traces"))
    archive.set_trace_scope({6})
    await archive.start()
    batch = fixture_batch()
    rows = history()
    assert archive.submit(batch) is False
    assert archive.submit_history(context(batch), rows)
    rows[0]["lap_ms"] = 1
    await archive.stop()
    with sqlite3.connect(db.path) as conn:
        row = conn.execute("SELECT lap_time_ms,trace_manifest_id FROM recorded_laps WHERE id=?",
                           (lap_id(batch.identity.id,4,0),)).fetchone()
        assert tuple(row) == (203881, None)
    assert archive.snapshot().history_laps_reconciled == 2
    assert archive.snapshot().history_updates_processed == 1
    assert archive.snapshot().submitted == archive.snapshot().history_updates_processed


@pytest.mark.asyncio
async def test_queued_old_history_cannot_outrun_prioritised_flashback_invalidation(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    archive = FullFieldArchiveService(db.path, TraceStore(tmp_path / "traces"))
    batch = fixture_batch()
    await archive.start()
    assert archive.submit_history(context(batch), history())
    assert archive.submit(BranchInvalidation(batch.session,0,1,1,.1,1,(),"test rewind"))
    await archive.stop()
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recorded_laps").fetchone()[0] == 0
    assert archive.snapshot().write_errors == 0
    assert archive.snapshot().history_updates_discarded == 1
    assert archive.snapshot().submitted == archive.snapshot().history_updates_discarded + archive.snapshot().invalidations


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [True, False])
async def test_history_first_is_enriched_only_by_measured_complete_matching_trace(tmp_path, full):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    archive = FullFieldArchiveService(db.path, TraceStore(tmp_path / "traces"))
    batch = fixture_batch(full=full,lap_ms=203881)
    await archive.start()
    assert archive.submit_history(context(batch),history())
    assert archive.submit(batch)
    await archive.stop()
    with sqlite3.connect(db.path) as conn:
        row = conn.execute("SELECT engineering_json,coverage_ratio FROM recorded_laps WHERE id=?",
                           (lap_id(batch.identity.id,4,0),)).fetchone()
    frozen = json.loads(row[0])
    assert frozen["context_observed"] is full
    assert frozen["trace_incomplete"] is not full
    assert ("missing_telemetry" in frozen["learning_exclusions"]) is not full
    assert row[1] == (1 if full else .1)
    assert frozen["s1_ms"] == 70123 and frozen["lap_time_ms"] == 203881
