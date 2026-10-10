"""Race order on recordings that lost lap rows, and the game's own classification.

Older recordings dropped field cars' lap rows whenever a trace batch failed.
Ranking only the cars with continuous records made the wrong car the winner
(the player shown P1 in races finished P20; cars out on lap 2 shown P1-P4).
The order now comes from the game's recorded race position at each lap end
when lap times cannot order the field, and from the game's final
classification when the recording kept it.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from pitwall.session_analysis import AnalysisCar, AnalysisLap, build_session_analysis

RACE = {"id": "ses_imperfect", "session_type": "Race", "raw_session_type_id": 15, "track_id": 12,
        "status": "complete", "total_laps": 6}
NAMES = ["LECLERC", "NORRIS", "PIASTRI", "RUSSELL", "HADJAR"]


def cars(player: int = 0) -> list[AnalysisCar]:
    return [AnalysisCar(index, (f"car_{index}",), name, None, index + 1, index == player)
            for index, name in enumerate(NAMES)]


def lap(index: int, number: int, ms: int | None = 92_000) -> AnalysisLap:
    return AnalysisLap(index, f"lap_{index}_{number}", number, ms, None, None, None, True, False, False,
                       "MEDIUM", number - 1, traced=True, trace_manifest_id=f"m_{index}_{number}",
                       coverage_ratio=1.0)


def reader(table: dict[tuple[int, int], int], calls: list[int] | None = None):
    def read(laps):
        laps = list(laps)
        if calls is not None:
            calls.append(len(laps))
        return {lap.lap_id: table[(lap.car_index, lap.lap_number)] for lap in laps
                if (lap.car_index, lap.lap_number) in table}
    return read


def by_code(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["display_name"]: row for row in result["drivers"]}


# The real order on the road, P1-P4: NORRIS, LECLERC, PIASTRI, RUSSELL, every lap.
ROAD = {0: 2, 1: 1, 2: 3, 3: 4}


def damaged_record() -> list[AnalysisLap]:
    """Lap 3 lost for every field car; the player's record is continuous.
    The final lap 6 has rows with no times (the session ended at the flag)."""
    laps = []
    for index in range(4):
        for number in range(1, 7):
            if index != 0 and number == 3:
                continue
            laps.append(lap(index, number, None if number == 6 else 92_000 + ROAD[index] * 200))
    return laps


def positions() -> dict[tuple[int, int], int]:
    return {(index, number): ROAD[index] for index in range(4) for number in range(1, 7) if not (index and number == 3)}


def test_a_continuous_record_is_never_ranked_against_a_subset_of_the_field() -> None:
    result = build_session_analysis(RACE, cars(), damaged_record(), events=[], recorded_positions=reader(positions()))
    rows = by_code(result)
    assert result["basis"]["finish_source"] == "game_positions"
    assert [rows[name]["finish_position"] for name in ("NORRIS", "LECLERC", "PIASTRI", "RUSSELL")] == [1, 2, 3, 4]
    assert result["winner_car_index"] == 1
    assert rows["LECLERC"]["laps_completed"] == 6 and rows["LECLERC"]["status"] == "finished"
    assert all(row["position_source"] == "recorded" for row in result["laps"] if row["position"] is not None)
    json.dumps(result, allow_nan=False)


def test_without_recorded_positions_nobody_is_ranked_among_a_subset() -> None:
    result = build_session_analysis(RACE, cars(), damaged_record(), events=[])
    # Only the player has a continuous record, and three cars that ran as far
    # could be ahead of it: no place, no winner.
    assert result["winner_car_index"] is None
    assert all(row["finish_position"] is None for row in result["drivers"])
    assert by_code(result)["LECLERC"]["status"] == "incomplete_record"


def test_a_car_ahead_of_every_unplaced_car_keeps_its_place() -> None:
    # PIASTRI and RUSSELL stop after lap 2 (records end, holes), LECLERC and
    # NORRIS run the distance with complete times: the leaders are certain.
    laps = [lap(index, number, 92_000 + ROAD[index] * 200) for index in (0, 1) for number in range(1, 7)]
    laps += [lap(index, number, 93_000) for index in (2, 3) for number in (1, 3)]
    result = build_session_analysis(RACE, cars(), laps, events=[])
    rows = by_code(result)
    assert (rows["NORRIS"]["finish_position"], rows["LECLERC"]["finish_position"]) == (1, 2)
    assert rows["PIASTRI"]["finish_position"] is None and rows["RUSSELL"]["finish_position"] is None


def test_cars_that_stopped_early_are_not_finishers_and_a_record_that_ends_is_unknown() -> None:
    laps = damaged_record()
    # HADJAR: a timed lap 1, then an unfinished lap 2 at the back; the game reported the retirement.
    laps += [lap(4, 1, 93_000), lap(4, 2, None)]
    table = positions() | {(4, 1): 5, (4, 2): 5}
    incidents = [{"type": "RTMT", "lap": 2, "payload": {"vehicle_idx": 4, "reason": 3}}]
    result = build_session_analysis(RACE, cars(), laps, events=[], incidents=incidents,
                                    recorded_positions=reader(table))
    hadjar = by_code(result)["HADJAR"]
    assert hadjar["status"] == "retired" and hadjar["finish_position"] is None
    assert hadjar["retirement"]["evidence"] == "event" and hadjar["retirement"]["reason"] == "terminal damage"
    # RUSSELL's record simply ends on lap 2 with a timed lap and no retirement message.
    truncated = [row for row in laps if not (row.car_index == 3 and row.lap_number > 2)]
    result = build_session_analysis(RACE, cars(), truncated, events=[], recorded_positions=reader(table))
    russell = by_code(result)["RUSSELL"]
    assert russell["status"] == "incomplete_record" and russell["finish_position"] is None
    assert any("RUSSELL: the record ends at lap 2" in text for text in result["warnings"])


def test_a_recording_that_stops_before_the_distance_claims_no_finish() -> None:
    laps = [row for row in damaged_record() if row.lap_number <= 4]
    result = build_session_analysis(RACE | {"total_laps": 30}, cars(), laps, events=[],
                                    recorded_positions=reader(positions()))
    assert result["winner_car_index"] is None
    assert all(row["finish_position"] is None for row in result["drivers"])
    assert any("The recording stops at lap 4 of 30" in text for text in result["warnings"])
    assert any(row["position"] is not None for row in result["laps"])


def test_a_complete_record_keeps_the_lap_time_order_and_reads_no_trace() -> None:
    laps = [lap(index, number, 92_000 + ROAD[index] * 200) for index in range(4) for number in range(1, 7)]
    calls: list[int] = []
    result = build_session_analysis(RACE, cars(), laps, events=[], recorded_positions=reader(positions(), calls))
    assert calls == []
    assert result["basis"]["finish_source"] == "lap_times"
    assert [by_code(result)[name]["finish_position"] for name in ("NORRIS", "LECLERC", "PIASTRI", "RUSSELL")] == [1, 2, 3, 4]


def test_a_lap_nobody_timed_after_a_red_flag_is_part_of_the_stoppage() -> None:
    laps = []
    for index in range(4):
        for number in range(1, 9):
            if number == 4:
                continue  # lost for the whole field
            ms = 210_000 if number == 3 else 92_000 + ROAD[index] * 200
            laps.append(lap(index, number, ms))
    result = build_session_analysis(RACE | {"total_laps": 8}, cars(), laps, events=[])
    assert 4 in result["suspended_laps"]
    assert any("Lap 4 has no recorded time for any car" in text for text in result["warnings"])
    assert result["basis"]["finish_source"] == "lap_times"
    assert [by_code(result)[name]["finish_position"] for name in ("NORRIS", "LECLERC", "PIASTRI", "RUSSELL")] == [1, 2, 3, 4]


def classification() -> dict[str, Any]:
    rows = [
        # car, position, laps, status, race time, penalties
        (0, 1, 6, 3, 560.0, 0),
        (1, 2, 6, 3, 559.0, 5),  # first on the road, second after a 5 s penalty
        (2, 3, 5, 3, 470.0, 0),
        (3, 5, 4, 5, 380.0, 0),  # disqualified
        (4, 4, 2, 4, 190.0, 0),  # did not finish
    ]
    return {"player_car_index": 0, "cars": [
        {"car_index": c, "position": p, "laps": n, "result_status": s, "total_race_time_s": t, "penalties_s": pen,
         "grid_position": c + 1, "pit_stops": 1}
        for c, p, n, s, t, pen in rows
    ]}


def test_the_games_final_classification_is_the_finish() -> None:
    laps = [lap(index, number, 92_000 + ROAD[index] * 200) for index in range(4) for number in range(1, 7)]
    laps += [lap(4, 1, 95_000), lap(4, 2, 95_000)]
    result = build_session_analysis(RACE, cars(), laps, events=[], classification=classification())
    rows = by_code(result)
    assert result["basis"]["finish_source"] == "classification"
    assert result["winner_car_index"] == 0
    assert (rows["NORRIS"]["finish_position"], rows["NORRIS"]["gap_to_winner_ms"], rows["NORRIS"]["penalties_ms"]) == (2, 4_000, 5_000)
    assert (rows["PIASTRI"]["status"], rows["PIASTRI"]["laps_down"]) == ("lapped", 1)
    assert (rows["RUSSELL"]["status"], rows["RUSSELL"]["result"]) == ("retired", "dsq")
    assert (rows["HADJAR"]["status"], rows["HADJAR"]["result"], rows["HADJAR"]["finish_position"]) == ("retired", "dnf", 4)
    assert result["official_result"]["player_position"] == 1
    assert rows["LECLERC"]["grid_position"] == 1


def test_a_classification_ends_a_recording_that_was_not_closed() -> None:
    laps = [lap(index, number, 92_000) for index in range(4) for number in range(1, 7)]
    result = build_session_analysis(RACE | {"status": "incomplete"}, cars(), laps, events=[],
                                    classification=classification())
    assert result["session"]["provisional"] is False
    assert result["winner_car_index"] == 0


def test_a_lap_chart_orders_the_field_without_reading_traces() -> None:
    chart = {number: [ROAD[0], ROAD[1], ROAD[2], ROAD[3]] for number in range(0, 7)}
    calls: list[int] = []
    result = build_session_analysis(RACE, cars(), damaged_record(), events=[], lap_chart=chart,
                                    recorded_positions=reader({}, calls))
    assert [by_code(result)[name]["finish_position"] for name in ("NORRIS", "LECLERC", "PIASTRI", "RUSSELL")] == [1, 2, 3, 4]
    assert sum(calls) == 0
    assert by_code(result)["NORRIS"]["grid_position"] == 1


def test_partial_final_lap_traces_never_settle_a_finish() -> None:
    laps = [dataclasses.replace(row, coverage_ratio=0.5) if row.lap_number == 6 and row.car_index == 3 else row
            for row in damaged_record()]
    result = build_session_analysis(RACE, cars(), laps, events=[], recorded_positions=reader(positions()))
    assert by_code(result)["RUSSELL"]["finish_position"] is None
    assert any("final-lap trace stops before the line" in text for text in result["warnings"])
