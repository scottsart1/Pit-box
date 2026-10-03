"""Fuel, safety-car option value, projected traffic and the decision objective.

Each subsystem is measured against a stated generating assumption, and each one
is checked to leave the engine exactly as it was when its evidence is missing.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from pitwall.strategy import (
    DEFAULT_SAFETY_CAR_RATE,
    POSITION_VALUE_POINTS,
    TRACK_SAFETY_CAR_RATE,
    UTILITY_RESOLUTION,
    StrategyEngine,
    finish_value,
    points_for_position,
)


def engine():
    return StrategyEngine.__new__(StrategyEngine)


def fuel_lap(number, start_kg, end_kg):
    return {
        "lap_num": number, "lap_time_ms": 92_000, "valid": True,
        "compound": "MEDIUM", "tyre_age_end": number, "tyre_age_start": number - 1,
        "fuel_start_kg": start_kg, "fuel_end_kg": end_kg,
        "wear_start": [(number - 1) * 2.0] * 4, "wear_end": [number * 2.0] * 4,
    }


def fuel_state(burn=1.8, fuel=60.0, laps=6, **overrides):
    completed = [
        fuel_lap(n, fuel + burn * (laps - n + 1), fuel + burn * (laps - n))
        for n in range(1, laps + 1)
    ]
    return {
        "mode_profile": "race", "track_id": 7, "current_lap": laps + 1,
        "total_laps": 40, "fuel_kg": fuel, "completed_laps": completed,
        "tyre": {"compound": "MEDIUM", "age_laps": laps, "wear": [20.0] * 4},
        **overrides,
    }


# --------------------------------------------------------------------------
# Fuel
# --------------------------------------------------------------------------


def test_burn_rate_is_measured_from_the_player_s_own_laps():
    model = StrategyEngine._fuel_model(fuel_state(burn=1.8))
    assert model["available"] is True
    assert model["burn_kg_per_lap"] == pytest.approx(1.8, abs=1e-6)
    assert model["source"] == "measured_lap_fuel"
    assert model["gain_s_per_lap"] == pytest.approx(1.8 * 0.030, abs=1e-6)


def test_burn_rate_falls_back_to_the_reported_fuel_range():
    state = {"fuel_kg": 45.0, "fuel_remaining_laps": 25.0, "completed_laps": []}
    model = StrategyEngine._fuel_model(state)
    assert model["source"] == "fuel_remaining_laps"
    assert model["burn_kg_per_lap"] == pytest.approx(1.8)


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"completed_laps": []},
        {"fuel_kg": 0.0, "fuel_remaining_laps": 10.0},
        {"fuel_kg": 50.0, "fuel_remaining_laps": 0.0},
        {"fuel_kg": 50.0, "fuel_remaining_laps": 0.1},  # implausible 500 kg/lap
    ],
)
def test_unusable_fuel_telemetry_prices_no_fuel_effect(state):
    model = StrategyEngine._fuel_model(state)
    assert model["available"] is False
    assert model["burn_kg_per_lap"] is None


def test_a_burning_car_projects_quicker_laps_as_the_stint_runs():
    model = StrategyEngine._fuel_model(fuel_state(burn=2.0))
    tyre_only = engine()._simulate_stint(
        fuel_state(burn=2.0), "MEDIUM", 10, 0, 0.0, 90.0, {}, 1.0,
    )
    fuelled = StrategyEngine._with_fuel(tyre_only, model, 0)
    gains = [
        bare - fuel
        for fuel, bare in zip(fuelled["lap_times_s"], tyre_only["lap_times_s"])
    ]
    assert gains == sorted(gains), "the fuel credit must grow as fuel burns off"
    assert gains[-1] - gains[0] == pytest.approx(2.0 * 0.030 * 9, abs=1e-3)
    assert fuelled["expected_time_s"] < tyre_only["expected_time_s"]
    assert fuelled["fuel_correction_s"] < 0


def test_the_stint_model_itself_stays_a_tyre_model():
    """Fuel is additive and offset-dependent, so it is applied outside.

    Keeping it out of _simulate_stint is what lets thousands of candidate
    stints share one cached tyre projection wherever they run in the race.
    """
    state = fuel_state(burn=2.0)
    stint = engine()._simulate_stint(state, "MEDIUM", 8, 0, 0.0, 90.0, {}, 1.0)
    assert "fuel_correction_s" not in stint


def test_absent_fuel_telemetry_returns_the_stint_untouched():
    state = fuel_state(burn=2.0)
    stint = engine()._simulate_stint(state, "MEDIUM", 8, 0, 0.0, 90.0, {}, 1.0)
    for model in (None, {}, {"available": False}):
        assert StrategyEngine._with_fuel(stint, model, 0) is stint


def test_the_fuel_credit_never_exceeds_the_fuel_actually_carried():
    """A long projection on 20 kg cannot keep getting lighter forever."""
    model = StrategyEngine._fuel_model(fuel_state(burn=2.0, fuel=20.0))
    deltas = StrategyEngine._fuel_deltas_s(model, 0, 40)
    assert max(abs(value) for value in deltas) <= 20.0 * 0.030 + 1e-9
    assert deltas[-1] == pytest.approx(-20.0 * 0.030)


def test_a_later_stint_is_corrected_for_a_lighter_car_than_an_earlier_one():
    model = StrategyEngine._fuel_model(fuel_state(burn=2.0))
    early = StrategyEngine._fuel_deltas_s(model, 0, 6)
    late = StrategyEngine._fuel_deltas_s(model, 20, 6)
    assert sum(late) < sum(early)


# --------------------------------------------------------------------------
# Safety-car hazard and option value
# --------------------------------------------------------------------------


def neutralisation(base=22.0, immediate=True):
    return {
        "base_pit_loss_s": base,
        "effective_pit_loss_s": base,
        "pit_this_lap_available": immediate,
        "phase": "green",
    }


def test_the_hazard_is_a_per_lap_probability_from_a_circuit_prior():
    model = StrategyEngine._safety_car_hazard({"track_id": 5, "total_laps": 78})
    assert model["circuit_known"] is True
    assert model["expected_deployments_per_race"] == TRACK_SAFETY_CAR_RATE[5]
    assert model["per_lap_probability"] == pytest.approx(
        TRACK_SAFETY_CAR_RATE[5] / 78, abs=1e-5
    )


def test_an_unknown_circuit_falls_back_and_says_so():
    model = StrategyEngine._safety_car_hazard({"track_id": 999, "total_laps": 50})
    assert model["circuit_known"] is False
    assert model["expected_deployments_per_race"] == DEFAULT_SAFETY_CAR_RATE


def test_no_future_stop_carries_no_option_value():
    state = {"track_id": 5, "total_laps": 50, "current_lap": 10}
    for box_laps in ([], [10]):
        option = StrategyEngine._neutralisation_option_value_s(
            state, box_laps, neutralisation()
        )
        assert option["option_value_s"] == 0.0


def test_option_value_grows_with_the_window_to_the_next_stop():
    state = {"track_id": 5, "total_laps": 50, "current_lap": 10}
    near = StrategyEngine._neutralisation_option_value_s(state, [14], neutralisation())
    far = StrategyEngine._neutralisation_option_value_s(state, [34], neutralisation())
    assert 0 < near["option_value_s"] < far["option_value_s"]
    assert near["window_laps"] == 4 and far["window_laps"] == 24


def test_the_window_is_measured_to_the_next_stop_not_the_last():
    """A first stop already spends the flexibility the later ones cannot hold."""
    state = {"track_id": 5, "total_laps": 50, "current_lap": 10}
    option = StrategyEngine._neutralisation_option_value_s(
        state, [14, 34], neutralisation()
    )
    assert option["window_laps"] == 4


def test_a_stop_taken_now_is_priced_by_its_actual_phase_not_a_hazard():
    state = {"track_id": 5, "total_laps": 50, "current_lap": 10}
    option = StrategyEngine._neutralisation_option_value_s(
        state, [10, 30], neutralisation(immediate=True)
    )
    # The immediate stop is excluded; only the lap-30 stop holds an option.
    assert option["window_laps"] == 20


def test_option_value_is_zero_without_a_race_distance():
    state = {"track_id": 5, "total_laps": 0, "current_lap": 10}
    option = StrategyEngine._neutralisation_option_value_s(
        state, [30], neutralisation()
    )
    assert option["option_value_s"] == 0.0


# --------------------------------------------------------------------------
# The decision objective
# --------------------------------------------------------------------------


def test_finish_value_is_strictly_decreasing_in_position():
    values = [finish_value(position, 20) for position in range(1, 21)]
    assert all(a > b for a, b in pairwise(values))


def test_points_dominate_but_places_outside_them_still_count():
    assert finish_value(10, 20) > finish_value(11, 20)
    assert finish_value(11, 20) - finish_value(12, 20) == pytest.approx(
        POSITION_VALUE_POINTS
    )
    assert finish_value(1, 20) - finish_value(2, 20) > POSITION_VALUE_POINTS


def test_a_back_of_the_field_race_still_has_a_gradient():
    """Every plan scores zero points; the ranking must still have a signal."""
    assert finish_value(17, 20) > finish_value(18, 20)


def test_balanced_utility_is_the_mean_value_of_the_distribution():
    positions = np.array([4, 4, 5, 6])
    result = StrategyEngine._decision_utility(positions, 20, "race", "balanced")
    expected = np.mean([finish_value(int(p), 20, "race") for p in positions])
    assert result["decision_utility"] == pytest.approx(expected, abs=1e-4)
    assert result["expected_value_points"] == pytest.approx(expected, abs=1e-4)


def test_conservative_weights_the_bad_tail_and_aggressive_the_good_one():
    positions = np.array([2] * 25 + [8] * 50 + [15] * 25)
    balanced = StrategyEngine._decision_utility(positions, 20, "race", "balanced")
    careful = StrategyEngine._decision_utility(positions, 20, "race", "conservative")
    bold = StrategyEngine._decision_utility(positions, 20, "race", "aggressive")
    assert careful["decision_utility"] < balanced["decision_utility"]
    assert bold["decision_utility"] > balanced["decision_utility"]
    assert careful["tail_value_points"] < bold["tail_value_points"]


def test_appetite_is_one_scale_so_a_safe_plan_and_a_gamble_can_be_compared():
    """The two appetites used to be separate sort keys and incomparable."""
    locked = np.array([5] * 100)
    gamble = np.array([2] * 40 + [9] * 60)
    careful = [
        StrategyEngine._decision_utility(p, 20, "race", "conservative")["decision_utility"]
        for p in (locked, gamble)
    ]
    bold = [
        StrategyEngine._decision_utility(p, 20, "race", "aggressive")["decision_utility"]
        for p in (locked, gamble)
    ]
    assert careful[0] > careful[1], "a conservative driver prefers the locked result"
    assert bold[1] > bold[0], "an aggressive driver prefers the gamble"


def test_utility_separates_plans_that_share_a_central_position():
    """The defect the objective exists to fix.

    Both plans centre on P4. One has a real chance of P2 and the integer
    ranking scored them identically.
    """
    locked = np.array([4] * 100)
    upside = np.array([2] * 35 + [4] * 40 + [5] * 25)
    scores = [
        StrategyEngine._decision_utility(p, 20, "race", "balanced")["decision_utility"]
        for p in (locked, upside)
    ]
    assert scores[1] > scores[0]
    assert abs(scores[1] - scores[0]) > UTILITY_RESOLUTION


def test_sprint_scoring_is_used_when_the_session_is_a_sprint():
    positions = np.array([3, 3, 3])
    race = StrategyEngine._decision_utility(positions, 20, "race", "balanced")
    sprint = StrategyEngine._decision_utility(positions, 20, "sprint", "balanced")
    assert race["decision_utility"] > sprint["decision_utility"]
    assert points_for_position(3, "sprint") < points_for_position(3, "race")


def test_an_empty_distribution_does_not_explode():
    result = StrategyEngine._decision_utility(
        np.array([], dtype=int), 20, "race", "balanced"
    )
    assert result["decision_utility"] == 0.0


# --------------------------------------------------------------------------
# Traffic against the grid as it will be
# --------------------------------------------------------------------------


def rival(name, gap, lap_times):
    return {"driver": name, "current_gap_s": gap, "lap_times_s": lap_times}


def test_gaps_are_advanced_along_both_cars_projected_laps():
    rivals = [rival("Slow", 5.0, [92.0] * 6), rival("Quick", 5.0, [89.0] * 6)]
    gaps = StrategyEngine._projected_gaps_at(rivals, [90.0] * 6, 5)
    # Five laps at +2.0 s/lap puts the slow car 15 s behind; the quick one
    # arrives 5 s ahead of where it started.
    assert gaps[0] == pytest.approx(5.0 + 10.0)
    assert gaps[1] == pytest.approx(5.0 - 5.0)


def test_an_immediate_stop_keeps_the_measured_live_gaps():
    rivals = [rival("A", 5.0, [92.0] * 6)]
    assert StrategyEngine._projected_gaps_at(rivals, [90.0] * 6, 1) is None
    assert StrategyEngine._projected_gaps_at(rivals, [90.0] * 6, 0) is None


def test_projections_too_short_for_the_window_fall_back_rather_than_extrapolate():
    rivals = [rival("A", 5.0, [92.0] * 3)]
    assert StrategyEngine._projected_gaps_at(rivals, [90.0] * 9, 6) is None
    assert StrategyEngine._projected_gaps_at(rivals, [90.0] * 3, 6) is None


def test_a_rival_missing_a_gap_disables_projection_for_the_whole_field():
    rivals = [rival("A", 5.0, [92.0] * 6), {"driver": "B", "lap_times_s": [92.0] * 6}]
    assert StrategyEngine._projected_gaps_at(rivals, [90.0] * 6, 5) is None


def test_rejoin_uses_the_supplied_gaps_when_they_are_given():
    state = {"player_position": 5, "active_cars": 20, "drivers": [
        {"gap_to_player_s": 1.0}, {"gap_to_player_s": 2.0}, {"gap_to_player_s": 3.0},
    ]}
    live = StrategyEngine._rejoin_position(state, 22.0)
    projected = StrategyEngine._rejoin_position(state, 22.0, [40.0, 50.0, 60.0])
    assert live == 8, "three cars inside the pit loss on the live grid"
    assert projected == 5, "none of them are inside it at the projected stop"


def test_traffic_cost_prices_a_distant_stop_against_the_projected_grid():
    state = {"player_position": 5, "active_cars": 20, "current_lap": 10,
             "total_laps": 50, "race_control_phase": "green", "drivers": [
                 {"gap_to_player_s": 1.0}, {"gap_to_player_s": 2.0},
             ]}
    rivals = [rival("A", 1.0, [95.0] * 8), rival("B", 2.0, [95.0] * 8)]
    live_cost, live_rejoin = StrategyEngine._traffic_cost(state, 22.0, 1)
    ahead_cost, ahead_rejoin = StrategyEngine._traffic_cost(
        state, 22.0, 1, rivals, [90.0] * 8, 6
    )
    # Both rivals lose 5 s a lap, so by the stop they are far outside the pit
    # loss and cost no track position at all.
    assert live_rejoin == 7 and live_cost > 0
    assert ahead_rejoin == 5 and ahead_cost == 0


def test_a_no_stop_plan_is_still_charged_no_traffic():
    state = {"player_position": 5, "active_cars": 20, "drivers": []}
    assert StrategyEngine._traffic_cost(state, 22.0, 0) == (0.0, 5)


def test_two_stints_at_different_race_points_are_corrected_differently():
    """A later stint runs on a lighter car and must not reuse an earlier one."""
    model = StrategyEngine._fuel_model(fuel_state(burn=2.0))
    tyre_only = engine()._simulate_stint(
        fuel_state(burn=2.0), "MEDIUM", 6, 0, 0.0, 90.0, {}, 1.0,
    )
    early = StrategyEngine._with_fuel(tyre_only, model, 0)
    late = StrategyEngine._with_fuel(tyre_only, model, 20)
    assert late["expected_time_s"] < early["expected_time_s"]
    assert late["lap_times_s"] != early["lap_times_s"]


def test_a_full_plan_prices_its_later_stints_on_a_lighter_car():
    """Through compute(), not just the stint model in isolation.

    The same race is planned with and without usable fuel telemetry. Every
    candidate covers the same laps and burns the same fuel, so the whole
    difference is the mass that the projection previously never lost.
    """
    def race(fuel=True):
        state = fuel_state(burn=2.0, laps=4)
        if not fuel:
            # Strip only the fuel fields. The laps themselves must stay, or the
            # pace baseline moves and the comparison stops isolating fuel.
            state["completed_laps"] = [
                {k: v for k, v in lap.items()
                 if k not in {"fuel_start_kg", "fuel_end_kg"}}
                for lap in state["completed_laps"]
            ]
            state.pop("fuel_kg", None)
        state.update(total_laps=40, current_lap=5, player_position=5,
                     active_cars=20, player_car_index=0, drivers=[],
                     mode_profile="race", session_type="Race", car_setup={})
        return state

    with_fuel = engine().compute(race(), {"compounds": {}})
    without = engine().compute(race(fuel=False), {"compounds": {}})
    assert with_fuel["available"] and without["available"]
    assert with_fuel["fuel_model"]["available"] is True
    assert without["fuel_model"]["available"] is False

    def by_shape(result):
        return {
            (tuple(plan["box_laps"]), tuple(plan["compounds"])): plan
            for plan in result["plans"]
        }

    shared = set(by_shape(with_fuel)) & set(by_shape(without))
    assert shared, "the two runs must share at least one candidate shape"
    lighter = 0
    for key in shared:
        fuelled = by_shape(with_fuel)[key]["projected_time_s"]
        heavy = by_shape(without)[key]["projected_time_s"]
        assert fuelled < heavy, f"{key} should be quicker on a lightening car"
        lighter += 1
    assert lighter > 0
