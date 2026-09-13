"""Checks the independent evaluator's physical arithmetic, not planner choices."""

import itertools
import json
import math
from dataclasses import replace

import pytest

from tools.strategy_validation import (
    MANIFEST_PATH,
    PhysicalSet,
    World,
    build_world,
    closed_loop,
    exact_oracle,
    lap_cost,
    output_contracts,
    score_assignment,
    score_plan,
    summarize,
    world_state,
)


def small_world(horizon=4, pit_loss=3.0):
    return World("unit", 1, "linear", "training", horizon,
                 (PhysicalSet(0, "MEDIUM", 0, 1, 5, 0, 0),
                  PhysicalSet(1, "HARD", 0, 1, 0, -2, 0)),
                 pit_loss, (False,) * horizon, (0.0,) * horizon,
                 base_lap_s=10, cold_cost_s=0)


def brute_force(world):
    """Independent enumeration of stop laps and permutations, without DP recursion."""
    best = math.inf
    spare_ids = [tyre.index for tyre in world.sets[1:]]
    for count in range(len(spare_ids) + 1):
        for laps in itertools.combinations(range(world.horizon), count):
            for indices in itertools.permutations(spare_ids, count):
                score, _ = score_assignment(world, list(laps), list(indices))
                best = min(best, score)
    return best


def test_oracle_exact_stop_arithmetic():
    world = small_world()
    # Four old laps cost 40; four fresh laps plus a stop cost 32 + 3.
    assert exact_oracle(world)["time_s"] == 35
    assert exact_oracle(world)["stops"] == [{"offset": 0, "set_index": 1}]
    assert exact_oracle(replace(world, pit_loss_s=9))["time_s"] == 40


def test_independent_weather_exposure_is_charged_on_actual_laps():
    world = small_world()
    tyre = world.sets[0]
    wet = replace(world, rain=(False, False, False, True))
    assert sum(lap_cost(wet, tyre, index, index) for index in range(4)) == 48.5
    assert sum(lap_cost(world, tyre, index, index) for index in range(4)) == 40


def test_physical_life_and_inventory_are_hard_constraints():
    world = small_world()
    world = replace(world, sets=(replace(world.sets[0], usable_laps=1),
                                 replace(world.sets[1], usable_laps=2)))
    assert exact_oracle(world)["time_s"] is None
    score, reason = score_assignment(world, [0, 2], [1, 1])
    assert math.isinf(score)
    assert reason == "physical set reused"


def test_oracle_can_find_short_distance_multistop():
    world = small_world(horizon=6)
    world = replace(world, sets=(replace(world.sets[0], usable_laps=2),
                                 replace(world.sets[1], usable_laps=2),
                                 PhysicalSet(2, "SOFT", 0, 1, 0, 0, 0, usable_laps=2)))
    oracle = exact_oracle(world)
    assert oracle["time_s"] == 62  # 20 + 16 + 20 + two 3-second stops.
    assert [stop["offset"] for stop in oracle["stops"]] == [2, 4]


@pytest.mark.parametrize("family", ["linear", "finite_sets", "quadratic", "wear_cliff", "weather", "traffic"])
def test_dynamic_program_matches_exhaustive_enumeration(family):
    manifest = json.loads(MANIFEST_PATH.read_text())
    world = build_world(manifest["training_seeds"][0], family, manifest)
    world = replace(world, horizon=5, rain=world.rain[:5], traffic_delays_s=world.traffic_delays_s[:5])
    assert exact_oracle(world)["time_s"] == pytest.approx(brute_force(world), abs=1e-6)


def test_legacy_matching_cannot_invent_or_reuse_sets():
    world = small_world()
    impossible = {"compounds": ["MEDIUM", "HARD", "HARD"], "box_laps": [11, 13]}
    assert score_plan(world, impossible)["physical_feasible"] is False
    possible = {"compounds": ["MEDIUM", "HARD"], "box_laps": [11]}
    assert score_plan(world, possible)["time_s"] == 37  # One old lap before boxing.


def test_explicit_set_identity_is_scored_as_emitted_not_optimistically_replaced():
    world = small_world()
    world = replace(world, sets=world.sets + (replace(world.sets[1], index=2, pace_delta_s=1),))
    legacy = {"compounds": ["MEDIUM", "HARD"], "box_laps": [11]}
    explicit = {**legacy, "tyre_set_indices": [2]}
    assert score_plan(world, legacy)["time_s"] == 37
    assert score_plan(world, explicit)["time_s"] == 46


def test_impossible_outcomes_are_not_silently_dropped_from_summary():
    rows = [{"family": "linear", "split": "training", "regret_s": None,
             "score": {"physical_feasible": False}, "runtime_ms": 1,
             "reported_feasible_but_physically_impossible": True, "contracts": []}]
    summary = summarize(rows, [])["groups"]["all"]
    assert summary["physically_impossible"] == 1
    assert summary["finite_regret_count"] == 0
    assert summary["median_regret_s"] is None


def test_contract_detects_race_points_leaking_into_sprint():
    state = {"mode_profile": "sprint", "current_lap": 1, "total_laps": 2}
    plan = {"box_laps": [], "stops_remaining": 0,
            "position_probabilities": {"1": 1.0}, "points_expected": 25}
    errors = output_contracts({"recommended": plan}, state)
    assert any("session scoring" in error for error in errors)


def test_frozen_worlds_reproduce_independently_of_generation_order():
    manifest = json.loads(MANIFEST_PATH.read_text())
    first = build_world(2026091391, "weather", manifest)
    build_world(2026091301, "linear", manifest)
    assert build_world(2026091391, "weather", manifest) == first


def test_closed_loop_executes_actual_set_and_tracks_wear_after_a_stop():
    class FixedDecision:
        def new_engine(self):
            return None

        def compute(self, engine, state, history):
            current = state["tyre"]["compound"]
            plan = {"compounds": [current], "box_laps": [], "stops_remaining": 0}
            if current == "MEDIUM":
                plan.update(compounds=["MEDIUM", "HARD"], box_laps=[state["current_lap"]],
                            tyre_set_indices=[1], stops_remaining=1)
            return {"recommended": plan}, 0

    result = closed_loop(FixedDecision(), small_world())
    assert result["completed"]
    assert result["time_s"] == 37
    assert result["checkpoints"][0]["lap_time_s"] == 10  # Box after this old-tyre lap.
    assert result["checkpoints"][1]["lap_time_s"] == 8
    assert result["checkpoints"][0]["executed_set_index"] == 1
    assert all(step["executed_set_index"] is None for step in result["checkpoints"][1:])


def test_contradictory_infeasible_finish_instruction_is_recorded():
    result = {"recommended": {"feasible": False, "instruction": "Stay out to the finish.",
                               "box_laps": [], "stops_remaining": 0}}
    errors = output_contracts(result, {"current_lap": 1, "total_laps": 2, "mode_profile": "race"})
    assert "infeasible recommendation carries an unconditional finish instruction" in errors


def test_adapter_maps_remaining_life_and_total_recommendation_to_ea_fields():
    world = small_world()
    world = replace(world, sets=(replace(world.sets[0], age=5, usable_laps=2), world.sets[1]))
    state, _ = world_state(world)
    fitted = state["tyre_sets"][0]
    assert fitted["life_span_laps"] == 2  # EA: laps left in this tyre set.
    assert fitted["usable_life_laps"] == 7  # EA: max recommended total laps.
    assert world.sets[0].usable_laps == 2  # Physical scoring remains unchanged.


def test_box_lap_means_end_of_lap_and_cannot_add_a_phantom_lap_to_final_set():
    world = small_world(horizon=6)
    world = replace(world, sets=(replace(world.sets[0], usable_laps=1),
                                 replace(world.sets[1], usable_laps=5)))
    plan = {"compounds": ["MEDIUM", "HARD"], "box_laps": [11], "tyre_set_indices": [1]}
    score = score_plan(world, plan)
    assert score["physical_feasible"]
    assert score["time_s"] == 53  # 10 + 5*8 + 3.
