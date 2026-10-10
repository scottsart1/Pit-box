"""Session Analysis of a real recorded race against the game's own result.

Opt-in: the recording is private test data, so this runs only when
PITWALL_REAL_RACE_DATA names a data directory holding the 2026 Singapore
replay (session ses_44a380e4a94a0bc59b959b73, 31 laps, red flag on laps 4-5,
safety car on laps 13-16) and PITWALL_REAL_RACE_EXPECTED names the final
classification decoded independently from the original capture
(full-race-expected.json). The database is copied first: the original is
never opened.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from pitwall.session_analysis import SessionAnalysisService

DATA = os.environ.get("PITWALL_REAL_RACE_DATA")
EXPECTED = os.environ.get("PITWALL_REAL_RACE_EXPECTED")
SESSION = "ses_44a380e4a94a0bc59b959b73"
FINISHED = 3  # final classification result status

pytestmark = pytest.mark.skipif(
    not (DATA and EXPECTED and Path(DATA, "pitwall.sqlite3").is_file() and Path(EXPECTED).is_file()),
    reason="private real-race recording not configured",
)


@pytest.fixture(scope="module")
def analysis(tmp_path_factory: pytest.TempPathFactory) -> dict:
    scratch = tmp_path_factory.mktemp("real-race") / "pitwall.sqlite3"
    shutil.copy2(Path(DATA or "", "pitwall.sqlite3"), scratch)
    return SessionAnalysisService(scratch)._analysis_sync(SESSION, True)


@pytest.fixture(scope="module")
def official() -> list[dict]:
    capture = json.loads(Path(EXPECTED or "").read_text(encoding="utf-8"))
    return capture["final_classifications"][-1]["packet"]["classification_data"]


def test_order_laps_best_laps_and_gaps_match_the_official_classification(analysis: dict, official: list[dict]) -> None:
    winner_time = min(row["total_race_time"] for row in official if row["result_status"] == FINISHED)
    assert len(analysis["drivers"]) == 22
    for driver in analysis["drivers"]:
        result = official[driver["car_index"]]
        assert driver["finish_position"] == result["position"], driver["code"]
        assert driver["laps_completed"] == result["num_laps"], driver["code"]
        assert driver["best_lap_ms"] == result["best_lap_time_in_ms"], driver["code"]
        if result["result_status"] == FINISHED:
            gap = (result["total_race_time"] - winner_time) * 1000
            assert driver["gap_to_winner_ms"] is not None
            assert abs(driver["gap_to_winner_ms"] - gap) <= 10, driver["code"]
    assert {key: analysis["official_result"][key] for key in ("player_position", "derived_player_position", "agrees")} == {
        "player_position": 1, "derived_player_position": 1, "agrees": True,
    }


def test_the_lap_record_flags_and_suspension_are_read_from_the_recording(analysis: dict) -> None:
    timed = [row for row in analysis["laps"] if row["lap_time_ms"]]
    assert len(timed) == 624
    completed = {row["car_index"]: row["laps_completed"] for row in analysis["drivers"]}
    assert completed[6] == 31 and completed[4] == 2 and completed[5] == 2
    assert analysis["suspended_laps"] == [4, 5]
    assert analysis["race_control"]["safety_car_laps"] == [13, 14, 15, 16]
    assert analysis["race_control"]["red_flag_laps"] == [4]
    # Local yellows on laps 2-3 and the restart on lap 6 are not a safety car.
    assert not {2, 3, 6} & set(analysis["neutralised_laps"])
    assert {row["lap_number"]: row["pace_excluded"] for row in analysis["laps"] if row["car_index"] == 6}[6] == "restart"
    # A suspended race has no single race clock in lap times: never present
    # the 188 s-short sum as the race time.
    assert all(row["race_time_ms"] is None for row in analysis["drivers"])


def test_pit_stops_match_or_are_unknown_never_a_false_zero(analysis: dict, official: list[dict]) -> None:
    by_code = {row["code"]: row for row in analysis["drivers"]}
    # Outside the trace scope after lap 2: known from lap history only.
    assert by_code["BOT"]["pit_stops"] is None and by_code["MAR"]["pit_stops"] is None
    # The game counts a retirement into the garage as a stop; the analysis
    # counts stops made while racing and shows the car as retired.
    assert by_code["HAD"]["status"] == by_code["VER"]["status"] == "retired"
    for driver in analysis["drivers"]:
        if driver["code"] in {"BOT", "MAR", "HAD", "VER"}:
            continue
        assert driver["pit_stops"] == official[driver["car_index"]]["num_pit_stops"], driver["code"]
    kinds = {row["kind"] for row in analysis["pit_stops"]}
    assert kinds <= {"pit_stop", "tyre_change"}  # every change here was observed
    assert all(row["under_neutralisation"] for row in analysis["pit_stops"] if row["kind"] == "tyre_change")


def test_the_players_stints_follow_the_red_flag_change(analysis: dict) -> None:
    stints = next(row["stints"] for row in analysis["stints"] if row["car_index"] == 6)
    # The last lap driven on each set. The game's own record ends the first
    # stint one lap later (on lap 6, the restart); its convention for the
    # player's red-flag change differs from the rest of the field's.
    assert [(s["compound"], s["start_lap"], s["end_lap"]) for s in stints] == [
        ("MEDIUM", 1, 5),
        ("MEDIUM", 6, 13),
        ("HARD", 14, 31),
    ]
