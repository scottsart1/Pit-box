"""Field-learned degradation reaching the strategy engine's actual output.

``test_field_learning`` proves the estimator recovers a known slope. These
tests prove the engine consults it: that a rival stops being modelled on
``DEFAULT_DEG * TRACK_TYRE_SEVERITY``, that the player's own estimate moves
when the field is the better evidence, and that a session which has taught the
model nothing behaves exactly as it did before any of this existed.
"""

from __future__ import annotations

import time

import pytest

from pitwall import field_learning
from pitwall.strategy import (
    DEFAULT_DEG,
    TRACK_TYRE_SEVERITY,
    StrategyEngine,
)

TRACK = 7  # Silverstone; severity 1.18, overtaking difficulty known.


def engine():
    return StrategyEngine.__new__(StrategyEngine)


def driver(car_idx, pace, plan, *, total_laps, deg, position=None, **overrides):
    """One car whose lap times come from a stated degradation model."""
    history, stints = [], []
    for start, end, compound in plan:
        last = end if end != field_learning.OPEN_STINT_END_LAP else total_laps
        stints.append({"start_lap": start, "end_lap": end, "compound": compound})
        for lap in range(start, min(last, total_laps) + 1):
            seconds = pace + deg.get(compound, 0.0) * (lap - start + 1)
            history.append(
                {"lap_num": lap, "lap_ms": round(seconds * 1000), "valid_flags": 1}
            )
    return {
        "car_idx": car_idx,
        "name": f"Car {car_idx}",
        "position": position if position is not None else car_idx + 1,
        "current_lap": total_laps,
        "tyre_compound": plan[-1][2],
        "tyre_age": total_laps - plan[-1][0] + 1,
        "gap_to_player_s": float(car_idx - 1) * 3.0,
        "lap_history": history,
        "tyre_stints": stints,
        **overrides,
    }


OPEN = field_learning.OPEN_STINT_END_LAP

# Six cars, every stop on a different lap, so tyre age has diverged from the
# lap counter and degradation is identifiable.
DIVERGED_PLANS = [
    (0, 90.0, [(1, 14, "MEDIUM"), (15, OPEN, "HARD")]),
    (1, 90.9, [(1, 18, "MEDIUM"), (19, OPEN, "HARD")]),
    (2, 91.8, [(1, 22, "MEDIUM"), (23, OPEN, "HARD")]),
    (3, 92.7, [(1, 16, "HARD"), (17, OPEN, "MEDIUM")]),
    (4, 93.6, [(1, 20, "HARD"), (21, OPEN, "MEDIUM")]),
    (5, 94.5, [(1, 24, "HARD"), (25, OPEN, "MEDIUM")]),
]

# The same six cars before anyone has stopped: every car at age N on lap N.
CONVOY_PLANS = [(idx, 90.0 + idx * 0.9, [(1, OPEN, "MEDIUM")]) for idx in range(6)]


def race_state(plans=DIVERGED_PLANS, *, total_laps=44, deg=None, current_lap=None):
    deg = deg or {"MEDIUM": 0.30, "HARD": 0.05}
    drivers = [
        driver(idx, pace, plan, total_laps=total_laps, deg=deg)
        for idx, pace, plan in plans
    ]
    drivers.append(
        {
            "car_idx": 9,
            "name": "Player",
            "is_player": True,
            "position": 7,
            "current_lap": total_laps,
            "tyre_compound": "MEDIUM",
            "tyre_age": 10,
            "gap_to_player_s": 0.0,
            "lap_history": [],
            "tyre_stints": [],
        }
    )
    return {
        "mode_profile": "race",
        "session_type": "Race",
        "track_id": TRACK,
        "session_uid": 1234,
        "current_lap": current_lap or total_laps,
        "total_laps": total_laps + 10,
        "player_position": 7,
        "player_car_index": 9,
        "active_cars": len(drivers),
        "race_control_phase": "green",
        "tyre": {"compound": "MEDIUM", "age_laps": 10, "wear": [30.0] * 4},
        "car_setup": {},
        "completed_laps": [],
        "drivers": drivers,
    }


def track_default(compound):
    return DEFAULT_DEG[compound] * TRACK_TYRE_SEVERITY[TRACK]


# --------------------------------------------------------------------------
# The rival path: the headline fix
# --------------------------------------------------------------------------


def test_rivals_stop_being_modelled_on_a_hand_written_constant():
    """The whole point. A rival's tyre now comes from the field's evidence."""
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    projections = engine()._project_rival_finish_times(state, {}, 6, 90.0, 22.0)
    medium = [item for item in projections if item["compound"] == "MEDIUM"]
    assert medium, "the field should include cars running mediums"
    for item in medium:
        assert "field_learned" in item["deg_source"]
        # The table would have said 0.106 s/lap at this circuit; the field ran
        # 0.30. The estimate must have moved decisively toward the evidence.
        assert item["deg_s_per_lap"] > track_default("MEDIUM") * 1.8
        assert item["deg_s_per_lap"] == pytest.approx(0.30, abs=0.05)


def test_rival_degradation_is_the_track_prior_again_when_the_field_is_silent():
    """Before the stops nothing is identified, so nothing should change."""
    state = race_state(CONVOY_PLANS, total_laps=30, deg={"MEDIUM": 0.30})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    projections = engine()._project_rival_finish_times(state, {}, 6, 90.0, 22.0)
    for item in projections:
        assert item["deg_source"] == "track_default"
        assert item["deg_s_per_lap"] == pytest.approx(
            track_default(item["compound"]), abs=1e-6
        )


def test_a_slow_degrading_field_pulls_rival_estimates_down_too():
    """The correction has to work in both directions, not just upward."""
    state = race_state(deg={"MEDIUM": 0.02, "HARD": 0.01})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    projections = engine()._project_rival_finish_times(state, {}, 6, 90.0, 22.0)
    medium = [item for item in projections if item["compound"] == "MEDIUM"]
    assert medium
    for item in medium:
        assert item["deg_s_per_lap"] < track_default("MEDIUM")


def _before_and_after(deg, remaining=8):
    state = race_state(deg=deg)
    bare = engine()._project_rival_finish_times(dict(state), {}, remaining, 90.0, 22.0)
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    learned = engine()._project_rival_finish_times(state, {}, remaining, 90.0, 22.0)
    return {item["driver"]: item for item in bare}, {
        item["driver"]: item for item in learned
    }


def test_a_steeper_learned_slope_slows_a_rival_that_will_not_stop():
    """A changed source is worthless if the projected laps do not move."""
    bare, learned = _before_and_after({"MEDIUM": 0.30, "HARD": 0.25})
    staying_out = [
        name
        for name, item in learned.items()
        if item["compound"] == "HARD" and item["likely_remaining_stops"] == 0
    ]
    assert staying_out, "the fixture needs rivals running to the flag"
    for name in staying_out:
        assert learned[name]["deg_s_per_lap"] > bare[name]["deg_s_per_lap"]
        assert learned[name]["finish_time_s"] > bare[name]["finish_time_s"] + 1.0


def test_a_steeper_slope_makes_a_worn_rival_project_faster_once_it_stops():
    """Not a bug: a steeper tyre means its stop recovers more.

    A rival observed at high tyre age is slow *because* of that tyre. Learning
    that the compound degrades harder than the table said lowers its
    fresh-tyre baseline, so the laps after its stop are quicker. The previous
    version of this test asserted the opposite and was wrong.
    """
    bare, learned = _before_and_after({"MEDIUM": 0.30, "HARD": 0.05})
    stopping = [
        name
        for name, item in learned.items()
        if item["compound"] == "MEDIUM"
        and item["likely_remaining_stops"] >= 1
        and item["tyre_age"] >= 24
    ]
    assert stopping, "the fixture needs rivals on worn tyres with a stop to come"
    for name in stopping:
        assert learned[name]["deg_s_per_lap"] > bare[name]["deg_s_per_lap"]
        assert learned[name]["finish_time_s"] < bare[name]["finish_time_s"]


def test_learning_moves_the_projection_materially_not_cosmetically():
    bare, learned = _before_and_after({"MEDIUM": 0.30, "HARD": 0.25})
    shifts = [
        abs(learned[name]["finish_time_s"] - bare[name]["finish_time_s"])
        for name in bare
    ]
    assert max(shifts) > 2.0


# --------------------------------------------------------------------------
# The player path
# --------------------------------------------------------------------------


def test_the_player_prior_moves_toward_the_field_with_no_personal_history():
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    value, source, samples = StrategyEngine._deg_for(state, "MEDIUM", {})
    assert "field_learned" in source and source.startswith("track_default")
    assert track_default("MEDIUM") < value <= 0.30
    assert samples > 0


def test_measured_personal_history_is_blended_with_the_field_not_replaced():
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    historical = {
        "compounds": {
            "MEDIUM": {
                "sample_size": 12,
                "slope_s_per_lap": 0.10,
                "slope_spread_s_per_lap": 0.02,
            }
        }
    }
    value, source, _ = StrategyEngine._deg_for(state, "MEDIUM", historical)
    assert source == "personal_track_history+field_learned"
    # Tight personal evidence dominates, but the field still pulls upward.
    assert 0.10 < value < 0.20


def test_weak_personal_history_yields_more_ground_to_the_field():
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)

    def blended(spread, samples):
        historical = {
            "compounds": {
                "MEDIUM": {
                    "sample_size": samples,
                    "slope_s_per_lap": 0.10,
                    "slope_spread_s_per_lap": spread,
                }
            }
        }
        return StrategyEngine._deg_for(state, "MEDIUM", historical)[0]

    assert blended(0.20, 3) > blended(0.01, 40)


def test_an_inferred_step_ratio_gives_way_to_a_measured_field_contrast():
    """A compound nobody ran was extrapolated through a 1.35 multiplier."""
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    historical = {
        "compounds": {
            "MEDIUM": {
                "inferred_deg_s_per_lap": 0.10,
                "inferred_from": ["HARD"],
            }
        }
    }
    value, source, _ = StrategyEngine._deg_for(state, "MEDIUM", historical)
    assert source == "inferred_from_hard+field_learned"
    assert value > 0.15


# --------------------------------------------------------------------------
# Behaviour preservation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "historical",
    [
        {},
        {"compounds": {"MEDIUM": {"sample_size": 12, "slope_s_per_lap": 0.10,
                                  "slope_spread_s_per_lap": 0.02}}},
        {"compounds": {"MEDIUM": {"inferred_deg_s_per_lap": 0.10,
                                  "inferred_from": ["HARD"]}}},
        {"compounds": {"MEDIUM": {"sample_size": 1, "slope_s_per_lap": 0.10}}},
    ],
)
def test_an_absent_field_model_leaves_every_branch_exactly_as_it_was(historical):
    """No field evidence must mean no change, on every path through _deg_for."""
    state = race_state(CONVOY_PLANS, total_laps=30, deg={"MEDIUM": 0.30})
    without = StrategyEngine._deg_for(dict(state), "MEDIUM", historical)
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    with_silent_field = StrategyEngine._deg_for(state, "MEDIUM", historical)
    assert without == with_silent_field
    assert "field_learned" not in without[1]


def test_a_malformed_field_model_cannot_break_the_estimate():
    state = race_state()
    for broken in (None, {}, {"compounds": None}, {"compounds": {"MEDIUM": None}},
                   {"compounds": {"MEDIUM": {"identified": True,
                                             "slope_s_per_lap": None}}}):
        state["_strategy_field_degradation"] = broken
        value, source, _ = StrategyEngine._deg_for(state, "MEDIUM", {})
        assert value == pytest.approx(track_default("MEDIUM"))
        assert source == "track_default"


def test_driver_feedback_still_multiplies_the_blended_estimate():
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    state["_strategy_field_degradation"] = field_learning.field_degradation(state)
    plain = StrategyEngine._deg_for(state, "MEDIUM", {})
    state["driver_tyre_feedback"] = {
        "category": "tyres_gone", "lap": state["current_lap"], "confidence": 1.0
    }
    with_feedback = StrategyEngine._deg_for(state, "MEDIUM", {})
    assert with_feedback[0] > plain[0]
    assert with_feedback[1].endswith("+driver_feedback")


# --------------------------------------------------------------------------
# End to end through compute()
# --------------------------------------------------------------------------


def test_compute_publishes_plans_and_reaches_the_field_model():
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05}, current_lap=30)
    state["total_laps"] = 50
    result = engine().compute(state, {"compounds": {}})
    assert result["available"], result.get("reason")
    assert result["plans"]
    identified = engine().field_degradation(state)["identified_compounds"]
    assert "MEDIUM" in identified and "HARD" in identified


def test_compute_is_unchanged_when_the_field_has_not_diverged():
    state = race_state(CONVOY_PLANS, total_laps=30, deg={"MEDIUM": 0.30})
    state["current_lap"] = 20
    state["total_laps"] = 40
    result = engine().compute(state, {"compounds": {}})
    assert result["available"], result.get("reason")
    sources = {
        stint.get("deg_source")
        for plan in result["plans"]
        for stint in plan.get("stint_models", [])
    }
    assert sources
    assert not any("field_learned" in str(item) for item in sources)


def test_the_field_fit_is_cached_per_lap_not_per_call():
    """recompute() runs several times a second; the fit must not."""
    instance = engine()
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    first = instance.field_degradation(state)
    assert instance.field_degradation(state) is first
    state["current_lap"] = int(state["current_lap"]) + 1
    assert instance.field_degradation(state) is not first


def test_a_full_field_fit_is_fast_enough_for_the_live_tick():
    """Twenty cars over a full race distance, refitted from scratch."""
    plans = [
        (idx, 90.0 + idx * 0.4,
         [(1, 12 + idx, "MEDIUM"), (13 + idx, OPEN, "HARD")])
        for idx in range(20)
    ]
    state = race_state(plans, total_laps=60, deg={"MEDIUM": 0.30, "HARD": 0.05})
    started = time.perf_counter()
    model = field_learning.field_degradation(state)
    elapsed = time.perf_counter() - started
    assert model["identified_compounds"]
    assert elapsed < 0.25, f"field fit took {elapsed:.3f}s"


def test_the_engine_survives_a_field_with_no_history_at_all():
    state = race_state(deg={"MEDIUM": 0.30, "HARD": 0.05})
    for item in state["drivers"]:
        item["lap_history"] = []
        item["tyre_stints"] = []
    model = engine().field_degradation(state)
    assert model["identified_compounds"] == []
    result = engine().compute(state, {"compounds": {}})
    assert result["available"], result.get("reason")
