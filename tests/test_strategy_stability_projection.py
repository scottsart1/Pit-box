"""A held pit call must not hold or mix old race projections (issue #36)."""

from copy import deepcopy

import pytest


@pytest.mark.asyncio
async def test_recompute_holds_radio_call_but_refreshes_the_whole_projection(stack):
    """Drive a real lap/overtake transition through compute, stability and store.

    Lower wear growth makes a later stop the new winner while the original
    call is still inside its hold window. The driver also gains a position;
    every published classification statistic must follow that current state.
    """
    store, database, strategy, _, _, tools = stack
    await store.update(
        session_uid=42,
        session_type="Race",
        mode_profile="race",
        current_lap=14,
        total_laps=28,
        player_position=5,
        active_cars=20,
        track_id=13,
        tyre={"compound": "HARD", "age_laps": 12, "wear": [40] * 4},
        tyre_sets=[
            {"compound": "MEDIUM", "available": True},
            {"compound": "SOFT", "available": True},
        ],
    )
    previous = await strategy.recompute()
    assert previous["recommended"]["box_lap"] == 25
    assert previous["recommended"]["expected_finish_position"] == 5.0

    await store.update(
        current_lap=15,
        player_position=4,
        tyre={"compound": "HARD", "age_laps": 13, "wear": [41] * 4},
    )
    result = await strategy.recompute()
    held = result["recommended"]
    fresh = next(
        plan for plan in strategy._candidate_pool
        if strategy._radio_signature(plan) == strategy._radio_signature(held)
    )

    assert result["stability"]["held"] is True
    assert result["raw_recommended"]["box_lap"] == 27
    assert held["box_lap"] == 25
    assert held["instruction"] == previous["recommended"]["instruction"]
    assert held["committed_at_lap"] == 14
    # The whole evaluation, not a hand-picked subset of its known fields.
    for key, value in fresh.items():
        assert held[key] == value, key
    assert held["projected_finish_position"] == 4
    assert held["position_probabilities"] == {"P4": 1.0}
    assert held["expected_finish_position"] == 4.0
    assert held["projected_points"] == held["points_expected"] == 12
    assert held["upside_p90"] == held["upside_p10_position"] == 4
    assert held["downside_p10"] == held["downside_p90_position"] == 4
    assert "Projects P4" in held["rationale"]
    assert (await store.snapshot_analysis())["strategy"] == result
    assert (await tools.get_pit_strategy())["recommended"] == held
    history = await database.history_query(session_uid=42)
    persisted = next(row for row in history["strategies"] if row["lap_num"] == 15)
    assert persisted["recommended"] == held
    assert persisted["model"]["model_summary"] == result["model_summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outside_top_five", [False, True])
@pytest.mark.parametrize("baseline_status", ["feasible", "illegal", "worn_out"])
async def test_held_snapshot_replaces_optional_fields_and_selected_plan_confidence(
    stack, outside_top_five, baseline_status
):
    store, _, strategy, *_ = stack
    await store.update(
        current_lap=14,
        tyre={"compound": "HARD", "age_laps": 12, "wear": [40] * 4},
    )
    state = await store.snapshot_analysis()
    previous = {
        "recommended": {
            "box_lap": 17,
            "fit_compound": "MEDIUM",
            "stops_remaining": 1,
            "instruction": "Box lap 17 for MEDIUM.",
            "committed_at_lap": 13,
            "source_compound": "HARD",
            "neutralisation_phase": "green",
            "expected_finish_position": 20.0,
            "projected_points": 0,
            "finish_projection_confidence": "high",
            "projected_finish_position_without_driver_feedback": 20,
            "rationale": "Projects P20 with no recovery.",
            "tyre_reason": "The old projection was 99% wear.",
        },
    }
    fresh = {
        "box_laps": [17],
        "compounds": ["HARD", "MEDIUM"],
        "stops_remaining": 1,
        "feasible": True,
        "legal": True,
        "compound_rule": {"compliant": True, "wet_waiver": False},
        "risk_adjusted_time_s": 1801.0,
        "projected_finish_position": 6,
        "projected_rejoin_position": 8,
        "expected_positions_recovered": 2,
        "projected_finish_wear_pct": 55,
        "projected_points": 8,
        "position_probabilities": {"P4": 0.25, "P6": 0.75},
        "outcome_distribution": {"P4-6": 1.0},
        "expected_finish_position": 5.5,
        "points_expected": 9.0,
        "upside_p90": 4,
        "upside_p10_position": 4,
        "downside_p10": 6,
        "downside_p90_position": 6,
        "finish_projection_confidence": "low",
        "monte_carlo": {"samples": 80, "mean_s": 1800.0},
        # New projection fields should work without editing the stabilizer.
        "future_projection_metric": {"value": 7},
        "stint_models": [{
            "laps": 5, "wear_sample_size": 4, "deg_sample_size": 5,
            "wear_per_lap_pct": 1.5, "wear_source": "personal_history",
            "deg_s_per_lap": 0.2, "deg_source": "personal_history",
        }],
    }
    stay_out = {
        "stops_remaining": 0, "feasible": baseline_status == "feasible",
        "legal": baseline_status != "illegal",
        "risk_adjusted_time_s": 1811.0, "projected_finish_wear_pct": 75,
    }
    candidate = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 21, "fit_compound": "SOFT", "stops_remaining": 1,
            "risk_adjusted_time_s": 1800.2, "projected_finish_position": 5,
            "instruction": "Box lap 21 for SOFT.",
        },
        "confidence": "high",
        "model_summary": {
            "confidence": "high", "evidence_samples": 20,
            "selected_stint_wear_source": "candidate_history",
            "confidence_basis": "Least-supported tyre stint in the selected plan.",
        },
        "plans": [] if outside_top_five else [fresh],
    }
    strategy._candidate_pool = [stay_out, fresh] if outside_top_five else [stay_out]
    previous_before, fresh_before = deepcopy(previous), deepcopy(fresh)
    result = strategy._stabilize_radio_plan(state, previous, candidate)
    held = result["recommended"]

    assert result["stability"]["held"] is True
    assert held["instruction"] == previous["recommended"]["instruction"]
    for key, value in fresh.items():
        assert held[key] == value, key
    assert "projected_finish_position_without_driver_feedback" not in held
    assert held["expected_finish_position"] == sum(
        int(position[1:]) * probability
        for position, probability in held["position_probabilities"].items()
    )
    assert held["net_gain_vs_stay_out_s"] == (
        10.0 if baseline_status == "feasible" else None
    )
    assert held["stop_required_reason"] == {
        "feasible": "best projected finishing position and expected-points outcome",
        "illegal": "mandatory compound change",
        "worn_out": "current tyre cannot reach the finish inside the operational wear margin",
    }[baseline_status]
    assert "Projects P6 after rejoining P8" in held["rationale"]
    assert "55%" in held["tyre_reason"] and "75%" in held["tyre_reason"]
    assert result["confidence"] == result["model_summary"]["confidence"] == "medium"
    assert result["model_summary"]["evidence_samples"] == 4
    assert result["model_summary"]["selected_stint_wear_source"] == "personal_history"
    assert result["compound_rule"] == fresh["compound_rule"]
    assert previous == previous_before
    assert fresh == fresh_before
