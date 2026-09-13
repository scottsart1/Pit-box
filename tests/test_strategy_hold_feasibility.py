"""A radio hold may preserve an instruction, never an unsupported finish."""

from copy import deepcopy

import pytest

from pitwall.strategy import StrategyEngine


def _sequence():
    state = {"session_uid": 44, "current_lap": 14, "player_position": 5,
             "tyre": {"compound": "HARD", "wear": [50] * 4}}
    previous = {"recommended": {
        "box_lap": 17, "fit_compound": "MEDIUM", "stops_remaining": 1,
        "committed_at_lap": 14, "instruction": "Box lap 17 for MEDIUM.",
        "source_compound": "HARD", "neutralisation_phase": "green",
    }}
    evaluated = {
        "box_laps": [17], "compounds": ["HARD", "MEDIUM"],
        "stops_remaining": 1, "feasible": True, "legal": True,
        "risk_adjusted_time_s": 1801., "projected_finish_position": 5,
        "projected_rejoin_position": 8, "projected_finish_wear_pct": 60,
        "finish_projection_confidence": "low",
        "stint_models": [{"laps": 10, "wear_sample_size": 30, "deg_sample_size": 30}],
    }
    candidate = {
        "neutralisation": {"phase": "green"},
        "recommended": {
            "box_lap": 18, "fit_compound": "SOFT", "stops_remaining": 1,
            "feasible": False, "legal": True, "risk_adjusted_time_s": 1800.,
            "finish_projection_valid": False,
            "instruction": "No feasible finish is supported by the current tyre data.",
        },
        "confidence": "low", "model_summary": {"confidence": "low"},
        "plans": [],
    }
    return state, previous, evaluated, candidate


@pytest.mark.parametrize("failed_constraint", ["feasible", "legal"])
@pytest.mark.parametrize("source", ["displayed", "candidate_pool"])
def test_no_hold_of_currently_unsafe_or_illegal_plan(failed_constraint, source):
    state, previous, evaluated, candidate = _sequence()
    evaluated[failed_constraint] = False
    engine = StrategyEngine(None, None)
    if source == "displayed":
        candidate["plans"] = [evaluated]
    else:
        engine._candidate_pool = [evaluated]
    warning = candidate["recommended"]["instruction"]
    result = engine._stabilize_radio_plan(state, previous, candidate)
    assert result["stability"]["held"] is False
    assert result["recommended"]["instruction"] == warning
    assert result["recommended"]["finish_projection_valid"] is False
    assert result["confidence"] == "low"


@pytest.mark.parametrize("metadata_location", ["plan", "result"])
def test_unknown_spare_inventory_cannot_become_high_confidence_during_hold(metadata_location):
    state, previous, evaluated, candidate = _sequence()
    engine = StrategyEngine(None, None)
    if metadata_location == "plan":
        evaluated.update(inventory_status="unknown", inventory_feasible=None)
    else:
        candidate["tyre_inventory"] = {"status": "unknown"}
    candidate["plans"] = [evaluated]
    result = engine._stabilize_radio_plan(state, previous, candidate)
    assert result["stability"]["held"] is True
    assert result["confidence"] == result["model_summary"]["confidence"] == "low"
    assert "Confirm a spare set is available" in result["recommended"]["instruction"]
    # Repeated holds update data without repeating the qualification.
    again = engine._stabilize_radio_plan(state, result, deepcopy(candidate))
    assert again["recommended"]["instruction"].count("Confirm a spare set is available") == 1


def test_supported_hold_keeps_field_confidence_separate_from_tyre_evidence():
    state, previous, evaluated, candidate = _sequence()
    engine = StrategyEngine(None, None)
    evaluated.update(inventory_status="known", inventory_feasible=True)
    candidate["plans"] = [evaluated]
    result = engine._stabilize_radio_plan(state, previous, candidate)
    held = result["recommended"]
    assert held["finish_projection_valid"] is True
    assert held["finish_projection_confidence"] == "low"
    assert result["model_summary"]["evidence_samples"] == 30
    assert result["confidence"] == "high"  # Measured tyre evidence, not probability calibration.

