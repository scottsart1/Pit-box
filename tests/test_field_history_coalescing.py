import asyncio
import json
import sqlite3

import pytest

from pitwall.database import PitWallDatabase
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.trace_store import TraceStore


def context(car=0, **changes):
    return {"session_uid": 123, "restart_epoch": 0, "timeline_epoch": 0, "session_generation": 0,
            "player_car_index": car, "identity_revision": 0, "history_is_player": False,
            "history_complete": True, "current_lap": 2, "track_id": 12, "track_name": "Singapore",
            "session_type": "Race", "mode_profile": "race", "packet_format": 2026,
            "drivers": [{"car_idx": car, "name": f"Driver {car}"}], **changes}


def timing(number=1, **changes):
    return {"lap_num": number, "lap_ms": 90000, "s1_ms": 30000, "s2_ms": 30000,
            "s3_ms": 30000, "valid_flags": 15, **changes}


async def archive_at(tmp_path, queue_size=64):
    database = PitWallDatabase(tmp_path / "laps.sqlite3")
    await database.initialize()
    archive = FullFieldArchiveService(database.path, TraceStore(tmp_path / "traces"), queue_size=queue_size)
    await archive.start()
    return archive


def assert_settled(archive):
    snapshot = archive.snapshot()
    assert snapshot.queue_depth == snapshot.queue_drops == snapshot.write_errors == 0
    assert snapshot.submitted == (snapshot.persisted_laps + snapshot.invalidations
                                  + snapshot.history_updates_processed + snapshot.history_updates_discarded
                                  + snapshot.lap_batches_discarded)


@pytest.mark.asyncio
async def test_repeating_all_22_cars_retains_each_completed_lap_without_queue_flood(tmp_path):
    archive = await archive_at(tmp_path)
    archive.set_trace_scope({6})  # Timing must still cover everyone.
    try:
        for frame in range(50):
            for car in range(22):
                assert not archive.submit_history(context(car, history_complete=False), [timing(lap_ms=0, s1_ms=frame, s2_ms=0, s3_ms=0)])
        assert archive.snapshot().submitted == 0
        for frame in range(50):
            for car in range(22):
                accepted = archive.submit_history(context(car, current_lap=1),
                    [timing(lap_ms=0, s1_ms=frame, s2_ms=0, s3_ms=0)])
                assert accepted is (frame == 0)
        await archive.queue.join()
        for frame in range(100):
            for car in range(22):
                accepted = archive.submit_history(
                    context(car, frame_identifier=frame, session_time_s=frame / 10,
                            history_identity={"car_index": car, "last_frame": frame}),
                    [timing(), timing(2, lap_ms=0, s1_ms=frame, s2_ms=0, s3_ms=0)],
                )
                assert accepted is (frame == 0)
        await archive.queue.join()
        # Identical completed history remains coalesced after successful writes.
        for car in range(22):
            assert not archive.submit_history(context(car, history_identity={"car_index": car, "last_frame": 101}), [timing()])
            assert archive.submit_history(context(car, current_lap=3), [timing(), timing(2)])
        await archive.queue.join()
    finally:
        await archive.stop()
    assert_settled(archive)
    snapshot = archive.snapshot()
    assert snapshot.history_empty_skipped == 1100
    assert snapshot.history_updates_processed == snapshot.submitted == 66
    assert snapshot.history_updates_coalesced == 3278
    assert snapshot.queue_high_water <= 22
    with sqlite3.connect(archive.database_path) as db:
        rows = db.execute("SELECT c.car_index,l.lap_number,l.lap_time_ms,l.trace_manifest_id FROM recorded_laps l "
                          "JOIN session_cars c ON c.id=l.session_car_id ORDER BY c.car_index,l.lap_number").fetchall()
        assert rows == [(car, lap, 90000, None) for car in range(22) for lap in (1, 2)]


@pytest.mark.asyncio
async def test_sector_and_validity_corrections_with_same_total_time_are_not_coalesced(tmp_path):
    archive = await archive_at(tmp_path)
    try:
        for row in [timing(), timing(s1_ms=30001, s2_ms=29999),
                    timing(s1_ms=30001, s2_ms=29999, valid_flags=14), timing(valid_flags=15)]:
            assert archive.submit_history(context(), [row])
            await archive.queue.join()
            with sqlite3.connect(archive.database_path) as db:
                saved = db.execute("SELECT valid,invalid_reason_mask,engineering_json FROM recorded_laps").fetchone()
            frozen = json.loads(saved[2])
            assert frozen["s1_ms"] == row["s1_ms"] and frozen["s2_ms"] == row["s2_ms"]
            assert saved[0] == bool(row["valid_flags"] & 1)
            assert bool(saved[1] & 1) == (not bool(row["valid_flags"] & 1))
    finally:
        await archive.stop()
    assert_settled(archive)
    assert archive.snapshot().history_updates_processed == 4


@pytest.mark.asyncio
async def test_completing_A_does_not_erase_pending_B_and_return_to_A_is_preserved(tmp_path, monkeypatch):
    archive = await archive_at(tmp_path)
    original = archive.catalog.reconcile_field_history
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def gated(ctx, history):
        nonlocal calls
        index = calls
        calls += 1
        if index < 2:
            started[index].set()
            await release[index].wait()
        return await original(ctx, history)

    monkeypatch.setattr(archive.catalog, "reconcile_field_history", gated)
    a, b = [timing()], [timing(valid_flags=14)]
    try:
        assert archive.submit_history(context(), a)
        await asyncio.wait_for(started[0].wait(), 2)
        assert archive.submit_history(context(), b)
        release[0].set()
        await asyncio.wait_for(started[1].wait(), 2)
        assert not archive.submit_history(context(), b)
        assert archive.submit_history(context(), a)  # A succeeded, but B is still in flight.
        release[1].set()
        await archive.queue.join()
    finally:
        for event in release:
            event.set()
        await archive.stop()
    assert_settled(archive)
    assert calls == 3
    with sqlite3.connect(archive.database_path) as db:
        assert db.execute("SELECT valid,invalid_reason_mask FROM recorded_laps").fetchone() == (1, 0)


@pytest.mark.asyncio
async def test_identical_history_retries_after_write_failure(tmp_path, monkeypatch):
    archive = await archive_at(tmp_path)
    original = archive.catalog.reconcile_field_history
    calls = 0

    async def fail_once(ctx, history):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected history write failure")
        return await original(ctx, history)

    monkeypatch.setattr(archive.catalog, "reconcile_field_history", fail_once)
    try:
        assert archive.submit_history(context(), [timing()])
        await archive.queue.join()
        assert archive.snapshot().write_errors == 1
        assert archive.submit_history(context(), [timing()])
        await archive.queue.join()
        assert not archive.submit_history(context(), [timing()])
    finally:
        await archive.stop()
    assert calls == 2 and archive.snapshot().history_updates_processed == 1
    assert archive.snapshot().write_errors == 1  # Retain the diagnostic after recovery.
    with sqlite3.connect(archive.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM recorded_laps").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_queue_rejection_does_not_cache_an_unwritten_history(tmp_path):
    archive = await archive_at(tmp_path, queue_size=1)
    try:
        assert archive.submit_history(context(0), [timing()])
        assert not archive.submit_history(context(1), [timing()])
        await archive.queue.join()
        assert archive.submit_history(context(1), [timing()])
        await archive.queue.join()
    finally:
        await archive.stop()
    assert archive.snapshot().queue_drops == 1
    assert archive.snapshot().history_updates_processed == 2


@pytest.mark.asyncio
async def test_complete_empty_history_is_reconsidered_on_new_timeline_and_keeps_unknown_rows(tmp_path, monkeypatch):
    archive = await archive_at(tmp_path)
    original = archive.catalog.reconcile_field_history
    observed = []

    async def record(ctx, history):
        observed.append((ctx["timeline_epoch"], history))
        return await original(ctx, history)

    monkeypatch.setattr(archive.catalog, "reconcile_field_history", record)
    invalid_positive = [timing(s3_ms=0)]
    try:
        assert archive.submit_history(context(), [])
        await archive.queue.join()
        assert not archive.submit_history(context(), [])
        assert archive.submit_history(context(timeline_epoch=1), invalid_positive)
        await archive.queue.join()
        # Unknown completed timing must not share the empty-history cache: an
        # actual complete empty replacement still needs to reach reconciliation.
        assert archive.submit_history(context(timeline_epoch=1), [])
        assert not archive.submit_history(context(timeline_epoch=1), [])
        await archive.queue.join()
    finally:
        await archive.stop()
    assert observed == [(0, []), (1, invalid_positive), (1, [])]
    assert_settled(archive)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"session_uid": 124}, {"restart_epoch": 1}, {"timeline_epoch": 1},
    {"player_car_index": 1}, {"identity_revision": 1}, {"session_generation": 1},
    {"history_complete": False}, {"current_lap": 3}, {"mode_profile": "qualifying"},
])
async def test_new_identity_epoch_or_history_scope_does_not_reuse_success_cache(tmp_path, changes):
    archive = await archive_at(tmp_path)
    try:
        assert archive.submit_history(context(), [timing()])
        await archive.queue.join()
        assert archive.submit_history(context(**changes), [timing()])
        await archive.queue.join()
    finally:
        await archive.stop()
    assert_settled(archive)
    assert archive.snapshot().history_updates_processed == 2
