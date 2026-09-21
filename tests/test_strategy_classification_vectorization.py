"""Exact scalar-versus-vector classification checks for the performance change."""

import math

import numpy as np
import pytest

from pitwall.strategy import StrategyEngine


def scalar_reference(plan, state, rivals, outcomes):
    """Frozen scalar decision rules, independent of production classification helpers."""
    player = int(state.get("player_position", 1) or 1)
    active = max(1, player, len(rivals) + 1,
                 max((int(r.get("position", 0) or 0) for r in rivals), default=0),
                 int(state.get("active_cars", 0) or 0))
    missing = max(0, player - 1 - len({int(r.get("position", 0)) for r in rivals
                                      if 0 < int(r.get("position", 0)) < player}))
    rejoin = int(plan.get("projected_rejoin_position", 1) or 1)
    cap = max(1, rejoin - math.floor(float(plan.get("expected_positions_recovered", 0))))
    positions = []
    for value in outcomes:
        outcome = float(value)
        raw = 1 + missing + sum(float(r.get("finish_time_s", 1e9)) < outcome for r in rivals) if rivals else player
        if int(plan.get("stops_remaining", 0) or 0) > 0:
            physical = outcome - float(plan.get("pending_finish_penalty_s", 0))
            recovered = sum(
                0 < int(r.get("position", 0)) <= int(plan.get("projected_rejoin_position", 1))
                and float(r.get("pending_finish_penalty_s", 0)) > 0
                and float(r.get("finish_time_before_penalties_s", r.get("finish_time_s", 0))) <= physical
                and float(r.get("finish_time_s", 0)) > outcome
                for r in rivals
            )
            raw = max(raw, max(1, cap - recovered))
        positions.append(max(1, min(active, raw)))
    total = len(positions)
    counts = {position: positions.count(position) for position in sorted(set(positions))}
    bands = {"P1-3": sum(p <= 3 for p in positions),
             "P4-6": sum(4 <= p <= 6 for p in positions),
             "P7-10": sum(7 <= p <= 10 for p in positions),
             "P11-15": sum(11 <= p <= 15 for p in positions),
             "P16+": sum(p >= 16 for p in positions)}
    points = [0, 8, 7, 6, 5, 4, 3, 2, 1] if state.get("mode_profile") == "sprint" else [0, 25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    return {
        "outcome_distribution": {key: round(value / total, 4) for key, value in bands.items()},
        "position_probabilities": {f"P{key}": round(value / total, 4) for key, value in counts.items()},
        "points_expected": round(sum(points[p] if p < len(points) else 0 for p in positions) / total, 3),
        "expected_finish_position": round(float(np.mean(positions)), 2),
        "upside_p90": int(np.quantile(positions, .10, method="nearest")),
        "downside_p10": int(np.quantile(positions, .90, method="nearest")),
        "upside_p10_position": int(np.quantile(positions, .10, method="nearest")),
        "downside_p90_position": int(np.quantile(positions, .90, method="nearest")),
    }


def _modelled(result):
    """Only the keys the scalar reference models.

    The distribution also carries the decision utility, which is a valuation of
    these same outcomes rather than a second way of computing them, and has no
    counterpart in this reference implementation.
    """
    return {
        key: value
        for key, value in result.items()
        if key not in _UTILITY_KEYS
    }


_UTILITY_KEYS = {
    "decision_utility",
    "expected_value_points",
    "tail_value_points",
    "utility_risk_appetite",
    "utility_basis",
}


@pytest.mark.parametrize("mode", ["race", "sprint"])
@pytest.mark.parametrize("stops", [0, 1, 3])
@pytest.mark.parametrize("rival_count", [0, 4, 23])
@pytest.mark.parametrize("samples", [80, 1200])
def test_vectorized_distribution_preserves_scalar_results(mode, stops, rival_count, samples):
    rng = np.random.default_rng(77013 + rival_count)
    rivals = []
    for index in range(rival_count):
        time = float(rng.normal(1000, 12))
        penalty = float(rng.choice([0, 0, 5, 10]))
        rivals.append({"position": index + 1, "finish_time_s": time + penalty,
                       "finish_time_before_penalties_s": time, "pending_finish_penalty_s": penalty})
    # Duplicate positions and unknown/lagging field size must preserve assumptions.
    if rival_count >= 4:
        rivals[-1]["position"] = rivals[-2]["position"]
    state = {"player_position": 8, "active_cars": 3, "mode_profile": mode}
    plan = {"stops_remaining": stops, "projected_rejoin_position": 15,
            "expected_positions_recovered": 4.9, "pending_finish_penalty_s": 5}
    outcomes = rng.normal(1000, 15, samples)
    assert _modelled(
        StrategyEngine._position_distribution(plan, state, rivals, outcomes)
    ) == scalar_reference(plan, state, rivals, outcomes)


@pytest.mark.parametrize("player_penalty", [0, 5, math.nan, math.inf])
def test_strict_ties_nonfinite_times_and_penalty_boundaries_are_unchanged(player_penalty):
    plan = {"stops_remaining": 1, "projected_rejoin_position": 5,
            "expected_positions_recovered": 2, "pending_finish_penalty_s": player_penalty}
    state = {"player_position": 3, "active_cars": 8, "mode_profile": "race"}
    rivals = [
        {"position": 1, "finish_time_s": 105, "finish_time_before_penalties_s": 100, "pending_finish_penalty_s": 5},
        {"position": 2, "finish_time_s": 100},
        {"position": 3, "finish_time_s": math.nan},
        {"position": 4, "finish_time_s": math.inf},
        {"position": 5, "finish_time_s": -math.inf},
        {"position": 6},
    ]
    outcomes = np.array([99.9, 100, 100.1, 104.9, 105, 105.1, math.nan, math.inf, -math.inf])
    assert _modelled(
        StrategyEngine._position_distribution(plan, state, rivals, outcomes)
    ) == scalar_reference(plan, state, rivals, outcomes)


def test_exact_finish_tie_does_not_move_a_rival_ahead():
    state = {"player_position": 1, "active_cars": 2, "mode_profile": "sprint"}
    plan = {"stops_remaining": 0}
    rivals = [{"position": 2, "finish_time_s": 100}]
    result = StrategyEngine._position_distribution(plan, state, rivals, np.array([100.0, 100.001]))
    assert result["position_probabilities"] == {"P1": .5, "P2": .5}
    assert result["points_expected"] == 7.5
