"""Circuit facts from recorded races, and the tables that stand in until then.

Shanghai and the three reverse layouts had no entry in any circuit table, the
pit loss was never measured, and the two-compound rule could not be switched
for an event the game treats differently.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from pitwall import circuit_model
from pitwall.config import settings
from pitwall.database import PitWallDatabase
from pitwall.strategy import (
    LAYOUT_PARENT,
    PIT_ENTRY_FRACTION,
    PIT_LOSS_SECONDS,
    TRACK_OVERTAKING_DIFFICULTY,
    TRACK_TYRE_SEVERITY,
    StrategyEngine,
    circuit_value,
)
from pitwall.udp import TRACKS

SHORT_LAYOUTS = {21, 22, 23, 24}
RETIRED = {1, 8, 18, 25, 28}


# --- ST-04: every circuit has a pit loss -----------------------------------------------


def test_every_circuit_the_parser_names_has_a_pit_loss_or_a_parent():
    for track_id in TRACKS:
        if track_id in SHORT_LAYOUTS | RETIRED:
            continue
        assert circuit_value(PIT_LOSS_SECONDS, track_id, None) is not None, TRACKS[track_id]
        assert circuit_value(TRACK_TYRE_SEVERITY, track_id, None) is not None, TRACKS[track_id]


def test_a_reverse_layout_inherits_pit_loss_and_severity_only():
    for reverse, parent in LAYOUT_PARENT.items():
        assert circuit_value(PIT_LOSS_SECONDS, reverse, None) == PIT_LOSS_SECONDS[parent]
        assert circuit_value(TRACK_TYRE_SEVERITY, reverse, None) == TRACK_TYRE_SEVERITY[parent]
        assert reverse not in TRACK_OVERTAKING_DIFFICULTY
        assert reverse not in PIT_ENTRY_FRACTION


# --- ST-03: the two-compound rule ------------------------------------------------------


def _rule(mode="race", used=("MEDIUM",)):
    state = {"mode_profile": mode, "tyre": {"compound": used[-1]},
             "completed_laps": [{"lap_num": n, "compound": used[0]} for n in range(1, 4)]}
    return StrategyEngine._compound_rule(state)


def test_the_rule_applies_to_a_dry_race_by_default():
    assert _rule()["applies"] is True and _rule(mode="sprint")["applies"] is False


@pytest.mark.parametrize("setting,race,sprint", [("always", True, True), ("never", False, False)])
def test_the_rule_can_be_forced_on_or_off(monkeypatch, setting, race, sprint):
    monkeypatch.setattr(settings, "strategy_compound_rule", setting)
    assert _rule()["applies"] is race
    assert _rule(mode="sprint")["applies"] is sprint
    if setting == "never":
        assert "switched off in settings" in _rule()["note"]


def test_the_setting_is_validated():
    from pitwall.config import Settings

    with pytest.raises(ValueError):
        Settings(strategy_compound_rule="sometimes")


# --- F-09: pit loss, severity and safety cars from recorded races ------------------------


def _rival(index, *, stop_lap=10, stationary_ms=2_500, in_extra=12.0, out_extra=9.0, base=90.0):
    laps = []
    for number in range(1, 20):
        seconds = base
        if number == stop_lap - 1:
            seconds += in_extra
        if number == stop_lap:
            seconds += out_extra
        laps.append({"lap_num": number, "lap_ms": int(seconds * 1000), "valid_flags": 1})
    return {
        "car_idx": index, "lap_history": laps,
        "pit_stop_history": [{"lap": stop_lap, "stop_number": 1, "stationary_time_ms": stationary_ms}],
    }


def _race(*drivers, laps=None, session=1, mode="race"):
    return {
        "mode_profile": mode, "session_uid": session, "player_car_index": 0,
        "drivers": [{"car_idx": 0}, *drivers],
        "completed_laps": laps if laps is not None else [{"lap_num": n} for n in range(1, 20)],
    }


def test_pit_loss_is_in_lap_plus_out_lap_less_two_clean_laps():
    assert circuit_model.pit_losses(_race(_rival(1))) == [21.0]


def test_a_repair_stop_is_not_a_pit_loss():
    assert circuit_model.pit_losses(_race(_rival(1, stationary_ms=9_000))) == []


def test_a_stop_under_the_safety_car_is_not_a_pit_loss():
    laps = [{"lap_num": n, "full_course_neutralised": n in (9, 10)} for n in range(1, 20)]
    assert circuit_model.pit_losses(_race(_rival(1), laps=laps)) == []


def test_seven_stops_are_not_enough():
    seven = circuit_model.learn_from_session(_race(*[_rival(i) for i in range(1, 8)]), {})
    value, source = circuit_model.measured(seven, "pit_loss_s", 22.0)
    assert value == 22.0 and "7 of 8 stops" in source
    eight = circuit_model.learn_from_session(_race(*[_rival(i) for i in range(1, 9)], session=2), {})
    value, source = circuit_model.measured(eight, "pit_loss_s", 22.0)
    assert value == pytest.approx(21.0) and source == "measured, 8 stops"


def test_a_measured_value_far_from_its_prior_is_reported_not_used():
    model = circuit_model.learn_from_session(_race(*[_rival(i, in_extra=25.0) for i in range(1, 9)]), {})
    value, source = circuit_model.measured(model, "pit_loss_s", 20.0)
    assert value == 20.0
    assert "too far" in source


def test_a_race_is_learned_once():
    race = _race(*[_rival(i) for i in range(1, 5)])
    once = circuit_model.learn_from_session(race, {})
    assert circuit_model.learn_from_session(race, once) == once
    assert circuit_model.learn_from_session(_race(*[_rival(i) for i in range(1, 5)], session=2), once)["pit_loss_s"]["n"] == 8


def test_practice_teaches_nothing():
    assert circuit_model.learn_from_session(_race(_rival(1), mode="practice"), {}) == {}


def test_severity_is_the_field_s_wear_against_the_default():
    drivers = [{"car_idx": i, "tyre_compound": "MEDIUM", "tyre_age": 10, "tyre_wear": [35.2] * 4} for i in range(1, 6)]
    model = circuit_model.learn_from_session(_race(*drivers), {})
    assert model["severity"]["value"] == pytest.approx(35.2 / 10 / 3.2, abs=0.01)
    assert model["severity"]["car_laps"] == 50
    assert circuit_model.measured(model, "severity", 1.0)[1] == "measured, 50 car-laps"


def test_safety_car_rate_needs_three_races():
    laps = [{"lap_num": n, "full_course_neutralised": n in (5, 6)} for n in range(1, 21)]
    model: dict = {}
    for session in (1, 2):
        model = circuit_model.learn_from_session(_race(laps=laps, session=session), model)
    assert model["safety_car_per_lap"]["periods"] == 2 and model["safety_car_per_lap"]["green_laps"] == 36
    assert circuit_model.measured(model, "safety_car_per_lap", 0.02)[0] == 0.02
    model = circuit_model.learn_from_session(_race(laps=laps, session=3), model)
    assert circuit_model.measured(model, "safety_car_per_lap", 0.0)[1] == "measured, 3 races"


def test_the_planner_names_where_its_pit_loss_came_from():
    measured = circuit_model.learn_from_session(_race(*[_rival(i) for i in range(1, 9)]), {})
    state = {"track_id": 9, "current_lap": 5, "total_laps": 50, "race_control_phase": "green",
             "circuit_model": measured}
    neutral = StrategyEngine._neutralisation(state)
    assert neutral["base_pit_loss_s"] == pytest.approx(21.0)
    assert neutral["base_pit_loss_source"] == "measured, 8 stops"
    table = StrategyEngine._neutralisation({**state, "circuit_model": {}})
    assert table["base_pit_loss_s"] == PIT_LOSS_SECONDS[9]
    assert table["base_pit_loss_source"] == "circuit table"


@pytest.mark.asyncio
async def test_the_circuit_model_is_stored_and_forgotten():
    database = PitWallDatabase(Path(tempfile.mkdtemp()) / "calibration.sqlite3")
    await database.initialize()
    await database.save_preference(circuit_model.preference_key(9), {"pit_loss_s": {"value": 21.0}})
    await database.save_preference("unrelated", 1)
    stored = await database.load_preferences_with_prefix("circuit_model:")
    assert list(stored) == ["circuit_model:9"]
    await database.delete_preference(circuit_model.preference_key(9))
    assert await database.load_preferences_with_prefix("circuit_model:") == {}


def test_the_summary_names_each_source():
    model = circuit_model.learn_from_session(_race(*[_rival(i) for i in range(1, 9)]), {})
    summary = circuit_model.summary(9, "Hungaroring", model, {"pit_loss_s": 20.5, "severity": 1.08})
    rows = {row["name"]: row for row in summary["quantities"]}
    assert rows["pit_loss_s"]["value"] == pytest.approx(21.0) and rows["pit_loss_s"]["source"] == "measured, 8 stops"
    assert rows["severity"]["source"] == "circuit table"
    assert "samples" not in str(summary)


@pytest.mark.asyncio
async def test_a_lap_keeps_the_conditions_it_was_driven_in():
    import sqlite3

    database = PitWallDatabase(Path(tempfile.mkdtemp()) / "conditions.sqlite3")
    await database.initialize()
    lap = {
        "session_uid": 5, "track_id": 9, "track_name": "Hungaroring", "session_type": "Race", "lap_num": 12,
        "lap_time_ms": 95_000, "valid": True, "weather": "Light rain",
        "surface_estimate": 0.41, "field_sector_ratio": 1.07, "field_surface_temp_drop_c": None,
        "full_course_neutralised": False, "local_yellow": True,
    }
    await database.save_lap(lap, [])
    with sqlite3.connect(database.path) as connection:
        stored = connection.execute("SELECT conditions_json FROM laps WHERE lap_num=12").fetchone()[0]
    assert json.loads(stored) == {
        "surface_estimate": 0.41, "field_sector_ratio": 1.07,
        "full_course_neutralised": False, "local_yellow": True,
    }
    exported = await database.history_query(session_uid=5, limit=10)
    assert exported["laps"][0]["conditions"]["surface_estimate"] == 0.41
