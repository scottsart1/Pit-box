"""Session Analysis against the failure modes earlier releases shipped.

Each test names the past incident it guards: flashback branches counted as
raced laps, "absence of evidence" read as "no", safety cars inferred from
local yellows, 500s on odd data, unlabelled derived results and so on.
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.api.field import create_field_router
from pitwall.catalog import _legacy_session_uid, lap_id, session_car_id, session_id
from pitwall.database import PitWallDatabase
from pitwall.field_service import FieldAnalysisService
from pitwall.full_field_archive import FullFieldArchiveService
from pitwall.identity import SessionKey
from pitwall.session_analysis import (
    DRIVER_STATUSES,
    PACE_EXCLUSION_REASONS,
    STOP_KINDS,
    AnalysisCar,
    AnalysisLap,
    SessionAnalysisService,
    build_session_analysis,
    is_race_session,
    race_control_from_events,
    stints_for,
    suspended_laps,
)
from pitwall.session_assembler import BranchInvalidation
from pitwall.trace_store import TraceStore

RACE = {"id": "ses_reg", "session_type": "Race", "raw_session_type_id": 15, "track_id": 12, "status": "complete"}
NO_MESSAGES: list[dict[str, Any]] = []  # race control recorded, nothing happened


def car(index: int, name: str, *, player: bool = False) -> AnalysisCar:
    return AnalysisCar(index, (f"car_{index}",), name, None, index + 1, player)


def lap(
    index: int,
    number: int,
    ms: int | None,
    *,
    compound: str | None = "MEDIUM",
    age: int | None = None,
    pit: bool = False,
    flag: bool = False,
    valid: bool = True,
    observed: bool = True,
    unconfirmed: bool = False,
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
        tyre_age_laps=(number - 1 if age is None and compound else age),
        context_observed=observed,
        unconfirmed=unconfirmed,
    )


def safety_car(first: int, last: int, *, kind: int = 1) -> list[dict[str, Any]]:
    return [
        {"type": "SCAR", "lap": first, "payload": {"safety_car_type": kind, "event_type": 0}},
        {"type": "SCAR", "lap": last, "payload": {"safety_car_type": kind, "event_type": 1}},
        {"type": "SCAR", "lap": last, "payload": {"safety_car_type": kind, "event_type": 2}},
    ]


def red_flag(lap_number: int, restart: int) -> list[dict[str, Any]]:
    return [
        {"type": "RDFL", "lap": lap_number, "payload": {"active": True}},
        {"type": "SCAR", "lap": lap_number, "payload": {"safety_car_type": 0, "event_type": 3}},
        {"type": "LGOT", "lap": restart, "payload": {}},
    ]


def drivers(result: dict) -> dict[str, dict]:
    return {row["code"]: row for row in result["drivers"]}


def lap_row(result: dict, index: int, number: int) -> dict:
    return next(row for row in result["laps"] if row["car_index"] == index and row["lap_number"] == number)


def json_safe(result: dict) -> None:
    json.dumps(result, allow_nan=False)


# ------------------------------------------------- API-02: never a 500 from data


def test_only_the_lead_lap_car_having_a_hole_is_reported_not_a_key_error() -> None:
    # 5.3.4-era KeyError: the leader's lap count included a car that could not
    # be placed, and its time was looked up on the classified winner.
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS")]
    laps = [lap(0, n, None if n == 3 else 92_000) for n in range(1, 6)]
    laps += [lap(1, n, 92_400) for n in range(1, 5)]
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    json_safe(result)
    rows = drivers(result)
    assert rows["LEC"]["status"] == "incomplete_record"
    assert rows["LEC"]["finish_position"] is None
    # LECLERC timed a lap further than NORRIS, so it is ahead: NORRIS is P2,
    # never the winner, and LECLERC's own place cannot be timed.
    assert rows["NOR"]["finish_position"] == 2
    assert result["winner_car_index"] is None


@pytest.mark.parametrize("seed", range(120))
def test_random_races_never_raise_and_stay_json_safe(seed: int) -> None:
    rng = random.Random(seed)
    session_type = rng.choice(["Race", "Race 2", "Sprint", "Practice 1", "Qualifying 1", "15"])
    car_count = rng.randint(1, 22)
    total = rng.randint(1, 30)
    stops = sorted(rng.sample(range(2, total + 1), k=min(total - 1, rng.choice([0, 0, 1, 2])))) if total > 2 else []
    cars = [car(index, rng.choice(["Car", "VERSTAPPEN", "Zhou Guanyu", "Ève"]), player=index == 0)
            for index in range(car_count)]
    laps: list[AnalysisLap] = []
    for index in range(car_count):
        last = total if rng.random() > 0.2 else rng.randint(0, total)
        pace = rng.randint(80_000, 110_000)
        for number in range(rng.choice([0, 1]), last + 1):
            missing = rng.random() < 0.08
            slow = 2.3 if number in stops else 1.0
            ms = None if missing else rng.choice([int(pace * slow) + rng.randint(-900, 900), 0, -5]) \
                if rng.random() < 0.05 else (None if missing else int(pace * slow) + rng.randint(-900, 900))
            compound = rng.choice(["SOFT", "MEDIUM", "HARD", "INTER", "WET", "UNKNOWN", None, "weird"])
            laps.append(lap(
                index, number, ms, compound=compound,
                age=rng.choice([None, number, 0]) if compound else None,
                pit=rng.random() < 0.05, flag=rng.random() < 0.1, valid=rng.random() > 0.1,
                observed=rng.random() > 0.2, unconfirmed=rng.random() < 0.03,
                sectors=rng.choice([None, (30_000, 31_000, 29_000), (0, 0, 0)]),
            ))
    events = rng.choice([None, [], safety_car(2, 3), safety_car(2, 99, kind=2), red_flag(4, 6)])
    for hide in (True, False):
        result = build_session_analysis(
            {**RACE, "session_type": session_type, "raw_session_type_id": None,
             "status": rng.choice(["complete", "recording", "incomplete", None])},
            cars, laps, hide_outliers=hide, events=events,
            official_player_position=rng.choice([None, 1, 3]),
        )
        json_safe(result)
        for row in result["drivers"]:
            assert row["pit_stops"] is None or row["pit_stops"] >= 0
            assert row["status"] in DRIVER_STATUSES
        assert {row["pace_excluded"] for row in result["laps"]} <= {None, *PACE_EXCLUSION_REASONS}
        assert {row["kind"] for row in result["pit_stops"]} <= set(STOP_KINDS)


# ---------------------------------- API-01: the active flashback branch only


def _history(number: int, *, lap_ms: int = 92_000) -> dict[str, Any]:
    return {"lap_num": number, "lap_ms": lap_ms + number, "s1_ms": 30_000, "s2_ms": 32_000,
            "s3_ms": lap_ms + number - 62_000, "valid_flags": 15}


def _context(car_index: int, **changes: Any) -> dict[str, Any]:
    return {"session_uid": 4_242_424_242, "restart_epoch": 0, "timeline_epoch": 0,
            "player_car_index": car_index, "identity_revision": 0, "history_complete": True,
            "track_id": 12, "session_type": "Race", **changes}


@pytest.mark.asyncio
async def test_abandoned_flashback_laps_never_count_through_the_real_catalog(tmp_path: Path) -> None:
    """Since 5.3.4 flashback-abandoned laps stay in place, flagged with bit 2.

    Car 6 re-drives laps 1-6 and retires; car 7's replacement history is
    complete and empty; car 8 never gets a replacement history and stays
    pending revalidation after the frame-only flashback.
    """
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    for car_index in (6, 7, 8):
        await database.catalog.reconcile_field_history(
            _context(car_index), [_history(number, lap_ms=92_000 + car_index * 100) for number in range(1, 11)]
        )
    archive = FullFieldArchiveService(database.path, TraceStore(tmp_path / "traces"))
    await archive.start()
    assert archive.submit(BranchInvalidation(SessionKey(_context(6)["session_uid"], 0), 0, 1, 20, 2.0, None, (),
                                             "game FLBK"))
    await archive.stop()
    await database.catalog.reconcile_field_history(
        _context(6, timeline_epoch=1), [_history(number, lap_ms=92_600) for number in range(1, 7)]
    )
    await database.catalog.reconcile_field_history(_context(7, timeline_epoch=1), [])
    with sqlite3.connect(database.path) as db:
        abandoned = db.execute("SELECT COUNT(*) FROM recorded_laps WHERE invalid_reason_mask & 2").fetchone()[0]
    assert abandoned == 4 + 10  # car 6 laps 7-10, car 7 laps 1-10

    key = session_id(_context(6)["session_uid"], 0)
    result = await SessionAnalysisService(database.path).analysis(key)
    json_safe(result)
    by_index = {row["car_index"]: row for row in result["drivers"]}
    laps_of = {index: sorted(row["lap_number"] for row in result["laps"] if row["car_index"] == index)
               for index in (6, 7, 8)}
    assert laps_of[6] == list(range(1, 7))
    assert by_index[6]["laps_completed"] == 6
    assert laps_of[7] == []
    assert by_index[7]["laps_completed"] == 0
    assert by_index[7]["status"] == "no_data"
    # Car 8 is pending: its laps are shown as unconfirmed and kept out of
    # pace and fastest laps, with a warning.
    assert laps_of[8] == list(range(1, 11))
    assert all(lap_row(result, 8, n)["unconfirmed"] for n in range(1, 11))
    assert all(lap_row(result, 8, n)["pace_excluded"] in {"unconfirmed", "lap_one"} for n in range(1, 11))
    assert by_index[8]["best_lap_ms"] is None
    assert all(row["car_index"] != 8 for row in result["fastest_laps"])
    assert any("await confirmation after a flashback" in warning for warning in result["warnings"])
    for section in ("race_pace", "fastest_laps"):
        for row in result[section]:
            numbers = row.get("lap_numbers", [row.get("lap_number")])
            if row["car_index"] == 6:
                assert all(number <= 6 for number in numbers)


# ------------------------------------- API-03: timing-only laps are unknown


def test_a_tyre_change_around_timing_only_laps_is_an_unrecorded_stop_not_a_free_change() -> None:
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS")]
    laps = [lap(0, n, 92_000, compound="SOFT", age=n - 1) for n in range(1, 11)]
    # The in- and out-laps were recorded from lap history only.
    laps += [lap(0, 11, 103_000, compound=None, observed=False), lap(0, 12, 110_000, compound=None, observed=False)]
    laps += [lap(0, n, 91_500, compound="HARD", age=n - 13) for n in range(13, 21)]
    laps += [lap(1, n, 92_300) for n in range(1, 21)]
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    stops = [row for row in result["pit_stops"] if row["car_index"] == 0]
    assert [row["kind"] for row in stops] == ["unrecorded_stop"]
    assert stops[0]["estimated_loss_ms"] is None
    assert (stops[0]["from_compound"], stops[0]["to_compound"]) == ("SOFT", "HARD")
    assert not any(row["kind"] == "tyre_change" for row in result["pit_stops"])
    assert any("without a recorded pit stop" in warning for warning in result["warnings"])
    for number in (11, 12):
        row = lap_row(result, 0, number)
        assert row["pit"] is None and row["flags"] is None and row["compound"] is None
        assert row["context_observed"] is False
    # Unknown context is excluded from pace whether or not outliers are hidden.
    for hide in (True, False):
        shown = build_session_analysis(RACE, cars, laps, hide_outliers=hide, events=NO_MESSAGES)
        assert lap_row(shown, 0, 11)["pace_excluded"] == "no_context"
        assert lap_row(shown, 0, 12)["pace_excluded"] == "no_context"


def test_a_car_known_only_from_lap_history_has_unknown_stops_never_zero() -> None:
    """Real Singapore race: BOT and MAR were outside the trace scope after lap 2;
    the official result has them on 3 and 2 stops, the old analysis said 0."""
    cars = [car(0, "LECLERC", player=True), car(1, "BOTTAS")]
    laps = [lap(0, n, 92_000) for n in range(1, 21)]
    laps += [lap(1, 1, 95_000), lap(1, 2, 93_000)]
    for number in range(3, 21):
        ms = 112_000 if number in (8, 15) else 93_000  # two stops nobody observed
        laps.append(lap(1, number, ms, compound=None, observed=False))
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    bot = drivers(result)["BOT"]
    assert bot["pit_stops"] is None
    assert bot["pit_stops_recorded"] == 0
    assert bot["laps_without_context"] == 18
    assert any("Pit stops are unknown for BOT" in warning for warning in result["warnings"])
    # A history-only lap at racing pace cannot have hidden a stop.
    calm = [lap(0, n, 92_000) for n in range(1, 21)]
    calm[-1] = lap(0, 20, 92_400, compound=None, observed=False)
    assert drivers(build_session_analysis(RACE, [car(0, "LECLERC")], calm, events=NO_MESSAGES))["LEC"]["pit_stops"] == 0


def test_timing_only_rivals_do_not_hide_a_safety_car_from_the_field() -> None:
    """22 cars: five with telemetry context, 17 known from lap history only,
    and a safety car on laps 7-9. The old share rule saw no flags on 17 cars
    and reported 'green all race'."""
    cars = [car(index, f"Driver{index:02d}", player=index == 0) for index in range(22)]
    laps: list[AnalysisLap] = []
    for index in range(22):
        for number in range(1, 16):
            ms = 132_500 if number in (7, 8, 9) else 92_000 + index * 50
            observed = index < 5
            laps.append(lap(index, number, ms, flag=observed and number in (7, 8, 9),
                            compound="MEDIUM" if observed else None, observed=observed))
    for hide in (True, False):
        result = build_session_analysis(RACE, cars, laps, hide_outliers=hide, events=safety_car(7, 9))
        assert result["neutralised_laps"] == [7, 8, 9]
        assert result["race_control"]["safety_car_laps"] == [7, 8, 9]
        for number in (7, 8, 9):
            assert lap_row(result, 12, number)["pace_excluded"] == "safety_car"
            assert lap_row(result, 12, number)["neutralised"] == "SC"
        assert lap_row(result, 12, 5)["pace_excluded"] == "no_context"


# ----------------------------- API-04: safety cars come from race control


def test_local_yellows_on_many_cars_are_not_a_safety_car() -> None:
    """Real race laps 2-3: 12 and 17 of 22 cars saw a local yellow."""
    cars = [car(index, f"Driver{index:02d}") for index in range(22)]
    laps = [lap(index, number, 92_000 + index * 40, flag=number == 3 and index < 17)
            for index in range(22) for number in range(1, 9)]
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    assert result["neutralised_laps"] == []
    assert result["race_control"]["neutralisations"] == []
    # The flagged cars' own laps are left out of pace as flag laps, never as
    # safety-car laps.
    assert lap_row(result, 3, 3)["pace_excluded"] == "flags"
    assert lap_row(result, 3, 3)["neutralised"] is None
    assert lap_row(result, 20, 3)["pace_excluded"] is None


def test_safety_car_and_vsc_periods_come_from_race_control_messages() -> None:
    cars = [car(index, f"Driver{index:02d}") for index in range(4)]
    laps = [lap(index, number, 92_000 + index * 40) for index in range(4) for number in range(1, 21)]
    events = safety_car(5, 7) + safety_car(12, 13, kind=2)
    result = build_session_analysis(RACE, cars, laps, events=events)
    assert result["race_control"]["safety_car_laps"] == [5, 6, 7]
    assert result["race_control"]["vsc_laps"] == [12, 13]
    assert result["neutralised_laps"] == [5, 6, 7, 12, 13]
    assert lap_row(result, 1, 6)["pace_excluded"] == "safety_car"
    assert lap_row(result, 1, 12)["pace_excluded"] == "vsc"
    assert lap_row(result, 1, 12)["neutralised"] == "VSC"


def test_no_race_control_record_marks_no_safety_car_and_says_so() -> None:
    cars = [car(index, f"Driver{index:02d}") for index in range(3)]
    laps = [lap(index, number, 92_000, flag=number == 4) for index in range(3) for number in range(1, 9)]
    result = build_session_analysis(RACE, cars, laps, events=None)
    assert result["neutralised_laps"] == []
    assert result["race_control"]["available"] is False
    assert any("Race-control messages were not recorded" in warning for warning in result["warnings"])
    assert lap_row(result, 0, 4)["pace_excluded"] == "flags"


def test_real_singapore_race_control_messages() -> None:
    """The sequence stored for the 2026 Singapore replay (session events)."""
    events = [
        {"type": "LGOT", "lap": 1, "payload": {}},
        {"type": "RDFL", "lap": 4, "payload": {"active": True}},
        {"type": "SCAR", "lap": 4, "payload": {"safety_car_type": 0, "event_type": 3}},
        {"type": "LGOT", "lap": 6, "payload": {}},
        {"type": "SCAR", "lap": 13, "payload": {"safety_car_type": 1, "event_type": 0}},
        {"type": "SCAR", "lap": 16, "payload": {"safety_car_type": 1, "event_type": 1}},
        {"type": "SCAR", "lap": 16, "payload": {"safety_car_type": 1, "event_type": 2}},
        {"type": "SCAR", "lap": 16, "payload": {"safety_car_type": 1, "event_type": 3}},
    ]
    control = race_control_from_events(events, 31)
    assert control.periods == (("SC", 13, 16),)
    assert control.red_flag_laps == (4,)
    assert control.lights_out_laps == (1, 6)
    # A safety car still out when the record ends runs to the last lap.
    open_ended = race_control_from_events(events[:5], 31)
    assert open_ended.periods == (("SC", 13, 31),)
    assert race_control_from_events(None, 31).available is False


# -------------------- API-05: no free suspension change without a suspension


def _red_flag_field() -> list[AnalysisLap]:
    laps: list[AnalysisLap] = []
    for index in range(3):
        for number in range(1, 11):
            if number in (4, 5):
                laps.append(lap(index, number, 205_000 + index * 900, compound="MEDIUM" if number == 4 else None,
                                age=3 if number == 4 else None, observed=number == 4))
            else:
                age = number - 1 if number < 6 else number - 6
                laps.append(lap(index, number, 92_000 + index * 200 + (2_500 if number in (1, 6) else 0),
                                compound="MEDIUM", age=age))
    return laps


def test_a_same_compound_red_flag_change_is_a_suspension_tyre_change() -> None:
    cars = [car(index, name) for index, name in enumerate(("LECLERC", "NORRIS", "PIASTRI"))]
    result = build_session_analysis(RACE, cars, _red_flag_field(), events=red_flag(4, 6))
    assert result["suspended_laps"] == [4, 5]
    stops = [row for row in result["pit_stops"] if row["car_index"] == 0]
    assert [(row["kind"], row["from_compound"], row["to_compound"]) for row in stops] == [
        ("tyre_change", "MEDIUM", "MEDIUM")
    ]
    assert stops[0]["under_neutralisation"] is True and stops[0]["estimated_loss_ms"] is None
    stints = next(row["stints"] for row in result["stints"] if row["car_index"] == 0)
    assert [(s["start_lap"], s["end_lap"]) for s in stints] == [(1, 5), (6, 10)]
    assert drivers(result)["LEC"]["pit_stops"] == 1


def test_a_change_under_a_safety_car_without_pit_context_is_not_a_free_suspension_change() -> None:
    """The 5.3.4 gate: ordinary SC/VSC must not imply free suspension changes."""
    cars = [car(index, f"Driver{index:02d}") for index in range(3)]
    laps: list[AnalysisLap] = []
    for index in range(3):
        for number in range(1, 13):
            sc = number in (5, 6)
            ms = 128_000 + index * 100 if sc else 92_000 + index * 100
            changed = index == 0 and number >= 6
            laps.append(lap(index, number, ms, compound="HARD" if changed else "MEDIUM",
                            age=number - 6 if changed else number - 1))
    result = build_session_analysis(RACE, cars, laps, events=safety_car(5, 6))
    assert result["suspended_laps"] == []
    assert len(result["segments"]) == 1
    stop = next(row for row in result["pit_stops"] if row["car_index"] == 0)
    assert stop["kind"] == "unrecorded_stop"
    assert stop["under_neutralisation"] is True
    assert stop["estimated_loss_ms"] is None


def test_safety_car_pace_is_never_a_suspension() -> None:
    by_car = {index: [lap(index, number, int(92_000 * (1.55 if number in (4, 5) else 1)))
                      for number in range(1, 12)] for index in range(5)}
    assert suspended_laps(by_car) == []


def test_no_stint_starts_before_lap_one() -> None:
    laps = [lap(0, 0, None, compound="SOFT", age=0)] + [lap(0, n, 92_000, compound="MEDIUM") for n in range(1, 6)]
    assert all(stint["start_lap"] >= 1 for stint in stints_for([item for item in laps if item.lap_number >= 1]))
    result = build_session_analysis(RACE, [car(0, "OCON")], laps, events=NO_MESSAGES)
    stints = result["stints"][0]["stints"]
    assert stints[0]["start_lap"] == 1 and stints[0]["compound"] == "MEDIUM"
    assert all(row["lap_number"] >= 1 for row in result["laps"])


# ------------------------- API-07: fastest and ideal laps, consistent sectors


def test_fastest_and_ideal_laps_use_valid_laps_with_consistent_sectors() -> None:
    cars = [car(0, "PIASTRI"), car(1, "NORRIS")]
    laps = [lap(0, n, 93_000, sectors=(30_000, 38_000, 25_000)) for n in range(1, 31)]
    laps[2] = lap(0, 3, 91_800, sectors=(29_500, 37_500, 24_800))                  # the best valid lap
    laps[4] = lap(0, 5, 90_500, valid=False, sectors=(29_000, 37_000, 24_500))      # faster, invalid
    laps[6] = lap(0, 7, 92_600, sectors=(29_200, 30_100, 24_700))                  # s2 rounded/garbled
    laps[8] = lap(0, 9, 92_700, sectors=(29_100, 0, 24_600))                       # zero placeholder
    laps += [lap(1, n, 92_200, sectors=(29_700, 37_800, 24_700)) for n in range(1, 31)]
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    piastri = drivers(result)["PIA"]
    assert piastri["best_lap_ms"] == 91_800 and piastri["best_lap_number"] == 3
    assert result["fastest_laps"][0]["car_index"] == 0 and result["fastest_laps"][0]["lap_number"] == 3
    assert piastri["best_sectors"]["s1"] == {"ms": 29_500, "lap_number": 3}  # not 29,200 or 29,100
    assert piastri["sector_laps_inconsistent"] == 2
    assert piastri["ideal_lap_ms"] == 29_500 + 37_500 + 24_800
    assert piastri["ideal_lap_ms"] <= piastri["best_lap_ms"]
    for hide in (True, False):
        again = build_session_analysis(RACE, cars, laps, hide_outliers=hide, events=NO_MESSAGES)
        assert again["fastest_laps"][0]["lap_time_ms"] == 91_800
    # An ideal lap slower than a real lap is not an ideal lap.
    odd = [lap(0, 1, 90_000), lap(0, 2, 93_000, sectors=(31_000, 31_000, 31_000))]
    assert drivers(build_session_analysis(RACE, [car(0, "ODD")], odd, events=NO_MESSAGES))["ODD"]["ideal_lap_ms"] is None


# --------------------- API-08 / API-16: derived, official and provisional


def test_a_penalty_that_changes_the_official_result_is_surfaced() -> None:
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS"), car(2, "PIASTRI")]
    laps = [lap(index, number, 92_000 + index * 300) for index in range(3) for number in range(1, 11)]
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES, official_player_position=3)
    assert result["official_result"] == {
        "player_position": 3, "derived_player_position": 1, "agrees": False, "player_identity": None,
    }
    assert any("official result is P3" in warning for warning in result["warnings"])
    assert "penalties are not applied" in result["basis"]["finish_order"]
    unknown = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    assert unknown["official_result"]["player_position"] is None
    assert unknown["official_result"]["agrees"] is None


@pytest.mark.parametrize("status", ["recording", "incomplete"])
def test_a_session_that_did_not_finish_recording_is_provisional(status: str) -> None:
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS")]
    laps = [lap(0, n, 92_000) for n in range(1, 9)] + [lap(1, n, 92_400) for n in range(1, 8)]
    result = build_session_analysis({**RACE, "status": status}, cars, laps, events=NO_MESSAGES)
    assert result["session"]["provisional"] is True
    assert {row["status"] for row in result["drivers"]} == {"running"}
    assert any("provisional" in warning for warning in result["warnings"])
    complete = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    assert complete["session"]["provisional"] is False
    assert drivers(complete)["LEC"]["status"] == "finished"


# ------------------------------------------- API-09: the Library's race family


@pytest.mark.parametrize(
    ("session_type", "raw", "race"),
    [
        ("Race", 15, True), ("Sprint", None, True), ("Sprint", 15, True), ("Race 2", 16, True),
        ("Race 3", None, True), ("15", None, True), ("16", None, True), (" race ", None, True),
        ("Sprint Shootout 1", 10, False), ("Qualifying 1", None, False), ("Practice 2", 2, False),
        ("Time Trial", 18, False), (None, None, False), ("Unknown", 0, False),
    ],
)
def test_sprint_numbered_and_digit_labelled_sessions_are_races(session_type: Any, raw: Any, race: bool) -> None:
    assert is_race_session(session_type, raw) is race
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS")]
    laps = [lap(index, number, 92_000 + index * 100) for index in range(2) for number in range(1, 6)]
    result = build_session_analysis({**RACE, "session_type": session_type, "raw_session_type_id": raw},
                                    cars, laps, events=NO_MESSAGES)
    assert result["session"]["is_race"] is race
    assert (result["winner_car_index"] == 0) is race
    assert any(row["position"] for row in result["laps"]) is race


# --------------------------------------- service: catalog-backed sessions


def _create_session(db: sqlite3.Connection, key: str, uid: int, *, track_id: int | None = 12,
                    session_type: str = "Race", raw: int | None = 15, status: str = "complete",
                    started: str = "2026-10-08T13:00:00+00:00", ended: str | None = "2026-10-08T14:00:00+00:00",
                    restart: int = 0) -> None:
    db.execute(
        """
        INSERT INTO recorded_sessions(
            id, legacy_session_uid, game_session_uid, restart_epoch, track_id, track_layout_signature,
            session_type, raw_session_type_id, started_at, ended_at, status,
            packet_format, capture_mode, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'f1:2026:12:4928', ?, ?, ?, ?, ?, 2026, 'balanced', ?, ?)
        """,
        (key, _legacy_session_uid(uid), str(uid), restart, track_id, session_type, raw, started, ended, status,
         started, started),
    )


def _create_car(db: sqlite3.Connection, key: str, index: int, name: str, *, revision: int = 0,
                player: bool = False) -> str:
    car_key = session_car_id(key, index, revision)
    db.execute(
        """
        INSERT INTO session_cars(id, session_id, car_index, identity_revision, display_name,
            anonymized_name, team_id, is_ai, is_player, identity_confidence)
        VALUES (?, ?, ?, ?, ?, ?, 484, ?, ?, 1.0)
        """,
        (car_key, key, index, revision, name, name, int(not player), int(player)),
    )
    return car_key


def _insert_lap(db: sqlite3.Connection, car_key: str, number: int, ms: int | None, *, epoch: int = 0,
                mask: int = 0, engineering: dict[str, Any] | None = None, compound: str = "MEDIUM") -> None:
    db.execute(
        """
        INSERT INTO recorded_laps(
            id, session_car_id, lap_number, timeline_epoch, lap_time_ms, valid, invalid_reason_mask,
            tyre_compound, tyre_age_laps, pit_context, flag_context,
            coverage_ratio, quality_score, created_at, engineering_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1.0, 1.0, '2026-10-08T13:00:00Z', ?)
        """,
        (lap_id(car_key, number, epoch), car_key, number, epoch, ms, int(not mask), mask, compound, number - 1,
         json.dumps(engineering or {})),
    )


def _event(db: sqlite3.Connection, uid: int, lap_number: int, kind: str, payload: dict[str, Any], at: float) -> None:
    db.execute(
        "INSERT INTO session_events(session_uid, track_id, lap_num, event_type, payload_json, created_at) "
        "VALUES (?, 12, ?, ?, ?, ?)",
        (_legacy_session_uid(uid), lap_number, kind, json.dumps(payload), at),
    )


async def _database(tmp_path: Path) -> PitWallDatabase:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    return database


@pytest.mark.asyncio
async def test_service_reads_race_control_official_result_and_library_track_name(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    uid = (1 << 64) - 77  # above 2**63: stored signed in the legacy tables
    key = session_id(uid)
    start = datetime.fromisoformat("2026-10-08T13:22:37+00:00").timestamp()
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, uid, track_id=0, started="2026-10-08T13:22:37+00:00",
                        ended="2026-10-08T14:23:30+00:00")
        player = _create_car(db, key, 0, "LECLERC", player=True)
        rival = _create_car(db, key, 1, "NORRIS")
        for number in range(1, 11):
            _insert_lap(db, player, number, 92_000)
            _insert_lap(db, rival, number, 91_900)
        # The player's laps are linked to its legacy lap rows, as the recorder does.
        db.execute("UPDATE recorded_laps SET legacy_lap_id=lap_number WHERE session_car_id=?", (player,))
        db.execute("INSERT INTO sessions(session_uid, track_id, track_name, session_type, mode_profile, started_at, "
                   "ended_at, result_position, total_laps, setup_json) VALUES (?, 0, '—', 'Race', 'race', ?, ?, 2, 10, '{}')",
                   (_legacy_session_uid(uid), start, start + 3600))
        _event(db, uid, 1, "LGOT", {}, start + 9)
        _event(db, uid, 4, "SCAR", {"safety_car_type": 2, "event_type": 0}, start + 400)
        _event(db, uid, 5, "SCAR", {"safety_car_type": 2, "event_type": 1}, start + 500)
        _event(db, uid, 5, "SCAR", {"safety_car_type": 2, "event_type": 2}, start + 501)
        # A different session's message with the same lap number never leaks in.
        _event(db, 991, 7, "SCAR", {"safety_car_type": 1, "event_type": 0}, start + 700)
        db.commit()
    result = await SessionAnalysisService(database.path).analysis(key)
    json_safe(result)
    assert result["session"]["track_name"] == "Melbourne"  # track 0 is a real circuit
    assert result["session"]["total_laps"] == 10
    assert result["race_control"]["available"] is True
    assert result["race_control"]["vsc_laps"] == [4, 5]
    assert result["race_control"]["safety_car_laps"] == []
    assert result["official_result"] == {
        "player_position": 2, "derived_player_position": 2, "agrees": True, "player_identity": "legacy_laps",
    }


@pytest.mark.asyncio
async def test_restarts_of_one_game_session_keep_their_own_messages_and_no_shared_result(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    uid = 5_555
    first, second = session_id(uid, 0), session_id(uid, 1)
    with sqlite3.connect(database.path) as db:
        _create_session(db, first, uid, started="2026-10-08T13:00:00+00:00", ended="2026-10-08T13:20:00+00:00")
        _create_session(db, second, uid, restart=1, started="2026-10-08T13:30:00+00:00",
                        ended="2026-10-08T14:30:00+00:00")
        for key in (first, second):
            player = _create_car(db, key, 0, "LECLERC", player=True)
            for number in range(1, 9):
                _insert_lap(db, player, number, 92_000)
        db.execute("INSERT INTO sessions(session_uid, track_id, track_name, session_type, mode_profile, started_at, "
                   "ended_at, result_position, total_laps, setup_json) VALUES (?, 12, 'Singapore', 'Race', 'race', 0, "
                   "NULL, 1, 8, '{}')", (_legacy_session_uid(uid),))
        first_start = datetime.fromisoformat("2026-10-08T13:00:00+00:00").timestamp()
        restart_start = datetime.fromisoformat("2026-10-08T13:30:00+00:00").timestamp()
        _event(db, uid, 3, "SCAR", {"safety_car_type": 1, "event_type": 0}, first_start + 300)
        _event(db, uid, 3, "SCAR", {"safety_car_type": 1, "event_type": 2}, first_start + 360)
        _event(db, uid, 6, "SCAR", {"safety_car_type": 2, "event_type": 0}, restart_start + 600)
        _event(db, uid, 6, "SCAR", {"safety_car_type": 2, "event_type": 2}, restart_start + 650)
        db.commit()
    service = SessionAnalysisService(database.path)
    one, two = await service.analysis(first), await service.analysis(second)
    assert one["race_control"]["safety_car_laps"] == [3] and one["race_control"]["vsc_laps"] == []
    assert two["race_control"]["vsc_laps"] == [6] and two["race_control"]["safety_car_laps"] == []
    # The legacy row is per game uid and keeps the first result recorded.
    assert one["official_result"]["player_position"] is None
    assert two["official_result"]["player_position"] is None


@pytest.mark.asyncio
async def test_unknown_tracks_and_missing_lap_context_read_unavailable(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    keys = {}
    with sqlite3.connect(database.path) as db:
        for uid, track in ((7001, -1), (7002, None), (7003, 11)):
            keys[track] = session_id(uid)
            _create_session(db, keys[track], uid, track_id=track, session_type="Practice 2", raw=2)
        db.commit()
    service = SessionAnalysisService(database.path)
    for track, expected in ((-1, None), (None, None), (11, "Monza")):
        result = await service.analysis(keys[track])
        assert result["session"]["track_name"] == expected
        assert "No laps are stored for this session." in result["warnings"]


@pytest.mark.asyncio
async def test_the_newest_epoch_wins_over_a_newer_identity_revision(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    key = session_id(8_008)
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, 8_008)
        first = _create_car(db, key, 3, "ALBON")
        revised = _create_car(db, key, 3, "ALBON", revision=1)
        for number in range(1, 5):
            _insert_lap(db, first, number, 93_000)
        _insert_lap(db, first, 5, 91_000, epoch=1)   # the active branch
        _insert_lap(db, revised, 5, 99_000, epoch=0)  # an older epoch, newer revision
        # An abandoned row never counts, even as the newest epoch.
        _insert_lap(db, first, 6, 80_000, epoch=2, mask=3)
        db.commit()
    result = await SessionAnalysisService(database.path).analysis(key)
    assert lap_row(result, 3, 5)["lap_time_ms"] == 91_000
    assert [row["lap_number"] for row in result["laps"]] == [1, 2, 3, 4, 5]
    assert drivers(result)["ALB"]["laps_completed"] == 5


# ------------------------------------------ route: 404 / 409, never 500


def _client(path: Path, **kwargs: Any) -> TestClient:
    app = FastAPI()
    app.include_router(create_field_router(FieldAnalysisService(path), SessionAnalysisService(path, **kwargs)))
    return TestClient(app)


def test_a_fresh_data_directory_is_a_409_not_a_500(tmp_path: Path) -> None:
    response = _client(tmp_path / "missing.sqlite3").get("/api/v1/sessions/ses_anything/analysis")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "analysis_unavailable"


@pytest.mark.asyncio
async def test_route_bounds_and_unknown_sessions(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    key = session_id(9_009)
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, 9_009)
        player = _create_car(db, key, 0, "LECLERC", player=True)
        for number in range(1, 8):
            _insert_lap(db, player, number, 92_000)
        _insert_lap(db, player, 8, 0)
        db.execute("UPDATE recorded_laps SET tyre_compound='weird', engineering_json='not json' WHERE lap_number=2")
        db.execute("UPDATE recorded_laps SET lap_time_ms=-4 WHERE lap_number=3")
        db.commit()
    client = _client(database.path)
    ok = client.get(f"/api/v1/sessions/{key}/analysis")
    assert ok.status_code == 200
    assert ok.json()["schema_version"] == 2
    assert lap_row(ok.json(), 0, 3)["lap_time_ms"] is None
    missing = client.get("/api/v1/sessions/ses_missing/analysis")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "session_not_found"
    bounded = _client(database.path, max_lap_rows=5).get(f"/api/v1/sessions/{key}/analysis")
    assert bounded.status_code == 409 and bounded.json()["detail"]["code"] == "analysis_unavailable"


# ------------------------------- API-12 / API-13: cheap, read-only, off-loop


@pytest.mark.asyncio
async def test_analysis_reads_no_traces_writes_nothing_and_leaves_the_loop_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _database(tmp_path)
    key = session_id(1_212)
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, 1_212)
        for index in range(22):
            car_key = _create_car(db, key, index, f"Driver{index:02d}", player=index == 0)
            for number in range(1, 71):
                engineering = {"s1_ms": 30_000, "s2_ms": 31_000, "s3_ms": 31_000 + index}
                _insert_lap(db, car_key, number, 92_000 + index, engineering=engineering)
                if number % 9 == 0:
                    _insert_lap(db, car_key, number, 95_000, epoch=1, engineering=engineering)
        db.commit()

    def no_traces(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Session Analysis must not read traces")

    monkeypatch.setattr(TraceStore, "read_range", no_traces, raising=False)
    before = database.path.read_bytes()
    service = SessionAnalysisService(database.path)
    pauses: list[float] = []
    finished = asyncio.Event()

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not finished.is_set():
            await asyncio.sleep(0)
            now = time.perf_counter()
            pauses.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    try:
        result = await service.analysis(key)
        await service.analysis(key, hide_outliers=False)
    finally:
        finished.set()
        await beat
    assert max(pauses) < 0.2
    assert len(result["drivers"]) == 22
    assert all(row["laps_completed"] == 70 for row in result["drivers"])
    assert database.path.read_bytes() == before
    with sqlite3.connect(database.path) as db:
        plan = " ".join(str(row[-1]) for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT l.id FROM recorded_laps l JOIN session_cars c ON c.id=l.session_car_id "
            "WHERE c.session_id=? AND l.lap_number>=1 AND (l.invalid_reason_mask & 2)=0 AND NOT EXISTS ("
            "SELECT 1 FROM recorded_laps newer WHERE newer.session_car_id=l.session_car_id "
            "AND newer.lap_number=l.lap_number AND newer.timeline_epoch>l.timeline_epoch "
            "AND (newer.invalid_reason_mask & 2)=0)", (key,)))
    assert "SCAN l" not in plan and "SCAN recorded_laps" not in plan and "SCAN newer" not in plan
    with sqlite3.connect(database.path) as db:
        events_plan = " ".join(str(row[-1]) for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT event_type, lap_num, payload_json FROM session_events "
            "WHERE session_uid=? AND created_at>=? AND created_at<? ORDER BY created_at, id", (1, 0.0, 1e18)))
    assert "USING INDEX idx_session_events_uid_time" in events_plan


# --------------------------------- API-15: deleted and re-recorded sessions


@pytest.mark.asyncio
async def test_a_deleted_session_is_404_and_its_restart_sibling_is_untouched(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    uid = 6_006
    first, second = session_id(uid, 0), session_id(uid, 1)
    with sqlite3.connect(database.path) as db:
        _create_session(db, first, uid, started="2026-10-08T13:00:00+00:00", ended="2026-10-08T13:20:00+00:00")
        _create_session(db, second, uid, restart=1, started="2026-10-08T13:30:00+00:00",
                        ended="2026-10-08T14:30:00+00:00")
        for key, pace in ((first, 93_000), (second, 92_000)):
            car_key = _create_car(db, key, 0, "LECLERC", player=True)
            for number in range(1, 7):
                _insert_lap(db, car_key, number, pace)
        db.commit()
    service = SessionAnalysisService(database.path)
    before = await service.analysis(second)
    preview = await database.catalog.preview_delete(first)
    assert preview is not None
    await database.catalog.delete_session(first, preview["confirmation_token"])
    client = _client(database.path)
    assert client.get(f"/api/v1/sessions/{first}/analysis").status_code == 404
    after = await service.analysis(second)
    assert after == before


# ------------------------------- partial fields, lapped cars and red flags


def test_a_player_only_race_claims_no_winner_but_still_finds_its_red_flag() -> None:
    """Legacy sessions are backfilled as the player's car alone; the old code
    called them 'Player wins' and timed the red flag into the race."""
    laps = [lap(0, n, 92_000) for n in range(1, 11)]
    laps[3] = lap(0, 4, 210_000)
    laps[4] = lap(0, 5, 232_000)
    laps[5] = lap(0, 6, 95_000)  # restart from the grid
    result = build_session_analysis(RACE, [car(0, "Player", player=True)], laps,
                                    events=red_flag(4, 6), official_player_position=3)
    assert result["suspended_laps"] == [4, 5]
    assert result["winner_car_index"] is None
    player = drivers(result)["PLA"]
    assert player["finish_position"] is None and player["status"] == "unranked"
    assert player["race_time_ms"] is None
    assert result["field"] == {"cars": 1, "cars_with_laps": 1, "complete": False}
    assert result["official_result"]["player_position"] == 3
    assert any("Only one car's laps were recorded" in warning for warning in result["warnings"])
    assert lap_row(result, 0, 4)["pace_excluded"] == "suspended"
    assert lap_row(result, 0, 6)["pace_excluded"] == "restart"
    # Without a red flag on record, one car's slow lap is a spin, not a suspension.
    alone = build_session_analysis(RACE, [car(0, "Player", player=True)], laps, events=NO_MESSAGES)
    assert alone["suspended_laps"] == []


def test_a_partial_field_says_its_positions_are_among_recorded_cars() -> None:
    cars = [car(index, f"Driver{index:02d}", player=index == 0) for index in range(22)]
    laps = [lap(index, number, 92_000 + index * 100) for index in range(3) for number in range(1, 11)]
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    assert result["field"] == {"cars": 22, "cars_with_laps": 3, "complete": False}
    assert any("Only 3 of 22 cars have recorded laps" in warning for warning in result["warnings"])
    assert result["winner_car_index"] == 0  # derived among the three, and labelled so


def _lapped_red_flag_race() -> list[AnalysisLap]:
    """Leaders suspended on laps 6-7 and restarting on lap 8; a backmarker a
    lap down meets the red flag on its lap 5 and restarts on its lap 7."""
    laps: list[AnalysisLap] = []
    for index in range(4):
        for number in range(1, 13):
            if number in (6, 7):
                ms = 210_000 + index * 500 if number == 6 else 232_000
            else:
                ms = 92_000 + index * 150 + (2_500 if number in (1, 8) else 0)
            laps.append(lap(index, number, ms))
    for number in range(1, 12):
        if number in (5, 6):
            ms = 206_000 if number == 5 else 232_500
        else:
            ms = 101_000 + (2_500 if number in (1, 7) else 0)
        laps.append(lap(4, number, ms))
    return laps


def test_a_car_a_lap_down_has_its_own_suspended_and_restart_laps() -> None:
    cars = [car(index, name) for index, name in enumerate(("VERSTAPPEN", "NORRIS", "LECLERC", "RUSSELL", "STROLL"))]
    result = build_session_analysis(RACE, cars, _lapped_red_flag_race(), events=red_flag(6, 8))
    assert result["suspended_laps"] == [6, 7]
    assert lap_row(result, 4, 5)["pace_excluded"] == "suspended"
    assert lap_row(result, 4, 6)["pace_excluded"] == "suspended"
    assert lap_row(result, 4, 7)["pace_excluded"] == "restart"
    assert lap_row(result, 4, 8)["pace_excluded"] != "restart"
    stroll = drivers(result)["STR"]
    assert stroll["status"] == "lapped" and stroll["laps_down"] == 1
    assert stroll["finish_position"] == 5
    # A lap down: its gap at lap 8 is about a lap, never two seconds.
    assert lap_row(result, 4, 8)["gap_to_leader_ms"] > 90_000


def test_a_race_stopped_and_not_resumed_is_classified_by_countback() -> None:
    cars = [car(index, name) for index, name in enumerate(("VERSTAPPEN", "NORRIS", "LECLERC"))]
    laps = []
    for index in range(3):
        for number in range(1, 11):
            ms = (210_000 + index * 400) if number == 9 else 232_000 if number == 10 else 92_000 + index * 200
            laps.append(lap(index, number, ms))
    result = build_session_analysis(RACE, cars, laps, events=[{"type": "RDFL", "lap": 9, "payload": {}}])
    assert result["suspended_laps"] == [9, 10]
    rows = drivers(result)
    assert [rows[code]["finish_position"] for code in ("VER", "NOR", "LEC")] == [1, 2, 3]
    assert all(rows[code]["status"] == "finished" for code in ("VER", "NOR", "LEC"))
    assert rows["VER"]["classified_at_lap"] == 8 and rows["VER"]["laps_completed"] == 10
    assert rows["NOR"]["gap_to_winner_ms"] == 8 * 200
    assert any("stopped and not resumed" in warning for warning in result["warnings"])
    assert not any("No car has a continuous lap record" in warning for warning in result["warnings"])


# ----------------------------- race-control packets lost or recorded late


def _field_with_safety_car(first: int, last: int, laps_total: int = 20) -> tuple[list[AnalysisCar], list[AnalysisLap]]:
    cars = [car(index, f"Driver{index:02d}", player=index == 0) for index in range(6)]
    laps = [lap(index, number, int((92_000 + index * 100) * (1.45 if first <= number <= last else 1)))
            for index in range(6) for number in range(1, laps_total + 1)]
    return cars, laps


def test_a_lost_safety_car_end_closes_when_the_field_is_back_at_racing_pace() -> None:
    cars, laps = _field_with_safety_car(8, 10)
    deployed_only = [{"type": "SCAR", "lap": 8, "payload": {"safety_car_type": 1, "event_type": 0}}]
    result = build_session_analysis(RACE, cars, laps, events=deployed_only)
    assert result["race_control"]["safety_car_laps"] == [8, 9, 10]
    assert lap_row(result, 1, 15)["pace_excluded"] is None


def test_a_lost_deployment_opens_where_the_slow_laps_began() -> None:
    cars, laps = _field_with_safety_car(8, 10)
    end_only = [{"type": "SCAR", "lap": 10, "payload": {"safety_car_type": 1, "event_type": 2}}]
    result = build_session_analysis(RACE, cars, laps, events=end_only)
    assert result["race_control"]["safety_car_laps"] == [8, 9, 10]


def test_messages_recorded_from_mid_race_say_where_coverage_begins() -> None:
    cars, laps = _field_with_safety_car(14, 15)
    result = build_session_analysis(RACE, cars, laps, events=safety_car(14, 15), race_control_from_lap=10)
    assert result["race_control"]["covered_from_lap"] == 10
    assert any("recorded from lap 10" in warning for warning in result["warnings"])
    whole = build_session_analysis(RACE, cars, laps, events=safety_car(14, 15), race_control_from_lap=1)
    assert whole["race_control"]["covered_from_lap"] == 1
    assert not any("recorded from lap" in warning for warning in whole["warnings"])


def test_a_lapped_players_messages_are_moved_onto_the_leaders_laps() -> None:
    """Race control carries the player's lap; a player a lap down hears the
    safety car on lap 9 while the leaders run lap 10."""
    cars = [car(0, "Player", player=True), car(1, "NORRIS"), car(2, "PIASTRI")]
    laps = []
    for index, pace in ((1, 90_000), (2, 90_200)):
        for number in range(1, 19):
            laps.append(lap(index, number, int(pace * (1.45 if number in (10, 11) else 1))))
    for number in range(1, 18):  # 13 s a lap slower: a lap down by lap 8
        laps.append(lap(0, number, int(103_000 * (1.40 if number in (9, 10) else 1))))
    result = build_session_analysis(RACE, cars, laps, events=safety_car(9, 10))
    assert result["race_control"]["safety_car_laps"] == [10, 11]


# ------------------------------------------ lap notes and other formulae


@pytest.mark.asyncio
async def test_laps_excluded_in_lap_notes_stay_out_of_pace(tmp_path: Path) -> None:
    """5.3.0 lap notes: a compromised lap the driver excluded from pace."""
    database = await _database(tmp_path)
    key = session_id(3_303)
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, 3_303)
        player = _create_car(db, key, 0, "LECLERC", player=True)
        rival = _create_car(db, key, 1, "NORRIS")
        for number in range(1, 15):
            _insert_lap(db, player, number, 92_000 + (3_000 if number == 12 else 0))
            _insert_lap(db, rival, number, 92_300)
        notes = {"lap_notes": [
            {"id": "note-1", "lap_ids": [lap_id(player, 12, 0)], "exclude_from_pace": True, "source": "driver",
             "category": "traffic", "pending_laps": []},
            {"id": "note-2", "lap_ids": [lap_id(player, 13, 0)], "exclude_from_pace": False, "source": "driver",
             "category": "other", "pending_laps": []},
            {"id": "note-3", "lap_ids": [], "exclude_from_pace": True, "source": "engineer",
             "category": "mistake", "pending_laps": [{"lap_num": 7, "timeline_epoch": 0}]},
        ]}
        db.execute("UPDATE recorded_sessions SET engineering_notes_json=? WHERE id=?", (json.dumps(notes), key))
        db.commit()
    for hide in (True, False):
        result = await SessionAnalysisService(database.path).analysis(key, hide_outliers=hide)
        assert lap_row(result, 0, 12)["pace_excluded"] == "driver_reported"
        assert lap_row(result, 0, 7)["pace_excluded"] == "driver_reported"
        assert lap_row(result, 0, 13)["pace_excluded"] is None
        assert lap_row(result, 1, 7)["pace_excluded"] is None  # another car's lap 7
        pace = next(row for row in result["race_pace"] if row["car_index"] == 0)
        assert pace["excluded"].get("driver_reported") == 2


def test_c1_to_c5_compounds_are_recorded_compounds_not_missing_data() -> None:
    from pitwall.session_analysis import normalise_compound

    assert [normalise_compound(value) for value in ("C5", "c3", " C1 ", "C7", "UNKNOWN", None)] == [
        "C5", "C3", "C1", None, None, None,
    ]
    laps = [lap(0, n, 98_000, compound="C4", age=n - 1) for n in range(1, 9)]
    laps += [lap(0, n, 97_000, compound="C2", age=n - 9) for n in range(9, 16)]
    laps[7] = lap(0, 8, 118_000, compound="C4", age=7, pit=True)
    laps[8] = lap(0, 9, 116_000, compound="C2", age=0, pit=True)
    result = build_session_analysis(RACE, [car(0, "F2DRIVER"), car(1, "OTHER")],
                                    laps + [lap(1, n, 98_500, compound="C4") for n in range(1, 16)],
                                    events=NO_MESSAGES)
    stints = next(row["stints"] for row in result["stints"] if row["car_index"] == 0)
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints] == [("C4", 1, 8), ("C2", 9, 15)]
    assert drivers(result)["F2D"]["pit_stops"] == 1


# ------------------------------------------ findings of the adversarial review


def test_the_players_out_lap_belongs_to_its_pit_stop() -> None:
    """The player's laps store the pit status at the line: the in-lap carries
    it, the out-lap (ending on track) does not. Field cars carry it on both."""
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS")]
    laps = []
    for number in range(1, 21):
        age = number - 1 if number <= 10 else number - 11
        compound = "MEDIUM" if number <= 10 else "HARD"
        ms = 92_000 + (6_000 if number == 10 else 15_000 if number == 11 else 0)
        laps.append(lap(0, number, ms, compound=compound, age=age, pit=number == 10))
        laps.append(lap(1, number, 92_300))
    for hide in (True, False):
        result = build_session_analysis(RACE, cars, laps, hide_outliers=hide, events=NO_MESSAGES)
        stop = next(row for row in result["pit_stops"] if row["car_index"] == 0)
        assert stop["kind"] == "pit_stop" and stop["laps"] == [10, 11]
        assert stop["estimated_loss_ms"] == 21_000
        assert lap_row(result, 0, 11)["pace_excluded"] == "pit"
        assert drivers(result)["LEC"]["pit_stops"] == 1


@pytest.mark.asyncio
async def test_the_recorders_pit_lap_marker_counts_as_pit_context(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    key = session_id(4_404)
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, 4_404)
        player = _create_car(db, key, 0, "LECLERC", player=True)
        rival = _create_car(db, key, 1, "NORRIS")
        for number in range(1, 16):
            engineering = {"learning_exclusions": ["pit_lap", "tyre_change"]} if number == 9 else {}
            _insert_lap(db, player, number, 92_000 + (14_000 if number == 9 else 0), engineering=engineering)
            _insert_lap(db, rival, number, 92_300)
        db.commit()
    result = await SessionAnalysisService(database.path).analysis(key)
    assert lap_row(result, 0, 9)["pit"] is True
    assert lap_row(result, 0, 9)["pace_excluded"] == "pit"


@pytest.mark.asyncio
async def test_timing_only_copies_from_an_identity_revision_never_hide_observed_laps(tmp_path: Path) -> None:
    """After an identity revision the game's lap history is reconciled again
    under the new revision and inserts timing-only rows for earlier laps."""
    database = await _database(tmp_path)
    key = session_id(5_505)
    timing_only = {"context_observed": False, "learning_exclusions": ["missing_telemetry"]}
    with sqlite3.connect(database.path) as db:
        _create_session(db, key, 5_505)
        first = _create_car(db, key, 19, "PEREZ")
        revised = _create_car(db, key, 19, "PEREZ", revision=1)
        rival = _create_car(db, key, 2, "PIASTRI")
        for number in range(1, 13):
            compound = "MEDIUM" if number < 7 else "HARD"
            _insert_lap(db, first, number, 92_000 + (11_000 if number in (6, 7) else 0), compound=compound)
            _insert_lap(db, revised, number, 92_000 + (11_000 if number in (6, 7) else 0), compound="UNKNOWN",
                        engineering=timing_only)
            _insert_lap(db, rival, number, 92_500)
        db.execute("UPDATE recorded_laps SET pit_context=1 WHERE session_car_id=? AND lap_number IN (6, 7)", (first,))
        db.execute("UPDATE recorded_laps SET tyre_age_laps=lap_number-7 WHERE session_car_id=? AND lap_number>=7",
                   (first,))
        db.commit()
    result = await SessionAnalysisService(database.path).analysis(key)
    perez = drivers(result)["PER"]
    assert perez["pit_stops"] == 1 and perez["laps_completed"] == 12
    stints = next(row["stints"] for row in result["stints"] if row["car_index"] == 19)
    assert [(s["compound"], s["start_lap"]) for s in stints] == [("MEDIUM", 1), ("HARD", 7)]
    assert all(row["context_observed"] for row in result["laps"] if row["car_index"] == 19)


def test_a_short_race_with_a_safety_car_before_a_red_flag_still_finds_the_suspension() -> None:
    cars = [car(0, "LECLERC"), car(1, "NORRIS"), car(2, "PIASTRI")]
    sc = {2: 128_000, 3: 128_400}
    laps = []
    for index, (restart, first) in enumerate(((95_000, 93_400), (95_600, 93_200), (96_400, 93_000))):
        laps.append(lap(index, 1, first))
        for number, ms in sc.items():
            laps.append(lap(index, number, ms + index * 50))
        laps.append(lap(index, 4, 208_000 + index * 4_000))
        laps.append(lap(index, 5, 224_000 + index * 3_000))
        laps.append(lap(index, 6, restart))
    result = build_session_analysis(RACE, cars, laps, events=safety_car(2, 3) + red_flag(4, 6))
    assert result["suspended_laps"] == [4, 5]
    rows = drivers(result)
    assert [rows[code]["finish_position"] for code in ("LEC", "NOR", "PIA")] == [1, 2, 3]
    assert rows["NOR"]["gap_to_winner_ms"] == 600 and rows["PIA"]["gap_to_winner_ms"] == 1_400
    assert rows["LEC"]["race_time_ms"] is None


def test_a_red_flag_on_lap_one_makes_the_next_lap_a_restart() -> None:
    cars = [car(index, f"Driver{index:02d}") for index in range(4)]
    laps = []
    for index in range(4):
        laps.append(lap(index, 1, 200_000 + index * 900))
        laps.append(lap(index, 2, 230_000))
        laps.append(lap(index, 3, 95_500 + index * 100))
        laps += [lap(index, number, 92_000 + index * 100) for number in range(4, 12)]
    result = build_session_analysis(RACE, cars, laps, events=red_flag(1, 3))
    assert result["suspended_laps"] == [1, 2]
    assert lap_row(result, 0, 3)["pace_excluded"] == "restart"
    pace = next(row for row in result["race_pace"] if row["car_index"] == 0)
    assert 3 not in pace["lap_numbers"]


def test_a_car_out_on_lap_one_is_retired_not_missing() -> None:
    cars = [car(0, "LECLERC", player=True), car(1, "NORRIS"), car(2, "PIASTRI")]
    laps = [lap(1, number, 92_000) for number in range(1, 9)] + [lap(2, number, 92_400) for number in range(1, 9)]
    laps.append(lap(0, 1, None))
    result = build_session_analysis(RACE, cars, laps, events=NO_MESSAGES)
    player = drivers(result)["LEC"]
    assert player["status"] == "retired" and player["laps_completed"] == 0
    assert player["finish_position"] is None
    # A car with no lap rows at all is still 'no data'.
    alone = build_session_analysis(RACE, [*cars, car(3, "STROLL")], laps, events=NO_MESSAGES)
    assert drivers(alone)["STR"]["status"] == "no_data"
