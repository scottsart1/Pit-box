import json
import sqlite3

import pytest

from pitwall.catalog import SessionCatalog, lap_id
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


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_worker", [False, True])
async def test_old_field_batch_after_worker_restart_cannot_revalidate_pending_history(tmp_path, reuse_worker):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    traces = TraceStore(tmp_path / "traces")
    batch = fixture_batch(full=True, lap_ms=203881)
    archive = FullFieldArchiveService(db.path, traces)
    await archive.start()
    assert archive.submit_history(context(batch), history())
    await archive.queue.join()
    # The live packet does not identify the rewind lap for every other car.
    assert archive.submit(BranchInvalidation(batch.session, 0, 1, 1, .1, None, (), "test rewind"))
    await archive.stop()
    restarted = archive if reuse_worker else FullFieldArchiveService(db.path, traces)
    before = restarted.snapshot()
    await restarted.start()
    assert restarted.submit(batch)
    assert restarted.submit_history(context(batch), history())
    await restarted.stop()
    with sqlite3.connect(db.path) as connection:
        rows = connection.execute("SELECT valid,trace_manifest_id,engineering_json FROM recorded_laps ORDER BY lap_number").fetchall()
        assert len(rows) == 2
        assert all(row[0] == 0 and row[1] is None for row in rows)
        assert all(json.loads(row[2])["history_revalidation_required_epoch"] == 1 for row in rows)
        assert connection.execute("SELECT COUNT(*) FROM trace_manifests").fetchone()[0] == 0
    assert restarted.snapshot().write_errors == 0
    assert restarted.snapshot().persisted_laps == 0
    assert restarted.snapshot().lap_batches_discarded - before.lap_batches_discarded == 1
    assert restarted.snapshot().history_updates_processed == before.history_updates_processed
    assert restarted.snapshot().history_updates_discarded - before.history_updates_discarded == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_worker", [False, True])
async def test_deleted_session_uid_can_be_recorded_again_without_inheriting_old_branch_barrier(tmp_path, reuse_worker):
    path = tmp_path / "laps.sqlite3"
    db = PitWallDatabase(path)
    await db.initialize()
    batch = fixture_batch(full=True, lap_ms=203881)
    field = {**context(batch), "history_complete": True}
    player = {**field, "history_is_player": True, "player_car_index": 6,
              "drivers": [{"car_idx": 6, "name": "Player", "is_player": True}]}
    await db.reconcile_player_lap_history(player, history())
    archive = FullFieldArchiveService(path, TraceStore(tmp_path / "traces"))
    await archive.start()
    assert archive.submit_history(field, history())
    await archive.queue.join()
    assert archive.submit(BranchInvalidation(batch.session, 0, 3, 1, .1, None, (), "first recording rewind"))
    await archive.stop()
    with db.catalog._connect() as connection:
        assert SessionCatalog.history_replacement_epoch(connection, batch.session.id) == 3
        connection.execute("UPDATE recorded_sessions SET status='complete' WHERE id=?", (batch.session.id,))
    preview = await db.catalog.preview_delete(batch.session.id)
    await db.catalog.delete_session(batch.session.id, preview["confirmation_token"])
    with db.catalog._connect() as connection:
        # Ordering is the audit primary key; equal timestamps do not join two
        # distinct recordings of the same game session UID.
        connection.execute("UPDATE audit_events SET created_at='2026-01-01T00:00:00Z'")
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (batch.session.id,)).fetchone()[0] == 2
        assert SessionCatalog.history_replacement_epoch(connection, batch.session.id) == 0

    # Reopening the database and worker mirrors a fresh process importing the
    # same capture after explicitly deleting the previous recording.
    reopened = db if reuse_worker else PitWallDatabase(path)
    await reopened.initialize()
    assert await reopened.reconcile_player_lap_history(player, history()) == 2
    replay = archive if reuse_worker else FullFieldArchiveService(path, TraceStore(tmp_path / "traces"))
    await replay.start()
    assert replay.submit_history(field, history())
    assert replay.submit(batch)
    await replay.queue.join()
    with reopened.catalog._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM recorded_laps").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM trace_manifests").fetchone()[0] == 1
    assert replay.snapshot().persisted_laps == 1
    assert replay.submit(BranchInvalidation(batch.session, 0, 1, 1, .1, None, (), "new recording rewind"))
    await replay.stop()
    with reopened.catalog._connect() as connection:
        connection.execute("UPDATE audit_events SET created_at='2026-01-01T00:00:00Z'")
        assert SessionCatalog.history_replacement_epoch(connection, batch.session.id) == 1

    restarted = PitWallDatabase(path)
    await restarted.initialize()
    # The new lifecycle still rejects a delayed old-branch legacy write.
    await restarted.save_lap({**player, "lap_num": 9, "lap_time_ms": 90000, "valid": True}, [])
    with restarted.catalog._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM laps WHERE lap_num=9").fetchone()[0] == 0
    # Lap 4 becomes valid again; the already-invalid lap 5 stays invalid.
    assert await restarted.reconcile_player_lap_history({**player, "timeline_epoch": 1}, history()) == 1
    with restarted.catalog._connect() as connection:
        assert connection.execute("SELECT valid FROM laps WHERE lap_num=4").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (batch.session.id,)).fetchone()[0] == 3


@pytest.mark.asyncio
async def test_running_assembler_reused_uid_has_new_restart_scope_after_deletion(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    archive = FullFieldArchiveService(db.path, TraceStore(tmp_path / "traces"))
    batches = []
    def emit(batch):
        batches.append(batch)
        archive.submit(batch)
    assembler = SessionAssembler(batch_sink=emit)

    def record(uid):
        def stamp(frame):
            return EventStamp(uid, frame, frame, frame / 10, frame * 100000000, frame * 100000000)
        assembler.consume(SessionEvent(stamp(1), track_id=12, session_type=16, packet_format=2026, player_car_index=6,
                                       metadata={"track_length_m": 100}))
        assembler.consume(ParticipantEvent(stamp(2), 0, {"name": "Opponent", "driver_id": 58}))
        for frame, distance in enumerate((0, 50, 100), 3):
            assembler.consume(SampleEvent(stamp(frame), 0, 4, "telemetry",
                {"lap_distance_m": distance, "speed_mps": 50.0}, units={"lap_distance_m": "m", "speed_mps": "m/s"}))
        assembler.consume(LapEvent(stamp(8), 0, 4, 5, 90000, True))
        return batches[-1]

    await archive.start()
    try:
        original = record(123)
        await archive.queue.join()
        assert archive.submit(BranchInvalidation(original.session, 0, 1, 1, .1, None,
                                                (original.batch_id,), "old recording rewind"))
        await archive._invalidation_queue.join()
        await db.catalog.finalize_session(original.session.id)
        preview = await db.catalog.preview_delete(original.session.id)
        await db.catalog.delete_session(original.session.id, preview["confirmation_token"])
        # The public assembler lifecycle changes the restart identity when a
        # previously seen UID returns, even though this archive keeps running.
        assembler.consume(SessionEvent(EventStamp(456, 1, 1, .1, 1, 1), track_id=0))
        replacement = record(123)
        assert replacement.session.restart_epoch == 1
        assert replacement.session.id != original.session.id
        assert archive.submit(original)  # Delayed old generation stays blocked.
        await archive.queue.join()
        with db.catalog._connect() as connection:
            rows = connection.execute("SELECT c.session_id,l.lap_time_ms,l.trace_manifest_id FROM recorded_laps l "
                                      "JOIN session_cars c ON c.id=l.session_car_id").fetchall()
            assert len(rows) == 1 and rows[0]["session_id"] == replacement.session.id
            assert rows[0]["lap_time_ms"] == 90000 and rows[0]["trace_manifest_id"]
        assert archive.snapshot().persisted_laps == 2
        assert archive.snapshot().lap_batches_discarded == 1
    finally:
        await archive.stop()
