"""Rival forecasts measured against known constant-fuel generating paths."""

from copy import deepcopy

import pytest

from pitwall.strategy import StrategyEngine


def rival(*, age=10, slope=0.1, start=1):
    return {
        "car_idx": 1, "name": "Matched rival", "position": 2,
        "current_lap": start + age, "tyre_compound": "MEDIUM", "tyre_age": age,
        "gap_to_player_s": 0.0,
        "tyre_stints": [{"start_lap": start, "end_lap": 255, "compound": "MEDIUM"}],
        "lap_history": [{"lap_num": start + end_age - 1,
                         "lap_ms": round((90 + slope * end_age) * 1000), "valid_flags": 1}
                        for end_age in range(2, age + 1)],
    }


def project(driver, monkeypatch, *, player_lap=None):
    engine = StrategyEngine(None, None)
    # This experiment specifies the rival's true slope; production continues
    # using its explicitly labelled track prior, never the player's fit.
    monkeypatch.setattr(engine, "_deg_for", lambda *args: (0.1, "synthetic_known_rival_prior", 20))
    state = {"player_car_index": 0, "player_position": 1, "current_lap": player_lap or driver.get("current_lap", 11),
             "track_id": 0, "race_control_phase": "green", "drivers": [driver]}
    return engine._project_rival_finish_times(state, {}, 5, 90, 22)[0]


def test_rival_observed_age_is_not_the_age_at_forecast_start(monkeypatch):
    result = project(rival(), monkeypatch)
    # Completed observations are90.2..91.0. Future completions age11..15
    # must be91.1..91.5, independently totalling456.5 seconds.
    assert result["finish_time_s"] == pytest.approx(456.5)
    assert result["pace_reference"]["reference_age_laps"] == 8
    assert result["pace_reference"]["source"] == "matched_current_stint_age"


def test_rival_matching_uses_its_own_lap_number_when_lapped(monkeypatch):
    driver = rival(start=8)
    assert project(driver, monkeypatch, player_lap=22)["finish_time_s"] == pytest.approx(456.5)


def test_newly_fitted_set_does_not_inherit_old_stint_pace(monkeypatch):
    driver = rival()
    driver.update(current_lap=11, tyre_age=0, tyre_compound="HARD",
                  tyre_stints=[{"start_lap": 1, "end_lap": 10, "compound": "MEDIUM"},
                               {"start_lap": 11, "end_lap": 255, "compound": "HARD"}])
    result = project(driver, monkeypatch)
    assert result["pace_samples"] == 0
    assert result["pace_reference"]["source"] == "no_matching_current_stint"


def test_previous_compound_and_future_history_do_not_pollute_reference(monkeypatch):
    driver = rival(age=5, start=20)
    driver["lap_history"].extend([
        {"lap_num": 18, "lap_ms": 70000, "valid_flags": 1},
        {"lap_num": 25, "lap_ms": 70000, "valid_flags": 1},
    ])
    result = project(driver, monkeypatch)
    assert result["finish_time_s"] == pytest.approx(sum(90 + .1 * age for age in range(6, 11)))
    assert result["pace_samples"] == 4


def test_duplicate_lap_is_not_extra_rival_evidence(monkeypatch):
    driver = rival()
    driver["lap_history"] = [deepcopy(driver["lap_history"][-1]) for _ in range(8)]
    result = project(driver, monkeypatch)
    assert result["pace_samples"] == 1
    assert result["finish_time_s"] == pytest.approx(456.5)
    assert result["confidence"] != "high"


def test_mismatched_stint_update_does_not_anchor_old_compound(monkeypatch):
    driver = rival()
    driver["tyre_stints"][-1]["compound"] = "HARD"
    result = project(driver, monkeypatch)
    assert result["pace_samples"] == 0


def test_missing_chronology_preserves_explicit_legacy_fallback(monkeypatch):
    driver = rival()
    driver.pop("current_lap")
    for lap in driver["lap_history"]:
        lap.pop("lap_num")
    result = project(driver, monkeypatch)
    assert result["finish_time_s"] == pytest.approx(455.0)
    assert result["pace_reference"]["source"] == "unmatched_recent_laps_fallback"
    assert result["pace_reference"]["age_end_reference"] is False


def test_full_compute_keeps_identical_rival_behind_at_same_known_slope():
    # Track11 medium prior is0.09*0.92=0.0828. Set the player's independently
    # generated/learned slope to the same value so this tests the clocks only.
    slope = 0.0828
    driver = rival(slope=slope)
    driver["gap_to_player_s"] = 1.0
    laps = [{"session_uid": 1, "restart_epoch": 0, "timeline_epoch": 0,
             "lap_num": age, "lap_time_ms": round((90 + slope * age) * 1000),
             "valid": True, "compound": "MEDIUM", "tyre_age_start": age-1,
             "tyre_age_end": age, "wear_start": [age-1.] * 4, "wear_end": [float(age)] * 4,
             "fuel_start_kg": 20., "fuel_end_kg": 20., "weather": "Clear", "setup": {}, "pit_status": 0}
            for age in range(2, 11)]
    state = {"session_uid": 1, "restart_epoch": 0, "timeline_epoch": 0,
             "current_lap": 11, "total_laps": 15, "mode_profile": "sprint", "session_type": "Sprint",
             "track_id": 11, "weather": "Clear", "race_control_phase": "green", "safety_car": "none",
             "player_car_index": 0, "player_position": 1, "active_cars": 2,
             "tyre": {"compound": "MEDIUM", "age_laps": 10, "wear": [10.] * 4},
             "car_setup": {}, "completed_laps": laps, "tyre_sets": [],
             "drivers": [{"car_idx": 0, "position": 1, "pit_stops": 0}, driver]}
    history = {"compounds": {"MEDIUM": {"slope_s_per_lap": slope, "sample_size": 20,
               "max_wear_per_lap_pct": 1., "wear_sample_size": 20, "wheel_wear_per_lap_pct": [1.] * 4}}}
    result = StrategyEngine(None, None).compute(state, history)
    mine = result["recommended"]
    opponent = result["rival_finish_projections"][0]
    assert opponent["finish_time_s"] - mine["projected_time_s"] == pytest.approx(1.0, abs=.01)
    assert mine["projected_finish_position"] == 1


def test_outlier_normalizes_observations_with_their_own_ages(monkeypatch):
    driver = rival()
    driver["lap_history"][-4]["lap_ms"] += 10000
    # Independent medians of pace and age would produce a false90.1s
    # intercept here. Paired age normalization retains the four90s samples.
    assert project(driver, monkeypatch)["finish_time_s"] == pytest.approx(456.5)


def test_matched_rival_stop_resets_to_first_completed_fresh_lap(monkeypatch):
    driver = rival(age=16)
    driver["tyre_compound"] = "SOFT"
    driver["tyre_stints"][-1]["compound"] = "SOFT"
    result = project(driver, monkeypatch)
    assert result["likely_stop_offsets_laps"] == [1]
    # Complete age17, pit22s, then finish fresh ages1,2,3,4.
    expected = sum(90 + .1 * age for age in [17, 1, 2, 3, 4]) + 22
    assert result["finish_time_s"] == pytest.approx(expected)
