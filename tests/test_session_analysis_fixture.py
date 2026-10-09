from __future__ import annotations

import json

from session_analysis_fixture import FIXTURE, build_fixture


def test_node_chart_fixture_matches_the_backend_contract() -> None:
    """The Node chart tests read this file; regenerate it when this fails.

    python tests/session_analysis_fixture.py
    """
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(build_fixture()))


def test_fixture_race_exercises_every_chart_case() -> None:
    result = build_fixture()
    drivers = {row["code"]: row for row in result["drivers"]}
    assert result["suspended_laps"] == [6, 7]
    assert result["neutralised_laps"] == [3]
    assert result["segments"] == [{"first_lap": 1, "last_lap": 5}, {"first_lap": 8, "last_lap": 12}]
    assert drivers["LEC"]["finish_position"] == 1 and drivers["LEC"]["is_player"]
    assert drivers["BOT"]["status"] == "lapped"
    assert drivers["HAD"]["status"] == "retired"
    assert [s["kind"] for s in result["pit_stops"] if s["car_index"] == 1] == ["tyre_change", "pit_stop"]
    assert [s["kind"] for s in result["pit_stops"] if s["car_index"] == 3] == ["tyre_change", "unrecorded_stop"]
    bottas = next(row["stints"] for row in result["stints"] if row["car_index"] == 4)
    assert bottas[-1]["compound"] is None
    assert drivers["BOT"]["pit_stops"] is None  # known from lap history only
    assert drivers["LEC"]["pit_stops"] == 1
    assert result["race_control"]["safety_car_laps"] == [3]
    assert result["race_control"]["red_flag_laps"] == [6]
    piastri_4 = next(row for row in result["laps"] if row["car_index"] == 2 and row["lap_number"] == 4)
    assert piastri_4["pace_excluded"] == "flags" and piastri_4["neutralised"] is None
    bottas_9 = next(row for row in result["laps"] if row["car_index"] == 4 and row["lap_number"] == 9)
    assert bottas_9["pace_excluded"] == "no_context" and bottas_9["traced"] is False
