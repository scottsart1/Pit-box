"""Rivals make the stops the rules and the weather force on them.

Rival stops were predicted from one table of tyre life (17, 27 and 42 laps)
with nothing else considered. In a 14- or 20-lap race nobody reached it, so
nineteen rivals who each owed a second dry compound were projected to run to
the flag and every stopping plan of the player's projected last. In the rain
nobody was projected to change tyres at all, and a projected stop could fall
three laps from the flag.
"""

from __future__ import annotations

import pytest

from pitwall.strategy import StrategyEngine


def _rival(car_idx=1, *, compound="MEDIUM", age=4, stints=None, pit_stops=0, gap=1.5):
    return {
        "car_idx": car_idx, "name": f"CAR{car_idx}", "position": car_idx + 1,
        "tyre_compound": compound, "tyre_age": age, "pit_stops": pit_stops,
        "gap_to_player_s": gap, "result_label": "active",
        "tyre_stints": stints if stints is not None else [
            {"start_lap": 1, "end_lap": 255, "compound": compound},
        ],
        "lap_history": [{"lap_ms": 90_000, "valid_flags": 1}],
    }


def _state(*rivals, current_lap=5, total_laps=20, mode="race", track_id=0):
    return {
        "player_car_index": 0, "player_position": 1, "track_id": track_id,
        "race_control_phase": "green", "safety_car": "none", "mode_profile": mode,
        "current_lap": current_lap, "total_laps": total_laps, "drivers": list(rivals),
    }


def _project(state, *, trajectory=None, wetness=0.0):
    remaining = int(state["total_laps"]) - int(state["current_lap"]) + 1
    return StrategyEngine(None, None)._project_rival_finish_times(
        state, {}, remaining, 90.0, 22.0, wetness, trajectory,
    )


# --- the compound the rules still require ----------------------------------------


def test_a_rival_on_its_first_dry_compound_is_projected_to_stop():
    projection = _project(_state(_rival()))[0]
    assert projection["stop_owed"] is True
    assert projection["likely_remaining_stops"] == 1
    assert projection["likely_stop_reasons"] == ["owed_compound"]
    # 45% of 20 laps is lap 9: offset 5 from lap 5.
    assert projection["likely_stop_offsets_laps"] == [5]


def test_the_owed_stop_fits_a_compound_not_yet_used():
    projection = _project(_state(_rival(compound="MEDIUM")))[0]
    assert projection["likely_stop_compounds"][0] in {"SOFT", "HARD"}


def test_a_rival_that_has_run_two_compounds_owes_nothing():
    stints = [{"start_lap": 1, "end_lap": 6, "compound": "SOFT"},
              {"start_lap": 7, "end_lap": 255, "compound": "MEDIUM"}]
    projection = _project(_state(_rival(stints=stints, pit_stops=1), current_lap=9))[0]
    assert projection["stop_owed"] is False
    assert projection["likely_remaining_stops"] == 0


def test_a_grid_swap_is_not_a_compound_used():
    stints = [{"start_lap": 1, "end_lap": 0, "compound": "SOFT"},
              {"start_lap": 1, "end_lap": 255, "compound": "HARD"}]
    assert StrategyEngine._rival_owes_compound(_state(), _rival(compound="HARD", stints=stints)) is True


def test_a_rival_that_ran_inters_owes_nothing():
    stints = [{"start_lap": 1, "end_lap": 6, "compound": "INTER"},
              {"start_lap": 7, "end_lap": 255, "compound": "MEDIUM"}]
    assert StrategyEngine._rival_owes_compound(_state(), _rival(stints=stints, pit_stops=1)) is False


def test_a_rival_that_stopped_without_history_is_not_assumed_to_owe():
    """Its earlier compounds are unknown, not one."""
    assert StrategyEngine._rival_owes_compound(_state(), _rival(stints=[], pit_stops=1)) is False


def test_a_sprint_binds_nobody():
    projection = _project(_state(_rival(), mode="sprint", total_laps=17))[0]
    assert projection["stop_owed"] is False
    assert projection["likely_remaining_stops"] == 0


def test_an_owed_stop_already_past_its_share_is_made_now():
    projection = _project(_state(_rival(age=14), current_lap=15, total_laps=20))[0]
    assert projection["likely_stop_offsets_laps"] == [1]


@pytest.mark.parametrize("total_laps,current_lap,age", [(14, 4, 3), (20, 5, 4), (29, 8, 7), (57, 12, 11)])
def test_no_rival_stops_in_the_last_two_laps(total_laps, current_lap, age):
    state = _state(_rival(age=age), _rival(2, compound="SOFT", age=age), current_lap=current_lap,
                   total_laps=total_laps, track_id=3)
    remaining = total_laps - current_lap + 1
    for projection in _project(state):
        assert max(projection["likely_stop_offsets_laps"], default=0) <= remaining - 2


def test_tyre_life_shortens_on_a_hard_circuit():
    gentle = _project(_state(_rival(stints=[], compound="SOFT", age=10, pit_stops=0), track_id=31,
                             mode="sprint", total_laps=40))[0]
    harsh = _project(_state(_rival(stints=[], compound="SOFT", age=10, pit_stops=0), track_id=32,
                            mode="sprint", total_laps=40))[0]
    assert harsh["likely_stop_offsets_laps"][0] < gentle["likely_stop_offsets_laps"][0]


@pytest.mark.asyncio
async def test_the_pit_window_tool_says_the_stop_is_owed(stack):
    from tools.strategy_probe import build_field, race_basics

    store, _, strategy, *_ = stack

    def setup(state):
        race_basics(state, total_laps=20, current_lap=5, position=10)
        state.weather = "Clear"
        state.drivers = build_field(current_lap=5, player_position=10, compound="MEDIUM", tyre_age=4)

    await store.mutate(setup)
    report = await strategy.predict_rival_strategy(top_n=24)
    assert report["another_stop_counts"]["owed"] == 19
    for rival in report["rivals"]:
        assert rival["stop_owed"] is True and rival["confidence"] == "medium"
        assert rival["projected_stop_lap"] <= 18


# --- the weather ---------------------------------------------------------------


def test_rivals_on_slicks_change_for_the_rain():
    trajectory = [0.10, 0.25, 0.38, 0.46, 0.50, 0.52, 0.52, 0.52]
    state = _state(_rival(stints=[], pit_stops=0), mode="sprint", current_lap=10, total_laps=17)
    projection = _project(state, trajectory=trajectory, wetness=0.05)[0]
    # The surface passes the crossover on offset 2, so the stop is at the end of it.
    assert projection["likely_stop_offsets_laps"] == [3]
    assert projection["likely_stop_reasons"] == ["weather"]
    assert projection["likely_stop_compounds"] == ["INTER"]


def test_rivals_on_inters_wait_for_a_clearly_dry_line():
    trajectory = [0.40, 0.33, 0.30, 0.24, 0.20, 0.15, 0.10, 0.05]
    state = _state(_rival(compound="INTER", stints=[], pit_stops=0), mode="sprint",
                   current_lap=10, total_laps=17)
    projection = _project(state, trajectory=trajectory, wetness=0.42)[0]
    # 0.33 and 0.30 are under the crossover but not by the margin.
    assert projection["likely_stop_offsets_laps"] == [4]
    assert projection["likely_stop_compounds"][0] in {"SOFT", "MEDIUM", "HARD"}


def test_a_rival_on_slicks_slows_as_the_track_gets_wetter():
    trajectory = [0.0, 0.1, 0.2, 0.3]
    state = _state(_rival(stints=[], pit_stops=0), mode="sprint", current_lap=13, total_laps=16)
    laps = _project(state, trajectory=trajectory, wetness=0.0)[0]["lap_times_s"]
    assert laps == sorted(laps) and laps[-1] > laps[0] + 1.0


def test_a_dry_race_is_untouched_by_the_weather_model():
    state = _state(_rival(stints=[], pit_stops=0), mode="sprint", current_lap=13, total_laps=16)
    assert _project(state) == _project(state, trajectory=[0.0] * 4)


def test_a_set_younger_than_the_race_shows_a_change_the_counter_missed():
    """Age 12 at lap 21 with no stop recorded: a tyre was changed, compound unknown."""
    driver = _rival(age=12, pit_stops=0)
    driver["current_lap"] = 21
    assert StrategyEngine._rival_owes_compound(_state(current_lap=21, total_laps=28), driver) is False
    fresh_from_the_start = _rival(age=20, pit_stops=0)
    fresh_from_the_start["current_lap"] = 21
    assert StrategyEngine._rival_owes_compound(_state(current_lap=21, total_laps=28), fresh_from_the_start) is True
