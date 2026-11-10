"""Possible races from here to the flag, and the call prepared for a safety car.

The Monte Carlo used to perturb one deterministic race: no safety car, one
weather outcome, rivals on one fixed schedule. Each draw is now a possible
race, shared by every plan, and the planner decides on each green lap what it
would do if a safety car or a VSC came out on it.
"""

from __future__ import annotations

import numpy as np
import pytest

from pitwall.proactive import ProactiveEngineer
from pitwall.race_simulator import (
    NONE,
    SAFETY_CAR,
    SAFETY_CAR_LOSS_SHARE,
    RaceSimulation,
    finishing_chances,
    hazard_for,
)
from pitwall.strategy import StrategyEngine

STATE = {"session_uid": 7, "current_lap": 20, "track_id": 3, "mode_profile": "race"}


def _race(*, hazard=0.1, weather=None, rivals=(), samples=400, state=STATE):
    return RaceSimulation.build(state, list(rivals), weather, 30, samples, hazard=hazard)


# --- the draws ------------------------------------------------------------------------


def test_every_plan_sees_the_same_races():
    first, second = _race(), _race()
    assert np.array_equal(first.neutral_kind, second.neutral_kind)
    assert np.array_equal(first.neutral_start, second.neutral_start)
    assert np.array_equal(first.scenario, second.scenario)
    # A later lap is a new decision: new draws.
    later = _race(state={**STATE, "current_lap": 21})
    assert not np.array_equal(first.neutral_start, later.neutral_start)


def test_a_neutralisation_comes_out_from_the_next_lap_at_the_given_rate():
    race = _race(hazard=0.05, samples=1200)
    neutralised = race.neutral_kind != NONE
    assert (race.neutral_start[neutralised] >= 1).all()
    assert (race.neutral_start[neutralised] < 30).all()
    # At 0.05 + 0.025 a lap, about 9 races in 10 see one inside 30 laps.
    assert 0.8 < float(np.mean(neutralised)) < 0.98
    assert 0.55 < float(np.mean(race.neutral_kind[neutralised] == SAFETY_CAR)) < 0.78


def test_a_street_circuit_doubles_the_prior_and_a_measured_rate_replaces_it():
    assert hazard_for({"track_id": 3}) == (0.02, "circuit table")
    assert hazard_for({"track_id": 12})[0] == 0.04
    measured = {"safety_car_per_lap": {"periods": 3, "green_laps": 150, "races": 3, "value": 0.02}}
    assert hazard_for({"track_id": 3, "circuit_model": measured})[1] == "measured, 3 races"


# --- what a draw does to a plan ---------------------------------------------------------


def test_a_stop_that_is_still_to_come_gains_from_a_safety_car():
    race = _race(hazard=0.15)
    stop = {"box_laps": [25], "compounds": ["MEDIUM", "HARD"], "pit_stop_costs_s": [22.0]}
    adjustment = race.plan_adjustment(stop, "MEDIUM")
    covered = (race.neutral_kind == SAFETY_CAR) & (race.neutral_start <= 5) & (race.neutral_start + race.neutral_laps - 1 >= 5)
    assert covered.any()
    assert adjustment[covered] == pytest.approx(-22.0 * (1.0 - SAFETY_CAR_LOSS_SHARE))
    assert (adjustment[race.neutral_kind == NONE] == 0).all()
    # A plan that does not stop again takes nothing from it.
    assert (race.plan_adjustment({"box_laps": [], "compounds": ["MEDIUM"]}, "MEDIUM") == 0).all()


def test_a_plan_on_slicks_loses_in_the_wet_draws():
    weather = {
        "dry_base_lap_s": 90.0,
        "scenarios": [
            {"probability": 0.5, "trajectory": [0.0] * 30},
            {"probability": 0.5, "trajectory": [0.0] * 5 + [0.8] * 25},
        ],
    }
    race = _race(hazard=0.0, weather=weather)
    wet = race.scenario == 1
    assert 0.35 < float(np.mean(wet)) < 0.65
    slicks = race.plan_adjustment({"box_laps": [], "compounds": ["MEDIUM"]}, "MEDIUM")
    inters = race.plan_adjustment({"box_laps": [24], "compounds": ["MEDIUM", "INTER"], "pit_stop_costs_s": [22.0]}, "MEDIUM")
    # Against its own expectation, every tyre is slower in the wet draws; the
    # slicks lose far more there than the inters, and gain more in the dry.
    assert (slicks[wet] > 0).all() and (slicks[~wet] < 0).all()
    assert ((slicks - inters)[wet] > 30).all() and ((slicks - inters)[~wet] < -30).all()
    assert race.summary()["share_wet_finish"] == pytest.approx(float(np.mean(wet)), abs=1e-3)


def test_a_rival_whose_stop_falls_under_the_safety_car_gains_too():
    rival = {"finish_time_s": 100.0, "likely_stop_offsets_laps": [6], "likely_stop_compounds": ["HARD"],
             "pit_stop_costs_s": [22.0], "compound": "MEDIUM"}
    race = _race(hazard=0.2, rivals=[rival])
    assert race.rival_times.shape == (1, race.samples)
    assert race.rival_times.max() == pytest.approx(100.0)
    assert race.rival_times.min() == pytest.approx(100.0 - 22.0 * (1.0 - SAFETY_CAR_LOSS_SHARE))


# --- what it does to the distribution ------------------------------------------------------


def _plan_and_field():
    state = {**STATE, "player_position": 5, "active_cars": 10, "total_laps": 50}
    rivals = [{"car_idx": i, "position": i if i < 5 else i + 1, "finish_time_s": 1000.0 + 2.0 * (i - 4),
               "likely_stop_offsets_laps": [4 + i], "likely_stop_compounds": ["HARD"],
               "pit_stop_costs_s": [22.0], "compound": "MEDIUM"} for i in range(1, 10)]
    plan = {"projected_rejoin_position": 5, "stops_remaining": 1, "box_laps": [26],
            "compounds": ["MEDIUM", "HARD"], "pit_stop_costs_s": [22.0], "projected_time_s": 1000.0}
    return state, rivals, plan


def test_no_safety_car_hazard_reproduces_the_current_distribution():
    state, rivals, plan = _plan_and_field()
    outcomes = 1000.0 + np.random.default_rng(3).normal(0, 3, 400)
    race = RaceSimulation.build(state, rivals, None, 30, 400, hazard=0.0)
    assert (race.plan_adjustment(plan, "MEDIUM") == 0).all()
    with_race = StrategyEngine._position_distribution(plan, state, rivals, outcomes, race=race)
    without = StrategyEngine._position_distribution(plan, state, rivals, outcomes)
    for key in ("outcome_distribution", "position_probabilities", "p_win", "p_podium", "p_points"):
        assert with_race[key] == without[key], key
    # No safety car in any draw: nothing to compare it against.
    assert with_race["safety_car_swing"] is None


def test_finishing_chances_count_the_points_places_of_the_format():
    positions = np.asarray([1, 2, 3, 4, 9, 10, 11, 12])
    assert finishing_chances(positions, "race") == {"p_win": 0.125, "p_podium": 0.375, "p_points": 0.75}
    assert finishing_chances(positions, "sprint")["p_points"] == 0.5


def test_the_compute_names_the_simulation_and_every_plan_its_chances():
    from tools.strategy_probe import build_field, new_engine, race_basics

    async def run():
        store, engine = await new_engine()

        def setup(state):
            race_basics(state, total_laps=57, current_lap=20, position=8)
            state.weather = "Clear"
            state.tyre.compound, state.tyre.age_laps, state.tyre.wear = "MEDIUM", 19, [48.0] * 4
            state.drivers = build_field(current_lap=20, player_position=8, compound="MEDIUM", tyre_age=19)

        await store.mutate(setup)
        return await engine.recompute()

    import asyncio

    plan = asyncio.run(run())
    assert plan["race_simulation"]["safety_car_hazard_per_lap"] == 0.02
    assert "+race_simulation" in plan["recommended"]["monte_carlo"]["uncertainty_basis"]
    for item in plan["plans"]:
        assert 0.0 <= item["p_win"] <= item["p_podium"] <= item["p_points"] <= 1.0


# --- the call prepared for a safety car --------------------------------------------------


async def _green_engine(lap=20):
    from tools.strategy_probe import build_field, new_engine, race_basics

    store, engine = await new_engine()

    def setup(state):
        race_basics(state, total_laps=57, current_lap=lap, position=8)
        state.weather = "Clear"
        state.tyre.compound, state.tyre.age_laps, state.tyre.wear = "MEDIUM", 19, [48.0] * 4
        state.tyre_sets = [{"compound": c, "available": True, "index": i}
                           for i, c in enumerate(("SOFT", "MEDIUM", "HARD", "HARD"))]
        state.drivers = build_field(current_lap=lap, player_position=8, compound="MEDIUM", tyre_age=19)

    await store.mutate(setup)
    return store, engine


@pytest.mark.asyncio
async def test_the_prepared_safety_car_call_matches_the_live_one():
    store, engine = await _green_engine()
    green = await engine.recompute()
    ready = green["if_neutralised"]
    assert ready["lap"] == 20 and ready["safety_car"]["instruction"]

    def safety_car(state):
        state.race_control_phase, state.safety_car = "safety_car", "full"

    await store.mutate(safety_car)
    live = await engine.recompute()
    assert live["recommended"]["box_lap"] == ready["safety_car"]["box_lap"]
    assert live["recommended"]["fit_compound"] == ready["safety_car"]["fit_compound"]
    # Nothing is prepared under the flag itself.
    assert "if_neutralised" not in live


@pytest.mark.asyncio
async def test_readiness_is_computed_once_a_lap():
    store, engine = await _green_engine()
    calls = []
    original = engine._compute_isolated

    async def counted(state, historical):
        calls.append(str(state.get("race_control_phase")))
        return await original(state, historical)

    engine._compute_isolated = counted
    await engine.recompute()
    assert sorted(calls) == ["green", "safety_car", "vsc"]
    calls.clear()
    await engine.recompute()
    assert calls == ["green"]

    def next_lap(state):
        state.current_lap = 21

    await store.mutate(next_lap)
    calls.clear()
    await engine.recompute()
    assert sorted(calls) == ["green", "safety_car", "vsc"]


def _flag_state(**extra):
    call = {"instruction": "Box this lap for hards", "box_lap": 20, "fit_compound": "HARD", "gain_vs_green_plan_s": 11.2}
    state = {
        "current_lap": 20, "track_id": 3, "track_length_m": 5000.0, "lap_distance_m": 2000.0,
        "strategy": {"neutralisation": {"phase": "green"},
                     "recommended": {"instruction": "Box lap 27 for hards"},
                     "if_neutralised": {"lap": 20, "safety_car": call, "vsc": {**call, "instruction": "Stay out"}}},
    }
    state.update(extra)
    return state


def test_the_safety_car_call_carries_what_was_prepared_for_it():
    state = _flag_state()
    assert ProactiveEngineer._prepared_neutralisation_call("safety_car", state) == "Box this lap for hards"
    assert ProactiveEngineer._prepared_neutralisation_call("vsc", state) == "Stay out"
    assert ProactiveEngineer._prepared_neutralisation_call("red_flag", state) == ""
    event = {"type": "race_control", "payload": {"to": "safety_car", "prepared": "Box this lap for hards",
                                                 "strategy": {"instruction": "Box lap 27 for hards"}}}
    assert ProactiveEngineer._fallback_text(event, state) == "Race control: Safety car. Box this lap for hards."


@pytest.mark.parametrize("change", [
    {"current_lap": 21},                       # prepared for another lap
    {"lap_distance_m": 4900.0},                # the pit entry is behind the car
])
def test_a_prepared_call_that_no_longer_applies_is_not_said(change):
    assert ProactiveEngineer._prepared_neutralisation_call("safety_car", _flag_state(**change)) == ""


def test_once_the_plan_is_recomputed_under_the_flag_it_speaks_for_itself():
    state = _flag_state()
    state["strategy"]["neutralisation"] = {"phase": "safety_car"}
    assert ProactiveEngineer._prepared_neutralisation_call("safety_car", state) == ""
