"""One row per player lap in the Engineering report, whatever the game's lap history inserted."""

from __future__ import annotations

import pytest
from test_engineering_5_2 import lap

from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService, build_runs, select_player_laps
from pitwall.engineering_groups import suggest_groups


def stint_lap(n: int, **changes):
    """A lap in one continuous stint: ages and fuel progress without cycling."""
    values = {
        "lap_time_ms": 90000 + n * 10,
        "tyre_age_start": n,
        "tyre_age_end": n + 1,
        "fuel_start_kg": 30 - n,
        "fuel_end_kg": 29 - n,
        "s3_ms": 30000 + n * 10,
        **changes,
    }
    return lap(n, **values)


def history_row(n: int) -> dict:
    return {
        "lap_num": n,
        "lap_ms": 90000 + n * 10,
        "s1_ms": 30000,
        "s2_ms": 30000,
        "s3_ms": 30000 + n * 10,
        "valid_flags": 15,
    }


def history_context(**changes) -> dict:
    base = {
        key: value
        for key, value in lap(1).items()
        if key in {"session_uid", "restart_epoch", "timeline_epoch", "track_id", "track_name", "session_type", "mode_profile"}
    }
    return {**base, "player_car_index": 0, "identity_revision": 0, "history_complete": True, **changes}


def timing_only(n: int, **changes) -> dict:
    """The row the catalog writes for a lap known only from the game's history."""
    return {
        "id": f"history-{n}",
        "session_uid": 52001,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "lap_num": n,
        "lap_time_ms": 90000 + n * 10,
        "valid": True,
        "compound": "UNKNOWN",
        "context_observed": False,
        "trace_coverage": 0.0,
        "learning_exclusions": ["missing_telemetry"],
        "timing_source": "session_history",
        "s1_ms": 30000,
        "s2_ms": 30000,
        "s3_ms": 30000 + n * 10,
        **changes,
    }


@pytest.mark.asyncio
async def test_a_new_identity_revision_does_not_duplicate_laps_or_shatter_the_stint(stack):
    # Six observed laps in one stint, then the game's lap history arrives
    # again under identity revision 1 and the catalog inserts a timing-only
    # copy of every lap beside the observed one.
    _, database, *_ = stack
    for n in range(1, 7):
        await database.save_lap(stint_lap(n), [])
    written = await database.reconcile_player_lap_history(
        history_context(identity_revision=1), [history_row(n) for n in range(1, 7)]
    )
    assert written == 6

    report = await EngineeringService(database).report(session_id(52001))

    assert [item["lap_num"] for item in report["laps"]] == [1, 2, 3, 4, 5, 6]
    assert all(item["context_observed"] is True for item in report["laps"])
    assert all(item["air_temp_c"] == 22 for item in report["laps"])
    assert [item["lap_time_ms"] for item in report["laps"]] == [90010, 90020, 90030, 90040, 90050, 90060]
    assert len(report["runs"]) == 1
    assert report["runs"][0]["summary"]["clean_lap_count"] == 6
    assert report["runs"][0]["summary"]["excluded_laps"] == []


@pytest.mark.asyncio
async def test_a_lap_known_only_from_history_stays_inside_its_stint(stack):
    # Telemetry missed lap 3 entirely; the history packet supplies its time.
    _, database, *_ = stack
    for n in (1, 2, 4, 5, 6):
        await database.save_lap(stint_lap(n), [])
    await database.reconcile_player_lap_history(history_context(), [history_row(n) for n in range(1, 7)])

    report = await EngineeringService(database).report(session_id(52001))

    assert [item["lap_num"] for item in report["laps"]] == [1, 2, 3, 4, 5, 6]
    third = report["laps"][2]
    assert third["context_observed"] is False
    assert third["lap_time_ms"] == 90030
    assert third.get("air_temp_c") is None
    assert len(report["runs"]) == 1
    summary = report["runs"][0]["summary"]
    assert summary["lap_count"] == 6 and summary["clean_lap_count"] == 5
    assert [(item["lap"], item["reasons"]) for item in summary["excluded_laps"]] == [(3, ["missing_telemetry"])]
    # Suggested stints do not treat the unknown compound of that lap as a tyre change.
    groups = suggest_groups(report["laps"], 2)
    assert [len(group["lap_ids"]) for group in groups] == [3, 3]


@pytest.mark.asyncio
async def test_flashback_attempts_stay_separate_while_history_copies_go(stack):
    _, database, *_ = stack
    for n in range(1, 5):
        await database.save_lap(stint_lap(n), [])
    # Lap 4 re-driven after a flashback, on a newer timeline of the same car.
    await database.save_lap(stint_lap(4, timeline_epoch=1, lap_time_ms=91000), [])
    # The game's history, re-sent under a new identity revision on that
    # timeline, inserts timing-only copies of laps 1-4 at epoch 1.
    written = await database.reconcile_player_lap_history(
        history_context(identity_revision=1, timeline_epoch=1),
        [history_row(n) for n in range(1, 4)] + [{**history_row(4), "lap_ms": 91000, "s3_ms": 31000}],
    )
    assert written == 4

    report = await EngineeringService(database).report(session_id(52001))

    assert [(item["lap_num"], item["timeline_epoch"]) for item in report["laps"]] == [(1, 0), (2, 0), (3, 0), (4, 0), (4, 1)]
    assert all(item["context_observed"] is True for item in report["laps"])
    assert [run["lap_range"] for run in report["runs"]] == [[1, 4], [4, 4]]
    assert report["laps"][-1]["lap_time_ms"] == 91000


def test_timing_only_laps_never_open_or_split_a_run():
    laps = [stint_lap(1), stint_lap(2), timing_only(3), stint_lap(4), stint_lap(5)]
    runs = build_runs(laps)
    assert len(runs) == 1
    assert runs[0]["lap_range"] == [1, 5]
    assert runs[0]["compound"] == "MEDIUM"
    assert runs[0]["summary"]["clean_lap_count"] == 4

    # A recording that starts before telemetry: the first observed lap names the run.
    runs = build_runs([timing_only(1), stint_lap(2), stint_lap(3)])
    assert len(runs) == 1
    assert runs[0]["compound"] == "MEDIUM" and runs[0]["setup_id"] != "unknown"
    assert [item["lap_num"] for item in runs[0]["laps"]] == [1, 2, 3]

    # A real tyre change after a timing-only lap still starts a new run.
    runs = build_runs([stint_lap(1), stint_lap(2), timing_only(3), stint_lap(4, tyre_age_start=1, tyre_age_end=2, compound="HARD")])
    assert [run["lap_range"] for run in runs] == [[1, 3], [4, 4]]
    assert runs[1]["compound"] == "HARD"


def test_the_observed_row_wins_and_borrows_a_missing_time():
    observed = stint_lap(2, lap_time_ms=0, s1_ms=0, s2_ms=0, s3_ms=0)
    copy = timing_only(2, identity_revision=1)
    chosen = select_player_laps([copy, observed, stint_lap(1), timing_only(1, identity_revision=1)])
    assert [item["lap_num"] for item in chosen] == [1, 2]
    assert chosen[0]["id"] == "lap-1-1"
    assert chosen[1]["id"] == "lap-1-2" and chosen[1]["air_temp_c"] == 22
    assert chosen[1]["lap_time_ms"] == 90020
    assert [chosen[1][key] for key in ("s1_ms", "s2_ms", "s3_ms")] == [30000, 30000, 30020]
    # Between two timing-only copies the newest revision is the one kept.
    newest = select_player_laps([timing_only(7), timing_only(7, identity_revision=2, id="history-7-r2")])
    assert newest[0]["id"] == "history-7-r2"
