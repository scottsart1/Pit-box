"""Field degradation measured against known synthetic generating paths.

Each test builds a field whose true degradation is stated, then checks that the
estimator recovers it - or, where the design cannot identify it, that the
estimator says so instead of returning a number.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from pitwall import field_learning
from pitwall.field_learning import (
    Evidence,
    blend,
    field_degradation,
    field_evidence,
    field_laps,
    neutralised_lap_numbers,
)

OPEN = field_learning.OPEN_STINT_END_LAP


def car(car_idx, pace, plan, *, name=None, **overrides):
    """``plan`` is ``[(start_lap, end_lap, compound), ...]``, last may be open."""
    return {
        "car_idx": car_idx,
        "name": name or f"Car {car_idx}",
        "pace": pace,
        "plan": plan,
        "position": car_idx + 1,
        **overrides,
    }


def build_state(
    cars,
    *,
    total_laps,
    deg,
    evolution=0.0,
    fuel_s_per_lap=0.0,
    pit_loss_s=0.0,
    noise=None,
    completed_laps=(),
    **state_overrides,
):
    """A field whose lap times come from an explicitly stated model.

    ``deg`` is seconds per lap of tyre age, by compound. ``evolution`` is
    applied per lap of the race and is shared by every car, as are fuel mass
    and any other per-lap effect - which is exactly what the lap fixed effect
    is supposed to absorb.
    """
    drivers = []
    for entry in cars:
        history = []
        stints = []
        for start, end, compound in entry["plan"]:
            last = end if end != OPEN else total_laps
            stints.append({"start_lap": start, "end_lap": end, "compound": compound})
            for lap in range(start, last + 1):
                if lap > total_laps:
                    break
                age = lap - start + 1
                seconds = (
                    entry["pace"]
                    + deg.get(compound, 0.0) * age
                    + evolution * (lap - 1)
                    + fuel_s_per_lap * (total_laps - lap)
                )
                if noise is not None:
                    seconds += noise(entry["car_idx"], lap)
                if end != OPEN and lap == end:
                    seconds += pit_loss_s
                history.append(
                    {"lap_num": lap, "lap_ms": round(seconds * 1000), "valid_flags": 1}
                )
        driver = {
            key: value
            for key, value in entry.items()
            if key not in {"pace", "plan"}
        }
        driver["lap_history"] = history
        driver["tyre_stints"] = stints
        drivers.append(driver)
    return {
        "drivers": drivers,
        "completed_laps": list(completed_laps),
        "current_lap": total_laps,
        "total_laps": total_laps,
        **state_overrides,
    }


def diverged_field(**kwargs):
    """Six cars whose stint offsets differ, so both compounds are identifiable.

    Half run medium then hard, half run hard then medium, and every stop is on
    a different lap. That is what puts two cars on the same compound at
    different tyre ages on the same lap.
    """
    plans = [
        (0, 90.0, [(1, 14, "MEDIUM"), (15, OPEN, "HARD")]),
        (1, 90.9, [(1, 18, "MEDIUM"), (19, OPEN, "HARD")]),
        (2, 91.8, [(1, 22, "MEDIUM"), (23, OPEN, "HARD")]),
        (3, 92.7, [(1, 16, "HARD"), (17, OPEN, "MEDIUM")]),
        (4, 93.6, [(1, 20, "HARD"), (21, OPEN, "MEDIUM")]),
        (5, 94.5, [(1, 24, "HARD"), (25, OPEN, "MEDIUM")]),
    ]
    return build_state(
        [car(idx, pace, plan) for idx, pace, plan in plans],
        total_laps=44,
        **kwargs,
    )


def convoy_field(**kwargs):
    """Six cars that all start on medium on lap one and never diverge."""
    plans = [
        (idx, 90.0 + idx * 0.9, [(1, OPEN, "MEDIUM")]) for idx in range(6)
    ]
    return build_state(
        [car(idx, pace, plan) for idx, pace, plan in plans],
        total_laps=30,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Recovering a known slope
# --------------------------------------------------------------------------


def test_recovers_the_true_slope_for_each_compound():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    model = field_degradation(state)
    assert model["compounds"]["MEDIUM"]["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)
    assert model["compounds"]["HARD"]["slope_s_per_lap"] == pytest.approx(0.06, abs=1e-3)
    assert set(model["identified_compounds"]) == {"MEDIUM", "HARD"}


def test_a_shared_improving_track_is_absorbed_not_read_as_negative_wear():
    """Track evolution moves every car on a lap, so the lap effect takes it.

    Without the lap fixed effect a track improving by 0.05 s/lap would cancel
    most of a 0.12 s/lap tyre and teach the model that mediums barely degrade.
    """
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06}, evolution=-0.05)
    model = field_degradation(state)
    assert model["compounds"]["MEDIUM"]["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)
    assert model["compounds"]["HARD"]["slope_s_per_lap"] == pytest.approx(0.06, abs=1e-3)


def test_a_shared_fuel_burn_is_absorbed_without_being_measured():
    """No rival fuel telemetry is needed: fuel is common to the field on a lap."""
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06}, fuel_s_per_lap=0.06)
    model = field_degradation(state)
    assert model["compounds"]["MEDIUM"]["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)


def test_both_fuel_and_evolution_together_leave_the_slope_alone():
    state = diverged_field(
        deg={"MEDIUM": 0.12, "HARD": 0.06}, evolution=-0.04, fuel_s_per_lap=0.055
    )
    model = field_degradation(state)
    assert model["compounds"]["MEDIUM"]["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)
    assert model["compounds"]["HARD"]["slope_s_per_lap"] == pytest.approx(0.06, abs=1e-3)


def test_car_pace_spread_does_not_leak_into_the_slope():
    """The generating paces span 4.5 s; the car effect must absorb all of it."""
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    medium = field_degradation(state)["compounds"]["MEDIUM"]
    assert medium["car_count"] == 6
    assert medium["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)


def test_recovers_the_slope_through_lap_to_lap_noise():
    def noise(car_idx, lap):
        return 0.18 * math.sin(car_idx * 7.1 + lap * 2.3)

    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06}, noise=noise)
    model = field_degradation(state)["compounds"]["MEDIUM"]
    assert model["slope_s_per_lap"] == pytest.approx(0.12, abs=0.02)
    assert model["standard_error_s_per_lap"] > 0


# --------------------------------------------------------------------------
# Refusing when the design cannot identify a slope
# --------------------------------------------------------------------------


def test_a_field_that_never_diverges_reports_no_slope():
    """Everyone on lap N is at age N. There is no tyre evidence in that."""
    model = field_degradation(convoy_field(deg={"MEDIUM": 0.12}))
    medium = model["compounds"]["MEDIUM"]
    assert medium["identified"] is False
    assert medium["identification"] == "age_not_separable_from_lap"
    assert medium["slope_s_per_lap"] is None
    assert model["identified_compounds"] == []


def test_the_unidentified_case_is_not_rescued_by_more_cars_or_laps():
    plans = [(idx, 90.0 + idx * 0.9, [(1, OPEN, "MEDIUM")]) for idx in range(20)]
    state = build_state(
        [car(idx, pace, plan) for idx, pace, plan in plans],
        total_laps=60,
        deg={"MEDIUM": 0.12},
    )
    assert field_degradation(state)["compounds"]["MEDIUM"]["identified"] is False


def test_retained_age_variance_is_reported_so_the_refusal_is_checkable():
    medium = field_degradation(convoy_field(deg={"MEDIUM": 0.12}))["compounds"]["MEDIUM"]
    assert medium["retained_age_variance"] == pytest.approx(0.0, abs=1e-6)
    diverged = field_degradation(
        diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    )["compounds"]["MEDIUM"]
    assert diverged["retained_age_variance"] > field_learning.MIN_RETAINED_AGE_VARIANCE


def test_thin_coverage_is_declined_before_any_fit():
    plans = [
        (0, 90.0, [(1, 14, "MEDIUM"), (15, OPEN, "HARD")]),
        (1, 91.5, [(1, 20, "HARD"), (21, OPEN, "MEDIUM")]),
    ]
    state = build_state(
        [car(idx, pace, plan) for idx, pace, plan in plans],
        total_laps=30,
        deg={"MEDIUM": 0.12, "HARD": 0.06},
    )
    medium = field_degradation(state)["compounds"]["MEDIUM"]
    assert medium["identified"] is False
    assert medium["identification"] == "insufficient_field_coverage"


def test_an_implausible_slope_is_rejected_rather_than_published():
    state = diverged_field(deg={"MEDIUM": 4.0, "HARD": 0.06})
    medium = field_degradation(state)["compounds"]["MEDIUM"]
    assert medium["identified"] is False
    assert medium["identification"] == "implausible_slope"
    assert medium["rejected_slope_s_per_lap"] == pytest.approx(4.0, abs=0.05)


# --------------------------------------------------------------------------
# What counts as evidence
# --------------------------------------------------------------------------


def test_out_laps_and_in_laps_are_excluded():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    observations, excluded = field_laps(state)
    assert excluded["out_lap"] > 0
    assert excluded["in_lap"] > 0
    # Car 0 stops at the end of lap 14 and starts its hard stint on lap 15.
    for item in observations:
        if item.car_idx == 0:
            assert item.lap_num not in {1, 14, 15}


def test_laps_run_in_another_cars_wake_are_excluded():
    """Two cars nose to tail measure dirty air, not their tyres."""
    plans = [
        (0, 90.0, [(1, 14, "MEDIUM"), (15, OPEN, "HARD")]),
        (1, 90.02, [(1, 18, "MEDIUM"), (19, OPEN, "HARD")]),
        (2, 90.04, [(1, 22, "MEDIUM"), (23, OPEN, "HARD")]),
    ]
    state = build_state(
        [car(idx, pace, plan) for idx, pace, plan in plans],
        total_laps=44,
        deg={"MEDIUM": 0.12, "HARD": 0.06},
    )
    _, excluded = field_laps(state)
    assert excluded["dirty_air"] > 0


def test_clean_air_threshold_is_the_gap_the_lap_started_with():
    elapsed = {0: {5: 100.0, 6: 190.0}, 1: {5: 104.0, 6: 192.0}}
    clean = field_learning.clean_air_laps(elapsed, 2.5)
    # Leader is always clean; car 1 began lap 6 four seconds back, so lap 6 is
    # clean, but it began lap 7 only two seconds back, so lap 7 is not.
    assert (0, 6) in clean and (0, 7) in clean
    assert (1, 6) in clean
    assert (1, 7) not in clean


def test_neutralised_laps_are_excluded_for_every_car():
    completed = [
        {"lap_num": 20, "learning_exclusions": ["neutralised_lap"]},
        {"lap_num": 21, "safety_car": "full"},
        {"lap_num": 22, "race_control_phase": "vsc"},
    ]
    assert neutralised_lap_numbers(completed) == {20, 21, 22}
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06}, completed_laps=completed)
    observations, excluded = field_laps(state)
    assert excluded["neutralised"] > 0
    assert not [item for item in observations if item.lap_num in {20, 21, 22}]


def test_a_safety_car_does_not_move_the_slope():
    """Neutralised laps are slow for everyone; excluding them keeps the fit."""
    completed = [{"lap_num": lap, "safety_car": "full"} for lap in (24, 25, 26)]
    clean = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    for driver in clean["drivers"]:
        for lap in driver["lap_history"]:
            if lap["lap_num"] in {24, 25, 26}:
                lap["lap_ms"] += 40000
    clean["completed_laps"] = completed
    model = field_degradation(clean)["compounds"]["MEDIUM"]
    assert model["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)


def test_invalid_laps_are_excluded():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["drivers"][0]["lap_history"][10]["valid_flags"] = 0
    _, excluded = field_laps(state)
    assert excluded["invalid_lap"] == 1


def test_restricted_and_retired_cars_contribute_nothing():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["drivers"][0]["restricted"] = True
    state["drivers"][1]["result_label"] = "Retired"
    observations, _ = field_laps(state)
    assert not [item for item in observations if item.car_idx in {0, 1}]


def test_wet_compounds_are_not_pooled_into_a_dry_slope():
    plans = [
        (0, 90.0, [(1, 14, "MEDIUM"), (15, OPEN, "INTER")]),
        (1, 90.9, [(1, 18, "MEDIUM"), (19, OPEN, "INTER")]),
        (2, 91.8, [(1, 22, "MEDIUM"), (23, OPEN, "INTER")]),
    ]
    state = build_state(
        [car(idx, pace, plan) for idx, pace, plan in plans],
        total_laps=44,
        deg={"MEDIUM": 0.12, "INTER": 0.3},
    )
    model = field_degradation(state)
    assert "INTER" not in model["compounds"]
    _, excluded = field_laps(state)
    assert excluded["not_a_dry_compound"] > 0


def test_a_broken_lap_history_stops_accumulating_rather_than_closing_the_gap():
    history = [
        {"lap_num": 1, "lap_ms": 90000},
        {"lap_num": 2, "lap_ms": 90000},
        {"lap_num": 4, "lap_ms": 90000},
    ]
    assert field_learning._elapsed_by_lap(history) == {1: 90.0, 2: 180.0}


def test_a_lap_with_no_identified_stint_is_counted_not_guessed():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["drivers"][0]["tyre_stints"] = [{"start_lap": 1, "end_lap": 5, "compound": "MEDIUM"}]
    _, excluded = field_laps(state)
    assert excluded["no_identified_stint"] > 0


# --------------------------------------------------------------------------
# Uncertainty
# --------------------------------------------------------------------------


def test_clustered_errors_exceed_independent_errors_when_laps_correlate():
    """Correlated laps inside one car must not advertise false precision.

    Residuals here are identical within each car, which is the worst case for
    an independence assumption: the clustered error must come out strictly
    larger than the one that treats every lap as its own observation.
    """
    x = np.array([[1.0], [2.0], [3.0], [1.0], [2.0], [3.0], [1.0], [2.0], [3.0],
                  [1.0], [2.0], [3.0]])
    residuals = np.repeat(np.array([0.2, -0.2, 0.15, -0.15]), 3)
    car_codes = np.repeat(np.arange(4), 3)
    gram = x.T @ x
    bread = np.linalg.inv(gram)
    covariance = field_learning._cluster_robust_covariance(
        x, residuals, car_codes, 4, bread
    )
    clustered = math.sqrt(float(covariance[0, 0]))
    independent = math.sqrt(
        float(np.sum((x[:, 0] * residuals) ** 2)) / float(gram[0, 0]) ** 2
    )
    assert clustered > independent


def test_a_single_cluster_yields_no_standard_error_rather_than_a_false_one():
    x = np.array([[1.0], [2.0], [3.0]])
    residuals = np.array([0.1, -0.1, 0.05])
    codes = np.zeros(3, dtype=np.int64)
    bread = np.linalg.inv(x.T @ x)
    assert field_learning._cluster_robust_covariance(x, residuals, codes, 1, bread) is None


def test_a_noisy_field_reports_a_standard_error_that_is_actually_positive():
    def noise(car_idx, lap):
        return 0.18 * math.sin(car_idx * 7.1 + lap * 2.3)

    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06}, noise=noise)
    model = field_degradation(state)["compounds"]["MEDIUM"]
    assert model["standard_error_s_per_lap"] > 0


def test_outliers_are_trimmed_rather_than_allowed_to_drag_the_fit():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    for driver in state["drivers"][:2]:
        driver["lap_history"][8]["lap_ms"] += 9000
    model = field_degradation(state)["compounds"]["MEDIUM"]
    assert model["slope_s_per_lap"] == pytest.approx(0.12, abs=0.01)
    assert model["pooled_observations_used"] < model["pooled_observations"]


# --------------------------------------------------------------------------
# Blending
# --------------------------------------------------------------------------


def test_blend_weights_by_precision():
    result = blend([
        Evidence(value=0.10, sigma=0.10, source="prior"),
        Evidence(value=0.20, sigma=0.01, source="field_learned"),
    ])
    # The tight source dominates but does not entirely erase the wide one.
    assert 0.195 < result["value"] < 0.20
    assert result["weights"]["field_learned"] > result["weights"]["prior"]
    assert result["sigma"] < 0.01


def test_blend_of_one_source_returns_that_source():
    result = blend([Evidence(value=0.14, sigma=0.03, source="field_learned"), None])
    assert result["value"] == pytest.approx(0.14)
    assert result["sources"] == ["field_learned"]


def test_blend_of_nothing_is_none():
    assert blend([None, None]) is None
    assert blend([Evidence(value=0.1, sigma=0.0, source="degenerate")]) is None
    assert blend([Evidence(value=float("nan"), sigma=0.1, source="bad")]) is None


def test_blend_sigma_tightens_as_sources_agree():
    one = blend([Evidence(value=0.12, sigma=0.04, source="a")])
    two = blend([
        Evidence(value=0.12, sigma=0.04, source="a"),
        Evidence(value=0.12, sigma=0.04, source="b"),
    ])
    assert two["sigma"] < one["sigma"]
    assert two["value"] == pytest.approx(0.12)


def test_field_evidence_is_absent_unless_the_slope_was_identified():
    assert field_evidence(None, "MEDIUM") is None
    assert field_evidence({"compounds": {}}, "MEDIUM") is None
    unidentified = {"compounds": {"MEDIUM": {"identified": False, "slope_s_per_lap": None}}}
    assert field_evidence(unidentified, "MEDIUM") is None


def test_a_large_slope_is_not_penalised_with_a_wider_spread():
    """A proportional spread would make strong evidence count for less."""
    def spread(slope):
        return field_evidence(
            {"compounds": {"MEDIUM": {"identified": True, "slope_s_per_lap": slope,
                                      "standard_error_s_per_lap": 0.004,
                                      "run_count": 6}}},
            "MEDIUM",
        ).sigma

    assert spread(0.30) == spread(0.05)


def test_field_evidence_carries_a_floor_under_its_spread():
    model = {
        "compounds": {
            "MEDIUM": {
                "identified": True,
                "slope_s_per_lap": 0.12,
                "standard_error_s_per_lap": 0.0001,
                "run_count": 6,
            }
        }
    }
    evidence = field_evidence(model, "MEDIUM")
    assert evidence.sigma == field_learning.SIGMA_FLOOR
    assert evidence.sample_size == 6


def test_field_evidence_without_a_standard_error_is_offered_widely():
    model = {
        "compounds": {
            "MEDIUM": {
                "identified": True,
                "slope_s_per_lap": 0.12,
                "standard_error_s_per_lap": None,
                "run_count": 4,
            }
        }
    }
    evidence = field_evidence(model, "MEDIUM")
    assert evidence.sigma == field_learning.SIGMA_UNKNOWN


# --------------------------------------------------------------------------
# Degenerate input
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"drivers": []},
        {"drivers": [{"car_idx": 0}]},
        {"drivers": [{"car_idx": 0, "lap_history": [], "tyre_stints": []}]},
        {"drivers": [{"car_idx": None, "lap_history": [{"lap_num": 1, "lap_ms": 1}]}]},
    ],
)
def test_empty_or_malformed_fields_are_survivable(state):
    model = field_degradation(state)
    assert model["compounds"] == {}
    assert model["identified_compounds"] == []


def test_a_stint_with_no_compound_is_ignored():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["drivers"][0]["tyre_stints"][0]["compound"] = "UNKNOWN"
    observations, _ = field_laps(state)
    assert not [
        item for item in observations if item.car_idx == 0 and item.lap_num <= 14
    ]


# --------------------------------------------------------------------------
# The player is not part of the field
# --------------------------------------------------------------------------


def test_the_players_own_car_is_excluded_from_the_field():
    """Otherwise the personal and field estimates share laps.

    Blending two sources by precision assumes they are independent. If the
    player's own laps sat in both, the combination would claim a confidence
    neither source earned.
    """
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["player_car_index"] = 2
    observations, _ = field_laps(state)
    assert not [item for item in observations if item.car_idx == 2]
    assert {item.car_idx for item in observations} == {0, 1, 3, 4, 5}


def test_a_player_flagged_without_an_index_is_still_excluded():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["drivers"][3]["is_player"] = True
    observations, _ = field_laps(state)
    assert not [item for item in observations if item.car_idx == 3]


def test_excluding_the_player_does_not_break_identification():
    state = diverged_field(deg={"MEDIUM": 0.12, "HARD": 0.06})
    state["player_car_index"] = 0
    model = field_degradation(state)
    assert model["compounds"]["MEDIUM"]["slope_s_per_lap"] == pytest.approx(0.12, abs=1e-3)
    assert model["contributing_cars"] == 5
