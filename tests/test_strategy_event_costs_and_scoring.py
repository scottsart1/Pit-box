"""Scenario oracles written before the event-cost and scoring fixes.

Expected points and pit sums come from explicit scenario rules, not from the
production scoring helpers. All state and storage belong to pytest fixtures.
"""
import numpy as np
import pytest

from pitwall.strategy import StrategyEngine


def _race(state, *, mode="race", phase="green", safety="none"):
    state.session_uid = 90913
    state.mode_profile = mode
    state.session_type = "Sprint" if mode == "sprint" else "Race"
    state.current_lap = 10
    state.total_laps = 40
    state.track_id = 11  # Monza: explicit 24-second green prior.
    state.player_car_index = 0
    state.player_position = 7
    state.active_cars = 20
    state.race_control_phase = phase
    state.safety_car = safety
    state.tyre.compound = "MEDIUM"
    state.tyre.age_laps = 9
    state.tyre.wear = [30.0] * 4
    state.completed_laps = [{
        "lap_num": 1, "lap_time_ms": 90_000, "valid": True,
        "compound": "HARD", "wear_start": [0]*4, "wear_end": [2]*4,
    }]
    state.tyre_sets = [
        {"index": i, "compound": name, "available": True,
         "wear_pct": 0, "usable_life_laps": 45}
        for i, name in enumerate(["SOFT", "MEDIUM", "HARD", "SOFT", "HARD"])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,position,points", [
    ("sprint", 1, 8), ("sprint", 7, 2), ("sprint", 8, 1),
    ("sprint", 9, 0), ("race", 1, 25), ("race", 7, 6), ("race", 10, 1),
])
async def test_classification_and_distribution_use_session_points(stack, mode, position, points):
    store, _, engine, *_ = stack
    await store.mutate(lambda s: _race(s, mode=mode))
    state = await store.snapshot_analysis()
    state["player_position"] = position
    plan = {"stops_remaining": 0, "projected_time_s": 1000,
            "projected_rejoin_position": position}
    engine._annotate_finish_projection(plan, state, [], 0.4, True)
    distribution = engine._position_distribution(plan, state, [], np.full(80, 1000.0))
    assert plan["projected_points"] == points
    assert distribution["points_expected"] == points


@pytest.mark.asyncio
async def test_championship_uses_finish_not_rejoin_and_keeps_expected_value(stack):
    store, _, engine, *_ = stack
    await store.update(mode_profile="sprint", player_position=7, strategy={"plans": [{
        "projected_rejoin_position": 12, "projected_finish_position": 5,
        "points_expected": 3.5, "stops_remaining": 1,
        "instruction": "Box lap 10 for HARD", "confidence": "low",
    }]})
    result = await engine.championship_scenario()
    assert result["plans"][0]["projected_position"] == 5
    assert result["plans"][0]["projected_points"] == 4
    assert result["plans"][0]["points_expected"] == 3.5
    assert result["current_points_if_held"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["Short", "Medium", "Medium Long", "Long", "Full"])
async def test_normal_game_distance_does_not_reduce_race_points(stack, label):
    store, _, engine, *_ = stack
    await store.update(mode_profile="race", session_length_label=label,
                       player_position=1, strategy={"plans": []})
    assert (await engine.championship_scenario())["current_points_if_held"] == 25


@pytest.mark.parametrize("phase,safety", [
    ("safety_car_ending", "full"), ("vsc_ending", "virtual"),
])
def test_specific_ending_event_outranks_lingering_general_status(phase, safety):
    result = StrategyEngine._neutralisation({
        "track_id": 11, "current_lap": 10, "total_laps": 40,
        "race_control_phase": phase, "safety_car": safety,
    })
    assert result["phase"] == phase
    assert result["effective_pit_loss_s"] == pytest.approx(24 * 0.82)


@pytest.mark.asyncio
async def test_only_current_stop_receives_safety_car_discount(stack):
    store, _, engine, *_ = stack
    await store.mutate(lambda s: _race(s, phase="safety_car", safety="full"))
    state = await store.snapshot_analysis()
    engine.compute(state)
    two = [p for p in engine._candidate_pool if p["stops_remaining"] == 2]
    assert two, "scenario must exercise an actual two-stop candidate"
    for plan in two:
        pit_cost = (plan["projected_time_s"] - plan["traffic_cost_s"]
                    - sum(s["expected_time_s"] for s in plan["stint_models"]))
        expected = 24 + (24 * 0.46 if plan["box_laps"][0] == 10 else 24)
        assert pit_cost == pytest.approx(expected, abs=0.02), plan["box_laps"]


def test_raw_lane_duration_is_not_a_measured_net_pit_loss():
    state = {"track_id": 11, "completed_laps": [{"pit_lane_time_ms": 34000}]}
    # No independent main-track traversal measurement is present, so retain
    # the explicitly labelled circuit prior rather than claiming 34 s lost.
    assert StrategyEngine._base_pit_loss(state) == 24


@pytest.mark.asyncio
async def test_rival_age_resets_after_every_projected_stop(stack):
    _, _, engine, *_ = stack
    state = {
        "player_car_index": 0, "player_position": 1, "track_id": 11,
        "race_control_phase": "green", "safety_car": "none",
        "current_lap": 10, "drivers": [{
            "car_idx": 1, "position": 2, "tyre_compound": "SOFT",
            "tyre_age": 16, "gap_to_player_s": 1,
            "lap_history": [{"lap_ms": 90_000, "valid_flags": 1}],
        }],
    }
    projection = engine._project_rival_finish_times(state, {}, 40, 90, 24)[0]
    # Explicit reference schedule: soft life 17, current age16; stops after
    # 1, 18 and 35 of the 40 remaining laps. Observed pace90 belongs to age16.
    ages = [16] + list(range(17)) + list(range(17)) + list(range(5))
    expected = 1 + sum(90 + projection["deg_s_per_lap"] * (age - 16) for age in ages) + 3 * 24
    assert projection["likely_remaining_stops"] == 3
    assert projection["finish_time_s"] == pytest.approx(expected, abs=0.01)


def test_retired_cars_do_not_cost_rejoin_positions():
    state = {"player_position": 5, "active_cars": 20, "drivers": [
        {"position": 6, "gap_to_player_s": 2, "result_label": "retired"},
        {"position": 7, "gap_to_player_s": 3, "result_label": "did not finish"},
        {"position": 8, "gap_to_player_s": 4, "result_label": "disqualified"},
    ]}
    assert StrategyEngine._rejoin_position(state, 24) == 5


@pytest.mark.asyncio
async def test_pit_cycle_recovery_does_not_require_on_track_overtakes(stack):
    _, _, engine, *_ = stack
    state = {"player_position": 5, "active_cars": 12, "current_lap": 10}
    plan = {"stops_remaining": 1, "box_laps": [10], "projected_time_s": 1000,
            "projected_rejoin_position": 11,
            "stint_models": [{"lap_times_s": [90.0]*10}]}
    rivals = [
        {"position": p, "finish_time_s": 950 if p < 5 else 1020,
         "current_gap_s": p-5, "pace_s": 90,
         "likely_remaining_stops": 1, "likely_stop_offsets_laps": [5],
         "pit_stop_costs_s": [24], "confidence": "medium"}
        for p in range(1, 13) if p != 5
    ]
    engine._annotate_finish_projection(plan, state, rivals, 1.0, True)
    # Six cars that pass us during our stop lose those positions again in
    # their own later pit stops; no passing manoeuvre is needed.
    assert plan["projected_finish_position"] == 5
    dist = engine._position_distribution(plan, state, rivals, np.full(80, 1000))
    assert dist["expected_finish_position"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("active_cars", [0, 3, 24])
async def test_partial_field_does_not_invent_a_podium_or_high_confidence(stack, active_cars):
    _, _, engine, *_ = stack
    state = {"player_position": 18, "active_cars": active_cars, "current_lap": 10}
    rivals = [{"position": p, "finish_time_s": 900, "confidence": "high"}
              for p in [1, 2]]
    plan = {"stops_remaining": 0, "projected_time_s": 1000,
            "projected_rejoin_position": 18}
    engine._annotate_finish_projection(plan, state, rivals, 0.4, True)
    assert plan["projected_finish_position"] == 18
    assert plan["finish_projection_confidence"] == "low"
    dist = engine._position_distribution(plan, state, rivals, np.full(80, 1000))
    assert dist["expected_finish_position"] == 18
    assert plan["field_size_source"] == ("reported" if active_cars == 24 else "observed_lower_bound")


@pytest.mark.asyncio
async def test_unknown_field_count_cannot_claim_high_confidence_even_with_p1(stack):
    _, _, engine, *_ = stack
    state = {"player_position": 1, "active_cars": 0}
    rivals = [{"position": 2, "finish_time_s": 1100, "confidence": "high"}]
    plan = {"stops_remaining": 0, "projected_time_s": 1000, "projected_rejoin_position": 1}
    engine._annotate_finish_projection(plan, state, rivals, 0.4, True)
    assert plan["projected_finish_position"] == 1
    assert plan["finish_projection_confidence"] == "low"
    assert plan["field_size_source"] == "observed_lower_bound"


@pytest.mark.asyncio
async def test_player_personal_degradation_is_not_rival_evidence(stack):
    _, _, engine, *_ = stack
    state = {"player_car_index": 0, "player_position": 1,
             "current_lap": 10, "track_id": 11,
             "drivers": [{"car_idx": 1, "position": 2,
                          "tyre_compound": "SOFT", "tyre_age": 6,
                          "gap_to_player_s": 1,
                          "lap_history": [{"lap_ms": 90_000, "valid_flags": 1}]}]}
    before = engine._project_rival_finish_times(state, {}, 10, 90, 24)[0]
    personal = {"compounds": {"SOFT": {"sample_size": 30, "slope_s_per_lap": 0.8}}}
    after = engine._project_rival_finish_times(state, personal, 10, 90, 24)[0]
    assert after["finish_time_s"] == before["finish_time_s"]
    assert after["deg_samples"] == 0


@pytest.mark.asyncio
async def test_infeasible_finish_is_not_spoken_as_a_safe_finish(stack):
    store, _, engine, *_ = stack
    await store.mutate(_race)
    state = await store.snapshot_analysis()
    state["tyre"]["wear"] = [99]*4
    state["tyre_sets"] = [{"compound": "HARD", "available": False}]
    result = engine.compute(state)
    assert result["recommended"]["feasible"] is False
    assert "no feasible" in result["recommended"]["instruction"].lower()
    assert "protects projected" not in result["recommended"]["rationale"].lower()
