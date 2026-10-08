"""Synthetic packet-11 timings repair gaps without inventing missing telemetry.

The 31-lap fixture is offset from the source shape (+123 ms lap and sector 1);
private capture values remain outside the repository for the device replay gate."""
import copy
import json
from pathlib import Path

import pytest
from f1.packets import PacketSessionHistoryData

from pitwall.catalog import session_id
from pitwall.udp import F1DatagramProtocol

TIMINGS = json.loads((Path(__file__).parent / "fixtures/red_flag_history_timings.json").read_text())
UID = (1 << 63) + 143


def packet(rows=TIMINGS, car=0):
    result = PacketSessionHistoryData()
    result.header.player_car_index = 0
    result.header.session_uid = UID
    result.car_idx = car
    result.num_laps = len(rows)
    for index, row in enumerate(rows):
        entry = result.lap_history_data[index]
        entry.lap_time_in_ms = row["lap_time_ms"]
        entry.lap_valid_bit_flags = row["valid_flags"]
        for number in (1, 2, 3):
            value = row[f"sector{number}_ms"]
            setattr(entry, f"sector{number}_time_minutes_part", value // 60000)
            setattr(entry, f"sector{number}_time_ms_part", value % 60000)
    return result


async def prepare(stack):
    store, database, *_ = stack
    await store.update(session_uid=UID, player_car_index=0, track_id=12,
                       track_name="Singapore", session_type="Race", mode_profile="race", current_lap=6)
    return store, database, F1DatagramProtocol(store, on_player_lap_history=database.reconcile_player_lap_history)


def observed_lap(number, duration):
    return {"session_uid": UID, "track_id": 12, "track_name": "Singapore", "session_type": "Race",
            "mode_profile": "race", "lap_num": number, "lap_time_ms": duration, "valid": True,
            "compound": "MEDIUM", "trace": [{"t": 0, "d": 1, "speed": 90}],
            "setup": {"front_wing": 31}, "wear_start": [10] * 4, "wear_end": [12] * 4}


@pytest.mark.asyncio
async def test_history_packet_repairs_all_31_laps_and_preserves_observations(stack):
    store, database, protocol = await prepare(stack)
    wrong = observed_lap(4, 231191)
    await database.save_lap(wrong, [])
    await database.save_lap(observed_lap(2, 91788), [])
    await store.update(completed_laps=[copy.deepcopy(wrong)])
    await protocol.handle_PacketSessionHistoryData(packet())
    saved = {row["lap_num"]: row for row in await database.recent_laps(12, 100)}
    assert set(saved) == set(range(1, 32))
    for expected in TIMINGS:
        row = saved[expected["lap_num"]]
        assert row["lap_time_ms"] == expected["lap_time_ms"]
        assert [row[f"s{i}_ms"] for i in (1, 2, 3)] == [expected[f"sector{i}_ms"] for i in (1, 2, 3)]
        assert bool(row["valid"]) == bool(expected["valid_flags"] & 1)
    assert json.loads(saved[4]["trace_json"]) == wrong["trace"]
    assert json.loads(saved[4]["setup_json"]) == wrong["setup"]
    assert "history_timing_mismatch" in json.loads(saved[4]["learning_exclusions_json"])
    assert json.loads(saved[5]["trace_json"]) == []
    assert saved[5]["fuel_end_kg"] is None
    assert "missing_telemetry" in json.loads(saved[5]["learning_exclusions_json"])
    assert "history_timing_mismatch" not in json.loads(saved[2]["learning_exclusions_json"])
    canonical = await database.catalog.list_laps(session_id(UID))
    assert len(canonical) == 31
    assert sorted(row["lap_time_ms"] for row in canonical) == sorted(row["lap_time_ms"] for row in TIMINGS)
    state = await store.snapshot_analysis()
    assert len(state["completed_laps"]) == 31
    assert next(row for row in state["completed_laps"] if row["lap_num"] == 4)["trace_incomplete"]
    # A stale analysis write must retain the corrected time and all sectors.
    await database.save_lap(observed_lap(4, 231191), [])
    saved = {row["lap_num"]: row for row in await database.recent_laps(12, 100)}
    assert saved[4]["lap_time_ms"] == 205263
    assert [saved[4][f"s{i}_ms"] for i in (1, 2, 3)] == [41934, 98289, 65039]


@pytest.mark.asyncio
async def test_legacy_repair_retries_after_canonical_write_succeeded(stack, monkeypatch):
    _, database, protocol = await prepare(stack)
    original = database._reconcile_player_history_sync
    calls = 0

    def fail_once(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated interrupted compatibility write")
        return original(*args)

    monkeypatch.setattr(database, "_reconcile_player_history_sync", fail_once)
    with pytest.raises(OSError):
        await protocol.handle_PacketSessionHistoryData(packet())
    assert len(await database.catalog.list_laps(session_id(UID))) == 31
    assert not await database.recent_laps(12, 100)
    await protocol.handle_PacketSessionHistoryData(packet())
    assert len(await database.recent_laps(12, 100)) == 31


@pytest.mark.asyncio
async def test_identical_history_coalesces_only_successful_exact_scope(stack, monkeypatch):
    store, database, protocol = await prepare(stack)
    original = database._reconcile_player_history_sync
    calls = 0

    def count(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(database, "_reconcile_player_history_sync", count)
    await protocol.handle_PacketSessionHistoryData(packet(TIMINGS[:1]))
    await protocol.handle_PacketSessionHistoryData(packet(TIMINGS[:1]))
    assert calls == 1
    revised = copy.deepcopy(TIMINGS[:1])
    revised[0]["valid_flags"] = 14
    await protocol.handle_PacketSessionHistoryData(packet(revised))
    assert calls == 2 and not (await database.recent_laps(12, 100))[0]["valid"]
    await store.update(restart_epoch=1)
    await protocol.handle_PacketSessionHistoryData(packet(revised))
    assert calls == 3


@pytest.mark.asyncio
async def test_history_keeps_invalid_flags_ignores_incomplete_or_inconsistent_timing(stack):
    store, database, protocol = await prepare(stack)
    rows = copy.deepcopy(TIMINGS[:3])
    rows[0]["valid_flags"] = 14
    rows[1]["sector3_ms"] = 0
    rows[2]["lap_time_ms"] += 10000
    await protocol.handle_PacketSessionHistoryData(packet(rows))
    stored = await database.recent_laps(12, 100)
    assert len(stored) == 1 and stored[0]["lap_num"] == 1
    assert not stored[0]["valid"]
    assert (await store.snapshot_analysis())["completed_laps"][0]["valid_flags"] == 14


@pytest.mark.asyncio
async def test_other_car_history_never_backfills_player_rows(stack):
    _, database, protocol = await prepare(stack)
    await protocol.handle_PacketSessionHistoryData(packet(car=1))
    assert not await database.recent_laps(12, 100)


@pytest.mark.asyncio
async def test_telemetry_gap_does_not_assign_lap5_time_to_lap4(stack):
    store, _, _ = await prepare(stack)
    await store.update(current_lap=4, traces=[{"d": 100, "t": 1, "speed": 90}], track_length_m=5000)
    completed = await store.transition_lap(6, 231191, False, 1, 0, 0)
    assert completed["lap_num"] == 4 and completed["lap_time_ms"] == 0
    assert completed["trace_incomplete"] and completed["trace_coverage"] == 0
    assert "telemetry_gap" in completed["learning_exclusions"]


@pytest.mark.asyncio
async def test_history_before_lap_transition_keeps_one_authoritative_observed_summary(stack):
    store, _, protocol = await prepare(stack)
    await store.update(current_lap=1, traces=[{"d": 0, "t": 0, "speed": 90},
                                            {"d": 4990, "t": 94, "speed": 90}], track_length_m=5000)
    rows = copy.deepcopy(TIMINGS[:1])
    rows[0]["valid_flags"] = 14
    await protocol.handle_PacketSessionHistoryData(packet(rows))
    completed = await store.transition_lap(2, rows[0]["lap_time_ms"] + 16, False, 1, 0, 0)
    summaries = (await store.snapshot_analysis())["completed_laps"]
    assert len(summaries) == 1
    assert summaries[0]["lap_time_ms"] == rows[0]["lap_time_ms"]
    assert summaries[0]["timing_source"] == "session_history" and not summaries[0]["valid"]
    assert completed["trace"] and summaries[0]["trace_coverage"] > 0.99
    assert "missing_telemetry" not in summaries[0]["learning_exclusions"]


@pytest.mark.asyncio
async def test_history_before_skipped_lap_transition_keeps_chronological_summaries(stack):
    store, _, protocol = await prepare(stack)
    await store.update(current_lap=4, traces=[{"d": 100, "t": 1, "speed": 90}], track_length_m=5000)
    await protocol.handle_PacketSessionHistoryData(packet(TIMINGS[:5]))
    await store.transition_lap(6, TIMINGS[4]["lap_time_ms"], False, 1, 0, 0)
    summaries = (await store.snapshot_analysis())["completed_laps"]
    assert [row["lap_num"] for row in summaries] == [1, 2, 3, 4, 5]
    assert [row["lap_time_ms"] for row in summaries] == [row["lap_time_ms"] for row in TIMINGS[:5]]
    assert summaries[3]["trace_incomplete"] and summaries[3]["trace_coverage"] == 0


@pytest.mark.asyncio
async def test_old_epoch_history_and_analysis_cannot_replace_current_legacy_lap(stack):
    store, database, protocol = await prepare(stack)
    await protocol.handle_PacketSessionHistoryData(packet(TIMINGS[:1]))
    previous = await store.snapshot_analysis()
    await store.update(restart_epoch=1)
    changed = copy.deepcopy(TIMINGS[:1])
    changed[0]["lap_time_ms"] += 1000
    changed[0]["sector3_ms"] += 1000
    await protocol.handle_PacketSessionHistoryData(packet(changed))
    await database.reconcile_player_lap_history(previous, [{"lap_num": 1, "lap_ms": 94461,
        "s1_ms": 30117, "s2_ms": 38867, "s3_ms": 25476, "valid_flags": 15}])
    await database.save_lap(observed_lap(1, 94461), [])
    await database.save_lap(observed_lap(2, 91788), [])
    assert len(await database.recent_laps(12, 100)) == 1
    assert (await database.recent_laps(12, 100))[0]["lap_time_ms"] == 95461
    assert (await database.catalog.list_laps(session_id(UID, 0)))[0]["lap_time_ms"] == 94461
    assert (await database.catalog.list_laps(session_id(UID, 1)))[0]["lap_time_ms"] == 95461
