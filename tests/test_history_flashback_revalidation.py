"""Packet-history-only laps need per-car proof after a frame-only FLBK."""

import json
import sqlite3

import pytest

from pitwall.catalog import lap_id, session_car_id, session_id
from pitwall.database import PitWallDatabase
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.identity import SessionKey
from pitwall.session_assembler import BranchInvalidation
from pitwall.trace_store import TraceStore


def context(**changes):
    return {"session_uid": (1 << 64) - 5, "restart_epoch": 0, "timeline_epoch": 0,
            "player_car_index": 6, "identity_revision": 0, "history_complete": True,
            "track_id": 12, "session_type": "Race", **changes}


def history(number):
    return {"lap_num": number, "lap_ms": 92000 + number, "s1_ms": 30000,
            "s2_ms": 32000, "s3_ms": 30000 + number, "valid_flags": 15}


def key(number, **changes):
    scope = context(**changes)
    return lap_id(session_car_id(session_id(scope["session_uid"], scope["restart_epoch"]),
                                 scope["player_car_index"], scope["identity_revision"]), number, 0)


def read(db, number, **changes):
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM recorded_laps WHERE id=?", (key(number, **changes),)).fetchone())
    return row, json.loads(row["engineering_json"])


async def rewind(db, tmp_path):
    archive = FullFieldArchiveService(db.path, TraceStore(tmp_path / "traces"))
    await archive.start()
    assert archive.submit(BranchInvalidation(SessionKey(context()["session_uid"], 0),
                          0, 1, 20, 2.0, None, (), "game FLBK"))
    await archive.stop()
    assert archive.snapshot().write_errors == 0


@pytest.mark.asyncio
async def test_frame_only_flashback_defers_history_then_restores_each_cars_own_prefix(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    scopes = [context(), context(player_car_index=7), context(session_uid=123), context(restart_epoch=1)]
    for scope in scopes:
        await db.catalog.reconcile_field_history(scope, [history(1), history(2), history(3)])
    await rewind(db, tmp_path)
    for car in (6, 7):
        for number in (1, 2, 3):
            row, frozen = read(db, number, player_car_index=car)
            assert row["valid"] == 0 and not row["invalid_reason_mask"] & 2
            assert row["lap_time_ms"] == history(number)["lap_ms"]
            assert frozen["history_revalidation_required_epoch"] == 1
    assert read(db, 3, session_uid=123)[0]["valid"] == 1
    assert read(db, 3, restart_epoch=1)[0]["valid"] == 1

    await db.catalog.reconcile_field_history(context(timeline_epoch=1), [history(1)])
    row, frozen = read(db, 1)
    assert row["valid"] == 1 and row["timeline_epoch"] == 0
    assert "history_revalidation_required_epoch" not in frozen
    assert all(read(db, n)[0]["invalid_reason_mask"] & 2 for n in (2, 3))
    # Another car may have completed more laps before the same flashback.
    assert read(db, 3, player_car_index=7)[0]["invalid_reason_mask"] == 0
    await db.catalog.reconcile_field_history(context(player_car_index=7, timeline_epoch=1),
                                             [history(1), history(2)])
    assert read(db, 2, player_car_index=7)[0]["valid"] == 1
    assert read(db, 3, player_car_index=7)[0]["invalid_reason_mask"] & 2


@pytest.mark.asyncio
async def test_empty_replacement_invalidates_all_future_laps_and_durable_barrier_blocks_old_history(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    await db.reconcile_player_lap_history(context(), [history(1), history(2)])
    await rewind(db, tmp_path)
    # A restarted archive/catalog must honor the persisted barrier even though
    # there has never been a canonical epoch1 lap to use as a guard.
    assert await db.catalog.reconcile_player_history(context(), [history(1), history(2), history(3)]) == []
    await db.catalog.record_player_lap({**context(), "lap_num": 3, "lap_time_ms": 92003, "valid": True})
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recorded_laps").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM laps WHERE valid=1").fetchone()[0] == 0
    await db.reconcile_player_lap_history(context(timeline_epoch=1), [])
    assert all(read(db, n)[0]["invalid_reason_mask"] & 2 for n in (1, 2))
    await db.reconcile_player_lap_history(context(), [history(1), history(2)])
    await db.save_lap({**context(), "lap_num": 2, "lap_time_ms": 92002, "valid": True}, [])
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recorded_laps WHERE valid=1").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM laps WHERE valid=1").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_malformed_positive_time_is_unknown_not_empty_replacement(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    await db.catalog.reconcile_field_history(context(), [history(1), history(2)])
    await rewind(db, tmp_path)
    await db.catalog.reconcile_field_history(context(timeline_epoch=1), [{**history(1), "s3_ms": 0}])
    assert all(not read(db, n)[0]["invalid_reason_mask"] & 2 for n in (1, 2))
    assert all(read(db, n)[1]["history_revalidation_required_epoch"] == 1 for n in (1, 2))
    # A genuine zero-completed-lap packet can contain a current, partial lap.
    await db.catalog.reconcile_field_history(context(timeline_epoch=1), [{**history(1), "lap_ms": 0}])
    assert all(read(db, n)[0]["invalid_reason_mask"] & 2 for n in (1, 2))


@pytest.mark.asyncio
async def test_player_retained_prefix_repairs_legacy_validity_without_changing_identity(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    await db.reconcile_player_lap_history(context(), [history(1), history(2)])
    await rewind(db, tmp_path)
    await db.reconcile_player_lap_history(context(timeline_epoch=1, history_complete=False), [history(1)])
    assert read(db, 1)[0]["valid"] == 0
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM laps WHERE valid=1").fetchone()[0] == 0
    await db.reconcile_player_lap_history(context(timeline_epoch=1), [history(1)])
    assert read(db, 1)[0]["valid"] == 1
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT lap_num,valid FROM laps ORDER BY lap_num").fetchall() == [(1, 1), (2, 0)]
        assert conn.execute("SELECT COUNT(*) FROM recorded_laps").fetchone()[0] == 2
