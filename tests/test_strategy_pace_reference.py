"""Known generating processes test the observed pace reference, not its formula."""

from copy import deepcopy

import pytest

from pitwall.setup_model import setup_effects
from pitwall.strategy import StrategyEngine


def linear_stint(*, age=10, offset=0.0, setup=None):
    """Constant-fuel laps generated independently of strategy implementation."""
    setup = dict(setup or {})
    laps = [
        {
            "session_uid": 501,
            "restart_epoch": 0,
            "timeline_epoch": 0,
            "lap_num": end_age,
            "lap_time_ms": round((90.0 + offset + 0.1 * end_age) * 1000),
            "valid": True,
            "compound": "MEDIUM",
            "tyre_age_start": end_age - 1,
            "tyre_age_end": end_age,
            "wear_start": [float(end_age - 1)] * 4,
            "wear_end": [float(end_age)] * 4,
            "fuel_start_kg": 20.0,
            "fuel_end_kg": 20.0,
            "weather": "Clear",
            "setup": dict(setup),
            "pit_status": 0,
        }
        for end_age in range(2, age + 1)
    ]
    state = {
        "session_uid": 501,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "current_lap": age + 1,
        "total_laps": age + 5,
        "mode_profile": "sprint",
        "session_type": "Sprint",
        "track_id": 0,
        "weather": "Clear",
        "race_control_phase": "green",
        "safety_car": "none",
        "player_car_index": 0,
        "player_position": 1,
        "active_cars": 1,
        "tyre": {"compound": "MEDIUM", "age_laps": age, "wear": [float(age)] * 4},
        "car_setup": dict(setup),
        "completed_laps": laps,
        "tyre_sets": [],
        "drivers": [{"car_idx": 0, "position": 1, "pit_stops": 0}],
    }
    historical = {
        "compounds": {
            "MEDIUM": {
                "slope_s_per_lap": 0.1,
                "sample_size": 20,
                "max_wear_per_lap_pct": 1.0,
                "wear_sample_size": 20,
                "wheel_wear_per_lap_pct": [1.0] * 4,
            }
        }
    }
    return state, historical


@pytest.mark.parametrize("age", [5, 10, 18])
@pytest.mark.parametrize("offset", [0.0, 4.25])
def test_actual_compute_extrapolates_known_linear_stint(age, offset):
    state, historical = linear_stint(age=age, offset=offset)
    original = deepcopy(state)
    result = StrategyEngine(None, None).compute(state, historical)

    expected = [90.0 + offset + 0.1 * end_age for end_age in range(age + 1, age + 6)]
    plan = result["recommended"]
    assert plan["stops_remaining"] == 0
    assert plan["stint_models"][0]["lap_times_s"] == pytest.approx(expected)
    assert plan["projected_time_s"] == pytest.approx(sum(expected))
    assert result["pace_reference"]["source"] == "matched_dry_stint"
    assert result["pace_reference"]["normalized_lap_s"] == pytest.approx(90 + offset)
    assert state == original
    assert "_strategy_pace_reference" not in state


def test_compute_does_not_charge_current_setup_twice():
    setup = {"front_wing": 50, "rear_wing": 50}
    assert setup_effects(setup, 0)["lap_time_delta_s"] > 0.1
    state, historical = linear_stint(offset=1.25, setup=setup)
    result = StrategyEngine(None, None).compute(state, historical)

    assert result["recommended"]["stint_models"][0]["lap_times_s"] == pytest.approx(
        [92.35, 92.45, 92.55, 92.65, 92.75]
    )
    assert result["pace_reference"]["setup_in_observed_pace"] is True


def test_reference_reports_quantities_and_runs_once_per_compute(monkeypatch):
    state, historical = linear_stint()
    engine = StrategyEngine(None, None)
    original = engine._pace_reference
    calls = []

    def counted(state_arg, history_arg):
        calls.append(1)
        return original(state_arg, history_arg)

    monkeypatch.setattr(engine, "_pace_reference", counted)
    result = engine.compute(state, historical)
    reference = result["pace_reference"]
    assert len(calls) == 1
    assert reference["observed_lap_s"] == pytest.approx(90.85)
    assert reference["reference_age_laps"] == pytest.approx(8.5)
    assert reference["reference_degradation_s"] == pytest.approx(0.85)
    assert reference["reference_wear_penalty_s"] == 0
    assert reference["sample_size"] == 4
    assert reference["age_end_reference"] is True
    assert "fuel" in " ".join(reference["assumptions"])


@pytest.mark.parametrize("mismatch", ["missing_age", "wet", "setup", "timeline", "session", "set_reset"])
def test_unmatched_context_keeps_labelled_fallback(mismatch):
    state, historical = linear_stint()
    if mismatch == "missing_age":
        for lap in state["completed_laps"]:
            lap.pop("tyre_age_end")
    elif mismatch == "wet":
        state["weather"] = "Light rain"
    elif mismatch == "setup":
        state["car_setup"] = {"front_wing": 50, "rear_wing": 50}
    elif mismatch == "timeline":
        state["timeline_epoch"] = 1
    elif mismatch == "session":
        state["session_uid"] = 502
    elif mismatch == "set_reset":
        state["tyre"]["age_laps"] = 0

    reference = StrategyEngine._pace_reference(state, historical)
    assert reference["source"] == "unmatched_observed_or_default_fallback"
    assert reference["age_end_reference"] is False
    assert reference["reference_adjustment_s"] == 0
    assert reference["setup_in_observed_pace"] is False


def test_missing_age_fixture_retains_previous_numeric_fallback():
    state, historical = linear_stint()
    for lap in state["completed_laps"]:
        lap.pop("tyre_age_end")
    result = StrategyEngine(None, None).compute(state, historical)
    assert result["recommended"]["projected_time_s"] == pytest.approx(460.25)
    assert result["pace_reference"]["source"].endswith("fallback")


def test_prior_compound_does_not_dilute_identified_current_stint():
    state, historical = linear_stint()
    state["completed_laps"] = state["completed_laps"][-3:]
    previous = deepcopy(state["completed_laps"][0])
    previous.update(compound="HARD", lap_num=7, lap_time_ms=110_000)
    state["completed_laps"].insert(0, previous)

    result = StrategyEngine(None, None).compute(state, historical)
    assert result["pace_reference"]["sample_size"] == 3
    assert result["pace_reference"]["observed_lap_s"] == pytest.approx(90.9)
    assert result["recommended"]["projected_time_s"] == pytest.approx(456.5)


def test_duplicate_observation_is_not_three_independent_laps():
    state, historical = linear_stint()
    state["completed_laps"] = [deepcopy(state["completed_laps"][-1]) for _ in range(4)]
    assert StrategyEngine._pace_reference(state, historical)["age_end_reference"] is False


def test_clear_sky_after_rain_does_not_establish_dry_reference():
    state, historical = linear_stint()
    for lap in state["completed_laps"][:-3]:
        lap["weather"] = "Heavy rain"
    state["track_temp_c"] = 15
    assert StrategyEngine._pace_reference(state, historical)["age_end_reference"] is False


def test_new_driver_feedback_is_not_cancelled_by_reference_adjustment():
    state, historical = linear_stint()
    baseline = StrategyEngine._pace_reference(state, historical)
    state["driver_tyre_feedback"] = {
        "category": "tyres_gone", "compound": "MEDIUM", "lap": 11,
    }
    assert StrategyEngine._pace_reference(state, historical) == baseline


def test_matched_wear_reference_preserves_observed_cost_once():
    state, historical = linear_stint()
    # The generator adds the existing model's documented piecewise cost, but
    # independently writes that arithmetic rather than calling the model.
    for lap in state["completed_laps"]:
        wear = 54.0 + lap["tyre_age_end"]
        lap["wear_end"] = [wear] * 4
        lap["wear_start"] = [wear - 1] * 4
        penalty = max(0, wear - 52) * 0.009 + max(0, wear - 58) * 0.008
        lap["lap_time_ms"] += round(penalty * 1000)
    state["tyre"]["wear"] = [64.0] * 4
    reference = StrategyEngine._pace_reference(state, historical)
    assert reference["normalized_lap_s"] == pytest.approx(90.0)
    assert reference["reference_wear_penalty_s"] > 0
