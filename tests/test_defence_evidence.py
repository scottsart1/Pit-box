from copy import deepcopy

import pytest

from pitwall.strategy import StrategyEngine
from pitwall.state import DriverState


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


@pytest.mark.parametrize("change,trend,description", [
    (.191, "pulling_away", "growing"),
    (-.191, "being_caught", "shrinking"),
    (0, "holding_station", "stable"),
    (.02, "holding_station", "stable"),
])
def test_defence_gap_direction_is_explicit_and_uses_gap_change_sign(change, trend, description):
    state = scenario()
    state["drivers"][1]["gap_history"] = [
        {"lap": 4, "gap_s": 2.0}, {"lap": 6, "gap_s": 2.0 + 2 * change},
    ]
    result = StrategyEngine.defence_assessment(state)
    assert result["gap_change_s_per_lap"] == change
    assert result["closing_rate_s_per_lap"] == change
    assert result["gap_trend"] == trend
    assert description in result["gap_trend_description"]
    assert result["interpretation"].startswith(result["gap_trend_description"])


def test_defence_without_gap_or_history_cannot_claim_contact_or_a_trend():
    state = scenario()
    state["drivers"][1]["gap_history"] = []
    result = StrategyEngine.defence_assessment(state)
    assert result["gap_trend"] == "unknown"
    assert result["gap_change_s_per_lap"] is None
    assert result["defence_laps_sustainable"] is None
    state["drivers"][1]["gap_to_player_s"] = None
    assert StrategyEngine.defence_assessment(state)["available"] is False


@pytest.mark.asyncio
async def test_named_defence_assesses_selected_following_car_not_nearest(stack):
    store, _, _, _, _, tools = stack
    await store.mark_packet(2026, 26, 42, packet_id=2)
    await store.mark_packet(2026, 26, 42, packet_id=7)
    await store.update(player_position=6, current_lap=7, total_laps=15)
    def seed(state):
        state.drivers[0] = DriverState(car_idx=0, position=6, active=True, is_player=True)
        state.drivers[1] = DriverState(car_idx=1, position=7, active=True, name="Nearest", gap_to_player_s=2,
            gap_history=[{"lap": 4, "gap_s": 1}, {"lap": 6, "gap_s": 2}])
        state.drivers[2] = DriverState(car_idx=2, position=8, active=True, name="Named rival", gap_to_player_s=3,
            gap_history=[{"lap": 4, "gap_s": 4}, {"lap": 6, "gap_s": 3}])
        state.drivers[3] = DriverState(car_idx=3, position=5, active=True, name="Ahead", gap_to_player_s=-2)
    await store.mutate(seed)
    result = await tools.get_defence_plan("Named rival")
    assert result["driver"] == result["assessment"]["pursuer"] == "Named rival"
    assert result["assessment"]["gap_trend"] == "being_caught"
    assert result["assessment"]["gap_change_s_per_lap"] == -.5
    assert (await tools.get_defence_plan("behind"))["assessment"]["gap_trend"] == "pulling_away"
    assert (await tools.get_defence_plan("ahead"))["available"] is False
    assert (await tools.get_attack_plan("ahead"))["available"] is True
