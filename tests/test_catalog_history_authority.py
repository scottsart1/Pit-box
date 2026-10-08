from copy import deepcopy
import json
import sqlite3

import pytest

from pitwall.catalog import lap_id, session_car_id, session_id
from pitwall.database import PitWallDatabase


def state(**changes):
    return {"session_uid": (1 << 64) - 5, "restart_epoch": 0, "timeline_epoch": 0,
            "player_car_index": 6, "track_id": 12, "track_name": "Singapore",
            "track_length_m": 4928, "packet_format": 2026, "session_type": "Race",
            "mode_profile": "race", "total_laps": 31, **changes}


def observed_lap(number=4, **changes):
    return {**state(), "lap_num": number, "lap_time_ms": 95000, "s1_ms": 30000,
            "s2_ms": 35000, "s3_ms": 30000, "valid": True, "compound": "MEDIUM",
            "tyre_age_start": 2, "tyre_age_end": 3, "fuel_start_kg": 40,
            "fuel_end_kg": 38, "wear_start": [3,4,5,6], "wear_end": [4,5,6,7],
            "setup": {"front_wing": 50}, "weather": "Clear", "track_temp_c": 30,
            "trace": [{"d": 100, "t": 1}, {"d": 200, "t": 2}], **changes}


def history(number=4, total=92000, flags=15):
    return {"lap_num": number, "lap_ms": total, "s1_ms": 30000, "s2_ms": 32000,
            "s3_ms": total-62000, "valid_flags": flags}


def saved(path, key):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM recorded_laps WHERE id=?", (key,)).fetchone())
    return row, json.loads(row["engineering_json"])


@pytest.mark.asyncio
async def test_history_corrects_timing_preserving_frozen_context_and_trace(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    original = observed_lap()
    key = await db.save_lap(original, [])
    await db.catalog.reconcile_player_history(state(), [history()])
    row, context = saved(db.path, key)
    assert row["lap_time_ms"] == 92000
    assert context["s2_ms"] == 32000 and context["valid_flags"] == 15
    assert context["setup"] == original["setup"] and context["wear_end"] == original["wear_end"]
    assert context["compound"] == "MEDIUM" and row["coverage_ratio"] == 0
    assert context["telemetry_timing_mismatch"] is True
    assert "history_timing_mismatch" in context["learning_exclusions"]
    with sqlite3.connect(db.path) as conn:
        trace = json.loads(conn.execute("SELECT trace_json FROM laps").fetchone()[0])
    assert trace == original["trace"]


@pytest.mark.asyncio
async def test_missing_lap_is_timing_only_and_delayed_analysis_cannot_undo_history(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    merged = await db.catalog.reconcile_player_history(state(), [history(5, 94000, 14)])
    key = db.catalog._player_lap_key(merged[0])
    row, context = saved(db.path, key)
    assert row["coverage_ratio"] == 0 and row["valid"] == 0
    assert row["invalid_reason_mask"] & 1
    assert row["fuel_start_kg"] is None and row["tyre_compound"] == "UNKNOWN"
    assert context["context_observed"] is False and context["trace_coverage"] == 0
    assert "setup" not in context and "wear_end" not in context
    late = observed_lap(5)
    frozen = deepcopy(late)
    authoritative = db.catalog.apply_authoritative_player_timing(late)
    assert late == frozen
    assert authoritative["lap_time_ms"] == 94000 and authoritative["valid"] is False
    await db.catalog.record_player_lap(late)
    row, context = saved(db.path, key)
    assert row["lap_time_ms"] == 94000 and row["valid"] == 0
    assert row["invalid_reason_mask"] & 1
    assert context["s3_ms"] == 32000 and context["setup"] == late["setup"]


@pytest.mark.asyncio
async def test_history_identity_isolated_by_uid_restart_car_revision_and_flashback(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    states = [state(), state(session_uid=123), state(restart_epoch=1),
              state(player_car_index=7), state(identity_revision=1)]
    keys = []
    for i, context in enumerate(states):
        key = await db.catalog.record_player_lap({**observed_lap(), **context, "lap_time_ms": 95000+i})
        keys.append(key)
    await db.catalog.reconcile_player_history(states[0], [history()])
    assert saved(db.path, keys[0])[0]["lap_time_ms"] == 92000
    assert [saved(db.path, key)[0]["lap_time_ms"] for key in keys[1:]] == [95001,95002,95003,95004]
    # An abandoned branch must remain invalid; replacement history belongs to
    # a new canonical row, while preceding valid laps keep their earlier epoch.
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE recorded_laps SET valid=0,invalid_reason_mask=2 WHERE id=?", (keys[0],))
    await db.catalog.reconcile_player_history(state(timeline_epoch=1), [history(total=93000)])
    new_key = lap_id(session_car_id(session_id(state()["session_uid"]), 6), 4, 1)
    assert saved(db.path, keys[0])[0]["valid"] == 0
    assert saved(db.path, new_key)[0]["lap_time_ms"] == 93000
    await db.catalog.record_player_lap(observed_lap())
    assert saved(db.path, keys[0])[0]["invalid_reason_mask"] & 2
    assert saved(db.path, keys[0])[0]["valid"] == 0
    assert saved(db.path, new_key)[0]["lap_time_ms"] == 93000
    await db.catalog.record_player_lap(observed_lap(3))
    preceding = db.catalog._player_lap_key(observed_lap(3))
    await db.catalog.reconcile_player_history(state(timeline_epoch=1), [history(3)])
    assert saved(db.path, preceding)[0]["lap_time_ms"] == 92000
    with sqlite3.connect(db.path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM recorded_laps WHERE lap_number=3").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_incomplete_history_ignored_and_partial_trace_coverage_not_promoted(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    key = await db.catalog.record_player_lap(observed_lap(trace_coverage=0))
    assert saved(db.path, key)[0]["coverage_ratio"] == 0
    assert await db.catalog.reconcile_player_history(state(), [{**history(), "s3_ms": 0}]) == []
    assert saved(db.path, key)[0]["lap_time_ms"] == 95000


@pytest.mark.asyncio
async def test_small_timing_refinement_keeps_trace_usable_and_repeated_packet_is_noop(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    key = await db.catalog.record_player_lap(observed_lap(lap_time_ms=92016))
    assert len(await db.catalog.reconcile_player_history(state(), [history()])) == 1
    row, context = saved(db.path, key)
    assert row["lap_time_ms"] == 92000 and row["coverage_ratio"] == 1
    assert not context.get("telemetry_timing_mismatch")
    assert await db.catalog.reconcile_player_history(state(), [history()]) == []


@pytest.mark.asyncio
async def test_complete_history_rewind_isolates_timing_only_laps_and_late_old_packets(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    await db.catalog.reconcile_field_history(state(history_complete=True), [history(1), history(2), history(3)])
    car = session_car_id(session_id(state()["session_uid"]), 6)
    await db.catalog.reconcile_field_history(state(timeline_epoch=1, history_complete=True), [history(1)])
    assert saved(db.path, lap_id(car,1,0))[0]["valid"] == 1
    assert saved(db.path, lap_id(car,2,0))[0]["invalid_reason_mask"] & 2
    assert saved(db.path, lap_id(car,3,0))[0]["invalid_reason_mask"] & 2
    await db.catalog.reconcile_field_history(state(timeline_epoch=1, history_complete=True), [history(1), history(2,93000)])
    assert saved(db.path, lap_id(car,2,1))[0]["lap_time_ms"] == 93000
    # A stale queued epoch0 update cannot resurrect old rows or alter epoch1.
    assert await db.catalog.reconcile_field_history(state(history_complete=True), [history(1),history(2),history(3)]) == []
    assert saved(db.path, lap_id(car,2,1))[0]["lap_time_ms"] == 93000


@pytest.mark.asyncio
async def test_changed_authoritative_lap_gets_new_branch_unchanged_past_keeps_identity(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    await db.catalog.reconcile_field_history(state(), [history(1), history(2)])
    await db.catalog.reconcile_field_history(state(timeline_epoch=1), [history(1), history(2,93000)])
    car = session_car_id(session_id(state()["session_uid"]), 6)
    assert saved(db.path, lap_id(car,1,0))[0]["valid"] == 1
    assert saved(db.path, lap_id(car,2,0))[0]["invalid_reason_mask"] & 2
    assert saved(db.path, lap_id(car,2,1))[0]["lap_time_ms"] == 93000


@pytest.mark.asyncio
async def test_authoritative_validity_updates_mask_in_both_directions_preserving_other_bits(tmp_path):
    db = PitWallDatabase(tmp_path / "laps.sqlite3")
    await db.initialize()
    key = await db.catalog.record_player_lap(observed_lap(valid=False,invalid_reason_mask=9))
    await db.catalog.reconcile_player_history(state(), [history()])
    assert saved(db.path,key)[0]["valid"] == 1
    assert saved(db.path,key)[0]["invalid_reason_mask"] == 8
    await db.catalog.reconcile_player_history(state(), [history(flags=14)])
    assert saved(db.path,key)[0]["valid"] == 0
    assert saved(db.path,key)[0]["invalid_reason_mask"] == 9
    await db.catalog.record_player_lap(observed_lap())
    assert saved(db.path,key)[0]["invalid_reason_mask"] == 9
