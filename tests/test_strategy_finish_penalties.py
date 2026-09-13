"""D25: independently specified pending-penalty classification clocks."""

import json
from copy import deepcopy

import numpy as np
import pytest

from pitwall.strategy import StrategyEngine


def race(*, player_position=1, gap=1.0):
    # The independently generated driver slope matches track11's existing
    # rival prior. This tests penalty arithmetic without changing physics.
    slope = .0828
    laps = [{"session_uid": 1, "restart_epoch": 0, "timeline_epoch": 0,
             "lap_num": age, "lap_time_ms": round((90 + slope * age) * 1000),
             "valid": True, "compound": "MEDIUM", "tyre_age_start": age-1,
             "tyre_age_end": age, "wear_start": [age-1.] * 4, "wear_end": [float(age)] * 4,
             "fuel_start_kg": 20., "fuel_end_kg": 20., "weather": "Clear", "setup": {}, "pit_status": 0}
            for age in range(2, 11)]
    opponent = {"car_idx": 1, "position": 3-player_position, "name": "Equal pace",
                "current_lap": 11, "tyre_compound": "MEDIUM", "tyre_age": 10,
                "gap_to_player_s": gap, "penalties_s": 0,
                "lap_history": [{"lap_num": lap["lap_num"], "lap_ms": lap["lap_time_ms"], "valid_flags": 1}
                                for lap in laps]}
    state = {"session_uid": 1, "restart_epoch": 0, "timeline_epoch": 0,
             "current_lap": 11, "total_laps": 15, "mode_profile": "sprint", "session_type": "Sprint",
             "track_id": 11, "weather": "Clear", "race_control_phase": "green", "safety_car": "none",
             "player_car_index": 0, "player_position": player_position, "active_cars": 2,
             "tyre": {"compound": "MEDIUM", "age_laps": 10, "wear": [10.] * 4},
             "car_setup": {}, "completed_laps": laps, "tyre_sets": [], "penalties_s": 0,
             "drivers": [{"car_idx": 0, "position": player_position, "pit_stops": 0, "penalties_s": 0}, opponent]}
    history = {"compounds": {"MEDIUM": {"slope_s_per_lap": slope, "sample_size": 20,
               "max_wear_per_lap_pct": 1., "wear_sample_size": 20, "wheel_wear_per_lap_pct": [1.] * 4}}}
    return state, history


def signature(plan):
    return tuple(plan["box_laps"]), tuple(plan["compounds"])


def no_stop(engine):
    return next(plan for plan in engine._candidate_pool if plan["stops_remaining"] == 0)


def test_current_player_penalty_adds_once_to_every_plan_and_shared_draw():
    state, history = race()
    # No field-order change should affect which candidates are shortlisted in
    # this translation experiment; field classification has its own tests.
    state.update(active_cars=1, drivers=[state["drivers"][0]])
    engine = StrategyEngine(None, None)
    baseline = engine.compute(state, history)
    before = {signature(plan): deepcopy(plan) for plan in engine._candidate_pool}
    state["penalties_s"] = state["drivers"][0]["penalties_s"] = 5
    original = deepcopy(state)
    result = engine.compute(state, history)
    assert {plan["stops_remaining"] for plan in engine._candidate_pool} == {0, 1, 2, 3}
    assert {signature(plan) for plan in engine._candidate_pool} == set(before)
    for plan in engine._candidate_pool:
        prior = before[signature(plan)]
        for key in ("projected_time_s", "risk_adjusted_time_s", "projected_time_without_driver_feedback_s",
                    "risk_adjusted_time_without_driver_feedback_s"):
            assert plan[key] - prior[key] == pytest.approx(5), key
        assert plan["projected_time_before_penalties_s"] == prior["projected_time_s"]
        assert plan["stint_models"] == prior["stint_models"]
        assert plan["pit_stop_costs_s"] == prior["pit_stop_costs_s"]
        assert plan["pending_finish_penalty_s"] == 5
        assert plan["feasible"] == prior["feasible"]
        if "monte_carlo" in prior and "monte_carlo" in plan:
            for key in ("mean_s", "p25_s", "p50_s", "p75_s", "p90_s"):
                assert plan["monte_carlo"][key] - prior["monte_carlo"][key] == pytest.approx(5)
            assert plan["monte_carlo"]["uncertainty_s"] == prior["monte_carlo"]["uncertainty_s"]
        old_draws = engine._monte_carlo_profile(prior, state, 22, 80)["_outcome_times_s"]
        new_draws = engine._monte_carlo_profile(plan, state, 22, 80)["_outcome_times_s"]
        assert np.allclose(new_draws-old_draws, 5)
    assert state == original
    assert result["recommended"]["projected_time_s"] - baseline["recommended"]["projected_time_s"] == pytest.approx(5)


@pytest.mark.parametrize("player_position,gap,player_penalty,rival_penalty,expected_position", [
    (1, 1, 0, 0, 1), (1, 1, 5, 0, 2), (1, 1, 5, 5, 1),
    (2, -1, 0, 0, 2), (2, -1, 0, 5, 1), (2, -1, 5, 5, 2),
])
def test_actual_compute_penalties_change_classification_on_common_axis(
    player_position, gap, player_penalty, rival_penalty, expected_position,
):
    state, history = race(player_position=player_position, gap=gap)
    state["penalties_s"] = player_penalty
    state["drivers"][1]["penalties_s"] = rival_penalty
    engine = StrategyEngine(None, None)
    result = engine.compute(state, history)
    plan = no_stop(engine)
    rival = result["rival_finish_projections"][0]
    assert rival["finish_time_s"] - plan["projected_time_s"] == pytest.approx(
        gap + rival_penalty - player_penalty, abs=.01,
    )
    assert plan["projected_finish_position"] == expected_position
    assert plan["position_probabilities"] == {f"P{expected_position}": 1.0}
    assert rival["finish_time_s"] - rival["finish_time_before_penalties_s"] == pytest.approx(rival_penalty)


def test_cleared_pending_counter_does_not_readd_served_stop_or_penalty_history():
    state, history = race()
    engine = StrategyEngine(None, None)
    baseline = engine.compute(state, history)["recommended"]
    state["penalties_s"] = state["drivers"][0]["penalties_s"] = 5
    assert engine.compute(state, history)["recommended"]["pending_finish_penalty_s"] == 5
    state.update(penalties_s=0, pit_lane_time_ms=30000, pit_stop_time_ms=8000,
                 penalty_history=[{"penalties_s": 5}], unserved_stop_go_penalties=0)
    state["drivers"][0]["pit_stop_history"] = [{"stop_time_ms": 8000}]
    # The mirrored driver counter may lag. Explicit top-level zero wins.
    cleared = engine.compute(state, history)["recommended"]
    assert cleared["pending_finish_penalty_s"] == 0
    assert cleared["projected_time_s"] == baseline["projected_time_s"]


def test_partial_snapshot_uses_only_identified_player_counter():
    state, history = race()
    state.pop("penalties_s")
    state["drivers"][0]["penalties_s"] = 5
    state["drivers"][1]["penalties_s"] = 10
    result = StrategyEngine(None, None).compute(state, history)
    assert result["finish_penalty"]["pending_s"] == 5
    assert result["rival_finish_projections"][0]["pending_finish_penalty_s"] == 10


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf"), -float("inf"), "bad", -5, 256, 5.5, True])
def test_invalid_penalty_counter_is_not_a_time_advantage_or_nonfinite_output(invalid):
    state, history = race()
    engine = StrategyEngine(None, None)
    baseline = engine.compute(state, history)
    state["penalties_s"] = invalid
    state["drivers"][1]["penalties_s"] = invalid
    result = engine.compute(state, history)
    assert result["finish_penalty"]["pending_s"] == 0
    assert result["finish_penalty"]["status"] != "reported"
    assert result["recommended"]["projected_time_s"] == baseline["recommended"]["projected_time_s"]
    assert result["rival_finish_projections"][0]["finish_time_s"] == baseline["rival_finish_projections"][0]["finish_time_s"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("rival_penalty,expected_position", [(0, 2), (5, 1)])
def test_penalty_classification_gain_bypasses_overtake_cap_without_inventing_pass(rival_penalty, expected_position):
    engine = StrategyEngine(None, None)
    state = {"player_position": 2, "active_cars": 2, "mode_profile": "race", "current_lap": 10}
    # A stationary physical oracle: rival finishes100s, player102s. The
    # player has no pace advantage and cannot pass, regardless of tyre choice.
    plan = {"stops_remaining": 1, "box_laps": [10], "projected_time_s": 102,
            "projected_time_before_penalties_s": 102, "pending_finish_penalty_s": 0,
            "projected_rejoin_position": 2, "pit_stop_costs_s": [22],
            "stint_models": [{"lap_times_s": [90, 90]}]}
    rivals = [{"position": 1, "finish_time_s": 100+rival_penalty,
               "finish_time_before_penalties_s": 100, "pending_finish_penalty_s": rival_penalty,
               "current_gap_s": -2, "pace_s": 90}]
    engine._annotate_finish_projection(plan, state, rivals, 1.0, True)
    assert plan["projected_finish_position"] == expected_position
    assert plan["projected_rejoin_position"] == 2
    assert plan["expected_positions_recovered"] == 0
    assert plan["penalty_positions_recovered"] == (1 if rival_penalty else 0)
    distribution = engine._position_distribution(plan, state, rivals, np.array([101.9, 102, 102.1]))
    assert distribution["position_probabilities"] == {f"P{expected_position}": 1.0}
    # If the candidate finishes after the penalized rival, no penalty gain is
    # available just because the rival has a nonzero counter.
    late = engine._position_distribution(plan, state, rivals, np.array([106.0]))
    assert late["position_probabilities"] == {"P2": 1.0}


def test_unreported_repair_or_service_duration_does_not_train_the_pit_prior():
    state, history = race()
    engine = StrategyEngine(None, None)
    baseline = engine.compute(state, history)
    state.update(unserved_drive_through_penalties=1, unserved_stop_go_penalties=1,
                 damage={"front_left_wing": 75}, pit_stop_time_ms=60000)
    result = engine.compute(state, history)
    assert result["pit_loss_s"] == baseline["pit_loss_s"]
    assert result["recommended"]["projected_time_s"] == baseline["recommended"]["projected_time_s"]
    assert "unreported repair" in result["neutralisation"]["pit_loss_scope"]


@pytest.mark.asyncio
async def test_what_if_prices_pending_penalty_on_the_same_best_plan_axis(stack):
    store, _, engine, *_ = stack
    state, _ = race()
    await store.update(**{key: value for key, value in state.items() if key != "drivers"})
    baseline = await engine.what_if("box lap 12 hard")
    await store.update(penalties_s=5)
    penalized = await engine.what_if("box lap 12 hard")
    assert penalized["projected_time_s"] - baseline["projected_time_s"] == pytest.approx(5)
    assert penalized["risk_adjusted_time_s"] - baseline["risk_adjusted_time_s"] == pytest.approx(5)
    assert penalized["delta_to_best_s"] == baseline["delta_to_best_s"]
    assert penalized["pending_finish_penalty_s"] == 5


@pytest.mark.asyncio
async def test_held_radio_instruction_refreshes_current_penalty_metadata(stack):
    store, _, engine, *_ = stack
    await store.update(session_uid=42, session_type="Race", mode_profile="race",
                       current_lap=14, total_laps=28, player_position=5, active_cars=20, track_id=13,
                       tyre={"compound": "HARD", "age_laps": 12, "wear": [40] * 4},
                       tyre_sets=[{"compound": "MEDIUM", "available": True},
                                  {"compound": "SOFT", "available": True}], penalties_s=0)
    previous = await engine.recompute()
    await store.update(current_lap=15, player_position=4, penalties_s=5,
                       tyre={"compound": "HARD", "age_laps": 13, "wear": [41] * 4})
    result = await engine.recompute()
    held = result["recommended"]
    current = next(plan for plan in engine._candidate_pool
                   if engine._radio_signature(plan) == engine._radio_signature(held))
    assert result["stability"]["held"]
    assert held["instruction"] == previous["recommended"]["instruction"]
    assert held["pending_finish_penalty_s"] == current["pending_finish_penalty_s"] == 5
    assert held["finish_penalty"] == result["finish_penalty"] == current["finish_penalty"]
    assert held["projected_time_s"] - held["projected_time_before_penalties_s"] == pytest.approx(5)
    assert held["penalty_positions_recovered"] == current["penalty_positions_recovered"]
    assert "5s of pending time penalties" in held["rationale"]
