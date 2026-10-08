from copy import deepcopy

import pytest

from pitwall.strategy import StrategyEngine


def scenario():
    return {
        "player_position": 6, "player_car_index": 0, "current_lap": 7,
        "total_laps": 15, "track_id": 13,
        "drivers": [
            {"car_idx": 0, "position": 6, "position_history": []},
            {"car_idx": 1, "position": 7, "name": "Rival", "gap_to_player_s": 2.0,
             "gap_history": [{"lap": lap, "gap_s": 2.0} for lap in (4, 5, 6)]},
        ],
        "analysis": {"corner_metrics": []},
    }


def test_stable_gap_is_not_evidence_of_strong_defending():
    result = StrategyEngine.defence_assessment(scenario())
    assert result["closing_rate_s_per_lap"] == 0
    assert result["defence_quality_score_out_of_10"] is None
    assert result["positions_lost_in_recorded_history"] is None
    assert result["largest_corner_loss_vs_pb_s"] is None
    assert result["quality_evidence_available"] is False
    assert "Insufficient" in result["quality_basis"]
    assert result["confidence"] == "low"


@pytest.mark.parametrize("corner_metrics", [[], [{"loss_vs_pb_s": None}], [{"loss_vs_pb_s": float('nan')}]])
def test_missing_corner_comparison_never_becomes_zero_loss(corner_metrics):
    state = scenario()
    state["drivers"][0]["position_history"] = [{"lap": 4, "position": 6}, {"lap": 6, "position": 6}]
    state["analysis"]["corner_metrics"] = corner_metrics
    result = StrategyEngine.defence_assessment(state)
    assert result["positions_lost_in_recorded_history"] == 0
    assert result["largest_corner_loss_vs_pb_s"] is None
    assert result["defence_quality_score_out_of_10"] is None


def test_observed_corner_and_position_losses_reduce_supported_quality():
    state = scenario()
    state["drivers"][0]["position_history"] = [{"lap": 4, "position": 6}, {"lap": 6, "position": 6}]
    state["analysis"]["corner_metrics"] = [{"loss_vs_pb_s": 0.0}]
    baseline = StrategyEngine.defence_assessment(state)
    assert baseline["quality_evidence_available"] is True
    assert baseline["defence_quality_score_out_of_10"] == 8.5
    assert baseline["defence_laps_sustainable"] == 9
    assert baseline["largest_corner_loss_vs_pb_s"] == 0.0
    worse = deepcopy(state)
    worse["drivers"][0]["position_history"][0]["position"] = 4
    worse["analysis"]["corner_metrics"] = [{"loss_vs_pb_s": 0.4}]
    result = StrategyEngine.defence_assessment(worse)
    assert result["positions_lost_in_recorded_history"] == 2
    assert result["largest_corner_loss_vs_pb_s"] == 0.4
    assert result["defence_quality_score_out_of_10"] == 4.5
    assert "Heuristic" in result["quality_basis"]


def test_one_position_or_missing_gap_trend_cannot_support_quality():
    state = scenario()
    state["drivers"][0]["position_history"] = [{"lap": 6, "position": 6}]
    state["analysis"]["corner_metrics"] = [{"loss_vs_pb_s": 0.0}]
    assert StrategyEngine.defence_assessment(state)["defence_quality_score_out_of_10"] is None
    state["drivers"][0]["position_history"].append({"lap": 7, "position": 6})
    state["drivers"][1]["gap_history"] = []
    result = StrategyEngine.defence_assessment(state)
    assert result["defence_quality_score_out_of_10"] is None
    assert result["defence_laps_sustainable"] is None
