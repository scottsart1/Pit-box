from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.api.field import create_field_router
from pitwall.catalog import lap_id, session_car_id, session_id
from pitwall.database import PitWallDatabase
from pitwall.field_service import FieldAnalysisService
from pitwall.session_analysis import (
    AnalysisCar,
    AnalysisLap,
    SessionAnalysisService,
    box_stats,
    build_session_analysis,
    driver_code,
    normalise_compound,
    race_segments,
    stints_for,
    suspended_laps,
)

_DEFAULT = object()
RACE = {"id": "ses_test", "session_type": "Race 2", "raw_session_type_id": 16, "track_id": 12}
PRACTICE = {"id": "ses_fp", "session_type": "Practice 1", "raw_session_type_id": 1, "track_id": 12}


def car(index: int, name: str, *, player: bool = False, team: int | None = None) -> AnalysisCar:
    return AnalysisCar(index, (f"car_{index}",), name, team, index + 1, player)


def lap(
    index: int,
    number: int,
    ms: int | None,
    *,
    valid: bool = True,
    pit: bool = False,
    flag: bool = False,
    compound: str | None = "MEDIUM",
    age: int | None | object = _DEFAULT,
    sectors: tuple[int, int, int] | None = None,
) -> AnalysisLap:
    s1, s2, s3 = sectors or (None, None, None)
    return AnalysisLap(
        car_index=index,
        lap_id=f"lap_{index}_{number}",
        lap_number=number,
        lap_time_ms=ms,
        s1_ms=s1,
        s2_ms=s2,
        s3_ms=s3,
        valid=valid,
        pit_context=pit,
        flag_context=flag,
        compound=compound,
        tyre_age_laps=number - 1 if age is _DEFAULT else age,  # type: ignore[arg-type]
    )


def by_code(result: dict) -> dict[str, dict]:
    return {row["code"]: row for row in result["drivers"]}


def lap_row(result: dict, index: int, number: int) -> dict:
    return next(row for row in result["laps"] if row["car_index"] == index and row["lap_number"] == number)


# ---------------------------------------------------------------- primitives


def test_box_stats_match_numpy_quartiles_and_tukey_whiskers() -> None:
    values = [91_000, 91_400, 91_550, 91_700, 92_100, 92_300, 95_800]
    stats = box_stats(values)
    assert stats is not None
    assert stats["q1_ms"] == pytest.approx(np.percentile(values, 25))
    assert stats["median_ms"] == pytest.approx(np.percentile(values, 50))
    assert stats["q3_ms"] == pytest.approx(np.percentile(values, 75))
    iqr = stats["q3_ms"] - stats["q1_ms"]
    assert stats["whisker_high_ms"] <= stats["q3_ms"] + 1.5 * iqr
    assert stats["whisker_high_ms"] == 92_300  # 95.8 s is outside the fence
    assert stats["whisker_low_ms"] == 91_000
    assert box_stats([]) is None


def test_driver_codes_are_three_letters_unique_and_accent_safe() -> None:
    taken: set[str] = set()
    assert driver_code("NORRIS", taken) == "NOR"
    assert driver_code("PÉREZ", taken) == "PER"
    assert driver_code("Kimi Antonelli", taken) == "ANT"
    first = driver_code("Car 1", taken)
    second = driver_code("Car 2", taken)
    assert len({first, second, "NOR", "PER", "ANT"}) == 5
    duplicate = driver_code("NORRIS", taken)
    assert duplicate != "NOR" and len(duplicate) == 3


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("SOFT", "SOFT"),
        ("medium", "MEDIUM"),
        ("HARD", "HARD"),
        ("INTER", "INTER"),
        ("INTERMEDIATE", "INTER"),
        ("WET", "WET"),
        ("FULL_WET", "WET"),
        ("UNKNOWN", None),
        ("", None),
        (None, None),
    ],
)
def test_compounds_fold_into_five_families(stored: str | None, expected: str | None) -> None:
    assert normalise_compound(stored) == expected


# ------------------------------------------------------- red-flag race order


def red_flag_race() -> tuple[list[AnalysisCar], list[AnalysisLap]]:
    """Three cars, laps 4-5 suspended, standing restart on lap 6.

    The summed lap times before the restart favour car 1, but the race
    restarts from the grid, so only laps 6-10 decide the order: car 0 wins.
    """
    cars = [car(0, "LECLERC", player=True, team=476), car(1, "NORRIS", team=484), car(2, "PIASTRI", team=484)]
    pace = {0: 92_000, 1: 92_100, 2: 92_600}
    suspended_times = {0: (205_000, 231_000), 1: (190_000, 225_000), 2: (199_000, 228_000)}
    laps: list[AnalysisLap] = []
    for index in range(3):
        for number in range(1, 11):
            if number in (4, 5):
                ms = suspended_times[index][number - 4]
                laps.append(lap(index, number, ms, flag=number == 4))
            elif number in (1, 6):
                laps.append(lap(index, number, pace[index] + 2_500))
            else:
                laps.append(lap(index, number, pace[index]))
    return cars, laps


def test_suspended_laps_are_recognised_from_field_pace_not_safety_cars() -> None:
    _cars, laps = red_flag_race()
    by_car: dict[int, list[AnalysisLap]] = {}
    for item in laps:
        by_car.setdefault(item.car_index, []).append(item)
    assert suspended_laps(by_car) == [4, 5]
    # A safety car at 1.5 times race pace is not a suspension.
    sc_laps = [lap(i, n, 138_000 if n == 4 else 92_000) for i in range(3) for n in range(1, 8)]
    sc_by_car: dict[int, list[AnalysisLap]] = {}
    for item in sc_laps:
        sc_by_car.setdefault(item.car_index, []).append(item)
    assert suspended_laps(sc_by_car) == []
    assert race_segments(10, [4, 5]) == [(1, 3), (6, 10)]
    assert race_segments(5, []) == [(1, 5)]


def test_red_flag_restart_resets_order_and_gaps_from_the_grid() -> None:
    cars, laps = red_flag_race()
    result = build_session_analysis(RACE, cars, laps)
    drivers = by_code(result)
    assert result["suspended_laps"] == [4, 5]
    assert result["segments"] == [{"first_lap": 1, "last_lap": 3}, {"first_lap": 6, "last_lap": 10}]
    assert [drivers[code]["finish_position"] for code in ("LEC", "NOR", "PIA")] == [1, 2, 3]
    assert result["winner_car_index"] == 0
    # Gaps come from laps 6-10 only: 5 laps x 100 ms and 600 ms per lap.
    assert drivers["NOR"]["gap_to_winner_ms"] == 500
    assert drivers["PIA"]["gap_to_winner_ms"] == 3_000
    # A suspended race has no single race clock in lap times.
    assert drivers["LEC"]["race_time_ms"] is None
    # Order holds while the race is stopped; no gap is claimed there.
    assert lap_row(result, 1, 4)["position"] == lap_row(result, 1, 3)["position"]
    assert lap_row(result, 1, 4)["gap_to_leader_ms"] is None
    assert lap_row(result, 1, 6)["gap_to_leader_ms"] == 100
    assert lap_row(result, 0, 4)["suspended"] is True


def test_red_flag_pace_excludes_lap_one_restart_and_suspended_laps() -> None:
    cars, laps = red_flag_race()
    result = build_session_analysis(RACE, cars, laps)
    pace = next(row for row in result["race_pace"] if row["car_index"] == 0)
    assert pace["lap_numbers"] == [2, 3, 7, 8, 9, 10]
    assert pace["excluded"] == {"lap_one": 1, "suspended": 2, "restart": 1}
    assert pace["median_ms"] == 92_000
    assert lap_row(result, 0, 6)["pace_excluded"] == "restart"


# ------------------------------------------------- uninterrupted race basics


def safety_car_race() -> tuple[list[AnalysisCar], list[AnalysisLap]]:
    cars = [car(0, "VERSTAPPEN"), car(1, "HAMILTON", player=True), car(2, "ALONSO"), car(3, "OCON")]
    laps: list[AnalysisLap] = []
    for index, pace in enumerate((90_000, 90_300, 90_900, 92_500)):
        for number in range(1, 9):
            ms = pace + (3_000 if number == 1 else 0)
            neutral = number in (4, 5)
            if neutral:
                ms = 125_000 + index * 50
            pit = index == 1 and number in (4, 5)
            compound = "MEDIUM" if index != 1 or number < 5 else "HARD"
            age = number - 1 if index != 1 or number < 5 else number - 5
            laps.append(lap(index, number, ms, flag=neutral, pit=pit, compound=compound, age=age))
    return cars, laps


def test_uninterrupted_race_has_race_time_positions_and_intervals() -> None:
    cars, laps = safety_car_race()
    result = build_session_analysis(RACE, cars, laps)
    drivers = by_code(result)
    assert result["suspended_laps"] == []
    assert result["neutralised_laps"] == [4, 5]
    assert drivers["VER"]["finish_position"] == 1
    assert drivers["VER"]["race_time_ms"] == sum(row.lap_time_ms for row in laps if row.car_index == 0)
    expected_gap = sum(row.lap_time_ms for row in laps if row.car_index == 1) - drivers["VER"]["race_time_ms"]
    assert drivers["HAM"]["gap_to_winner_ms"] == expected_gap
    second = lap_row(result, 1, 8)
    assert second["position"] == 2
    assert second["interval_ms"] == second["gap_to_leader_ms"]


def test_pit_stop_under_safety_car_has_no_loss_estimate_and_changes_compound() -> None:
    cars, laps = safety_car_race()
    result = build_session_analysis(RACE, cars, laps)
    stops = [row for row in result["pit_stops"] if row["car_index"] == 1]
    assert len(stops) == 1
    stop = stops[0]
    assert stop["kind"] == "pit_stop"
    assert stop["laps"] == [4, 5]
    assert (stop["from_compound"], stop["to_compound"]) == ("MEDIUM", "HARD")
    assert stop["under_neutralisation"] is True
    assert stop["estimated_loss_ms"] is None
    stints = next(row["stints"] for row in result["stints"] if row["car_index"] == 1)
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints] == [("MEDIUM", 1, 4), ("HARD", 5, 8)]


def test_green_flag_pit_loss_is_in_and_out_lap_minus_median_pace() -> None:
    cars = [car(0, "SAINZ"), car(1, "ALBON")]
    laps = []
    for index in range(2):
        for number in range(1, 11):
            pit = index == 0 and number in (5, 6)
            ms = 95_000 if number == 1 else 91_000
            if pit:
                ms += 11_000
            laps.append(lap(index, number, ms, pit=pit, compound="SOFT" if number < 6 or index else "HARD",
                            age=number - 1 if number < 6 or index else number - 6))
    result = build_session_analysis(RACE, cars, laps)
    stop = next(row for row in result["pit_stops"] if row["car_index"] == 0)
    assert stop["estimated_loss_ms"] == 22_000
    assert by_code(result)["SAI"]["pit_stops"] == 1


def test_lapped_and_retired_cars_are_classified_correctly() -> None:
    cars = [car(0, "RUSSELL"), car(1, "STROLL"), car(2, "GASLY")]
    laps = []
    for number in range(1, 11):
        laps.append(lap(0, number, 90_000))
    for number in range(1, 10):  # one lap down, crosses after the winner
        laps.append(lap(1, number, 101_000))
    for number in range(1, 4):  # retired after lap 3
        laps.append(lap(2, number, 91_000))
    laps.append(lap(2, 4, None))  # the unfinished lap of the retirement
    result = build_session_analysis(RACE, cars, laps)
    drivers = by_code(result)
    assert drivers["RUS"]["status"] == "finished"
    assert drivers["STR"]["status"] == "lapped"
    assert drivers["STR"]["laps_down"] == 1
    assert drivers["STR"]["finish_position"] == 2
    assert drivers["GAS"]["status"] == "retired"
    assert drivers["GAS"]["finish_position"] == 3
    assert drivers["GAS"]["record_gap_from_lap"] is None
    assert result["warnings"] == []


def test_a_hole_in_the_record_is_reported_not_papered_over() -> None:
    cars = [car(0, "LAWSON"), car(1, "HADJAR")]
    laps = [lap(0, number, 92_000) for number in range(1, 9)]
    laps += [lap(1, number, None if number == 4 else 91_000) for number in range(1, 9)]
    result = build_session_analysis(RACE, cars, laps)
    drivers = by_code(result)
    assert drivers["HAD"]["status"] == "incomplete_record"
    assert drivers["HAD"]["finish_position"] is None
    assert drivers["HAD"]["record_gap_from_lap"] == 4
    assert lap_row(result, 1, 6)["position"] is None
    assert any("HADJAR" in warning and "lap 4" in warning for warning in result["warnings"])
    assert drivers["LAW"]["finish_position"] == 1


def test_fastest_lap_and_ideal_lap_use_valid_laps_only() -> None:
    cars = [car(0, "PIASTRI"), car(1, "NORRIS")]
    laps = [
        lap(0, 1, 93_000, sectors=(30_000, 38_000, 25_000)),
        lap(0, 2, 90_500, valid=False, sectors=(29_000, 37_000, 24_500)),
        lap(0, 3, 91_800, sectors=(29_500, 37_500, 24_800)),
        lap(1, 1, 92_900, sectors=(29_900, 38_100, 24_900)),
        lap(1, 2, 91_600, sectors=(29_400, 37_700, 24_500)),
        lap(1, 3, 91_900, sectors=(29_300, 37_900, 24_700)),
    ]
    result = build_session_analysis(RACE, cars, laps)
    fastest = result["fastest_laps"][0]
    assert (fastest["car_index"], fastest["lap_time_ms"], fastest["delta_to_fastest_ms"]) == (1, 91_600, 0)
    piastri = by_code(result)["PIA"]
    assert piastri["best_lap_ms"] == 91_800  # the invalid 90.5 does not count
    assert piastri["ideal_lap_ms"] == 29_500 + 37_500 + 24_800
    assert result["sector_bests"]["s1"] == {"car_index": 1, "ms": 29_300, "lap_number": 3}
    assert result["ideal_lap"]["best_sectors_ms"] == 29_300 + 37_500 + 24_500


def test_outlier_hiding_is_optional() -> None:
    cars = [car(0, "BEARMAN")]
    laps = [lap(0, n, 92_000 + (8_000 if n == 6 else 0)) for n in range(1, 10)]
    hidden = build_session_analysis(RACE, cars, laps)["race_pace"][0]
    shown = build_session_analysis(RACE, cars, laps, hide_outliers=False)["race_pace"][0]
    assert hidden["excluded"].get("outlier") == 1 and 6 not in hidden["lap_numbers"]
    assert 6 in shown["lap_numbers"] and shown["outliers"] == [{"lap_number": 6, "lap_time_ms": 100_000}]


# --------------------------------------------------------------- tyre stints


def test_stints_split_at_out_lap_one_lap_pit_runs_and_red_flag_changes() -> None:
    laps = [lap(0, n, 92_000, compound="MEDIUM", age=n - 1) for n in range(1, 6)]
    laps += [lap(0, 6, 92_000, compound="MEDIUM", age=0)]  # tyre age reset, no pit: red flag
    laps += [lap(0, n, 92_000, compound="MEDIUM", age=n - 6) for n in range(7, 10)]
    laps += [lap(0, 10, 104_000, pit=True, compound="MEDIUM", age=4)]  # one-lap pit run
    laps += [lap(0, n, 91_000, compound="HARD", age=n - 11) for n in range(11, 15)]
    stints = stints_for(laps)
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints] == [
        ("MEDIUM", 1, 5),
        ("MEDIUM", 6, 10),
        ("HARD", 11, 14),
    ]
    result = build_session_analysis(RACE, [car(0, "OCON")], laps)
    kinds = [(row["kind"], row["lap_number"]) for row in result["pit_stops"]]
    assert kinds == [("tyre_change", 5), ("pit_stop", 10)]


def test_unknown_tyre_laps_are_their_own_stint_and_never_a_tyre_change() -> None:
    laps = [lap(0, n, 92_000, compound="MEDIUM", age=n - 1) for n in range(1, 3)]
    laps += [lap(0, n, 92_000, compound=None, age=None) for n in range(3, 12)]
    stints = stints_for(laps)
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints] == [("MEDIUM", 1, 2), (None, 3, 11)]
    result = build_session_analysis(RACE, [car(0, "BOTTAS")], laps)
    assert result["pit_stops"] == []
    # A single unrecorded lap inside a stint does not split it.
    short = [lap(0, n, 92_000, compound=None if n == 4 else "SOFT", age=None if n == 4 else n - 1) for n in range(1, 8)]
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints_for(short)] == [("SOFT", 1, 7)]


# ------------------------------------------------------------- non-race / edge


def test_practice_sessions_have_no_race_order_and_drop_invalid_laps_from_pace() -> None:
    cars = [car(0, "ALONSO"), car(1, "STROLL")]
    laps = [lap(0, n, 93_000 - n * 100, valid=n != 3) for n in range(1, 7)]
    laps += [lap(1, n, 94_000) for n in range(1, 4)]
    result = build_session_analysis(PRACTICE, cars, laps)
    assert result["session"]["is_race"] is False
    assert result["winner_car_index"] is None
    assert result["segments"] == []
    assert all(row["finish_position"] is None for row in result["drivers"])
    assert all(row["position"] is None for row in result["laps"])
    alonso = next(row for row in result["race_pace"] if row["car_index"] == 0)
    assert alonso["excluded"] == {"invalid": 1}
    assert 1 in alonso["lap_numbers"]  # lap 1 is not special outside a race


def test_empty_session_reports_without_failing() -> None:
    result = build_session_analysis(RACE, [], [])
    assert result["drivers"] == [] and result["laps"] == []
    assert "No laps are stored for this session." in result["warnings"]


# ------------------------------------------------------- database and route


async def _stored_race(path: Path) -> str:
    database = PitWallDatabase(path)
    await database.initialize()
    key = session_id(770077)
    cars = [session_car_id(key, index) for index in range(2)]
    revised = session_car_id(key, 1, 1)
    with sqlite3.connect(path) as db:
        db.execute(
            """
            INSERT INTO recorded_sessions(
                id, game_session_uid, restart_epoch, track_id, track_layout_signature,
                session_type, raw_session_type_id, started_at, ended_at, status,
                packet_format, capture_mode, created_at, updated_at
            ) VALUES (?, '770077', 0, 12, 'f1:2026:12:4928', 'Race 2', 16,
                      '2026-10-08T13:00:00Z', '2026-10-08T14:00:00Z', 'complete',
                      2026, 'balanced', '2026-10-08T13:00:00Z', '2026-10-08T14:00:00Z')
            """,
            (key,),
        )
        rows = [(cars[0], 0, 0, "LECLERC", 1), (cars[1], 1, 0, "Car 2", 0), (revised, 1, 1, "NORRIS", 0)]
        for car_key, index, revision, name, player in rows:
            db.execute(
                """
                INSERT INTO session_cars(
                    id, session_id, car_index, identity_revision, display_name,
                    anonymized_name, team_id, is_ai, is_player, identity_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, 484, ?, ?, 1.0)
                """,
                (car_key, key, index, revision, name, name, 1 - player, player),
            )

        def insert(car_key: str, number: int, epoch: int, ms: int | None, *, sectors=(30_000, 37_000, 25_000)) -> None:
            engineering = {"s1_ms": sectors[0], "s2_ms": sectors[1], "s3_ms": sectors[2],
                           "track_name": "Singapore", "total_laps": 5}
            db.execute(
                """
                INSERT INTO recorded_laps(
                    id, session_car_id, lap_number, timeline_epoch, lap_time_ms, valid,
                    tyre_compound, tyre_age_laps, pit_context, flag_context,
                    coverage_ratio, quality_score, created_at, engineering_json
                ) VALUES (?, ?, ?, ?, ?, 1, 'MEDIUM', ?, 0, 0, 1.0, 1.0, ?, ?)
                """,
                (lap_id(car_key, number, epoch), car_key, number, epoch, ms, number - 1,
                 f"2026-10-08T13:{number:02d}:{epoch:02d}Z", json.dumps(engineering)),
            )

        for number in range(1, 6):
            insert(cars[0], number, 0, 92_000)
        # A flashback re-ran lap 3: the abandoned epoch-0 lap must not count.
        insert(cars[0], 3, 1, 91_500)
        for number in range(1, 3):
            insert(cars[1], number, 0, 92_400)
        for number in range(3, 6):
            insert(revised, number, 0, 92_400)  # same car after an identity revision
        db.commit()
    return key


@pytest.mark.asyncio
async def test_service_reads_newest_epoch_and_merges_identity_revisions(tmp_path: Path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    key = await _stored_race(path)
    result = await SessionAnalysisService(path).analysis(key)
    drivers = by_code(result)
    assert result["session"]["track_name"] == "Singapore"
    assert result["session"]["total_laps"] == 5
    assert set(drivers) == {"LEC", "NOR"}
    assert drivers["NOR"]["laps_completed"] == 5
    assert len(drivers["NOR"]["car_ids"]) == 2
    assert drivers["LEC"]["race_time_ms"] == 4 * 92_000 + 91_500
    assert drivers["LEC"]["is_player"] is True
    assert drivers["LEC"]["finish_position"] == 1


@pytest.mark.asyncio
async def test_analysis_route_serves_results_and_404s_unknown_sessions(tmp_path: Path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    key = await _stored_race(path)
    app = FastAPI()
    app.include_router(create_field_router(FieldAnalysisService(path), SessionAnalysisService(path)))
    client = TestClient(app)
    response = client.get(f"/api/v1/sessions/{key}/analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["winner_car_index"] == 0
    assert client.get(f"/api/v1/sessions/{key}/analysis?hide_outliers=false").json()["hide_outliers"] is False
    missing = client.get("/api/v1/sessions/ses_missing/analysis")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"


def test_field_router_without_analysis_service_keeps_existing_routes(tmp_path: Path) -> None:
    router = create_field_router(FieldAnalysisService(tmp_path / "unused.sqlite3"))
    paths = {route.path for route in router.routes}
    assert "/api/v1/sessions/{session_id}/field" in paths
    assert "/api/v1/sessions/{session_id}/analysis" not in paths
    with_analysis = create_field_router(
        FieldAnalysisService(tmp_path / "unused.sqlite3"), SessionAnalysisService(tmp_path / "unused.sqlite3")
    )
    assert "/api/v1/sessions/{session_id}/analysis" in {route.path for route in with_analysis.routes}
