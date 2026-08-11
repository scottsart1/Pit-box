"""Never tell a driver to pit for the tyre they just fitted.

From a real Mexico race, lap 33 of 37, one lap after a stop, on softs at age 0:

    "Box this lap for softs. The Safety Car makes the stop worthwhile..."
    "You're on fresh softs, so stay with the Safety Car queue..."

Both spoken, same lap, contradicting each other. The driver's reply was "I have
a feeling we fucked up". Throwing away a new set is bad; being told to do it
while also being told not to is what destroys trust in every other call.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall.brain import EngineerBrain
from pitwall.strategy import StrategyEngine

instruction = EngineerBrain._spoken_strategy_instruction


def test_it_does_not_ask_for_a_stop_onto_the_tyre_already_fitted() -> None:
    # The exact lap 33 situation.
    said = instruction(
        {"box_lap": 33, "fit_compound": "SOFT"},
        33,
        {"compound": "SOFT", "age_laps": 0},
    )
    assert not said.lower().startswith("box "), said
    assert "fresh" in said.lower()


def test_a_worn_set_of_the_same_compound_may_still_be_replaced() -> None:
    # Same compound, but genuinely old: a stop is legitimate here.
    said = instruction(
        {"box_lap": 30, "fit_compound": "SOFT"},
        30,
        {"compound": "SOFT", "age_laps": 18},
    )
    assert said == "Box this lap for softs."


def test_a_different_compound_is_always_allowed() -> None:
    said = instruction(
        {"box_lap": 30, "fit_compound": "HARD"},
        30,
        {"compound": "SOFT", "age_laps": 0},
    )
    assert said == "Box this lap for hards."


def test_a_future_stop_is_unaffected() -> None:
    # Planning a stop for a later lap is fine even on a fresh set: by lap 34
    # the tyre will not be fresh any more.
    said = instruction(
        {"box_lap": 34, "fit_compound": "SOFT"},
        30,
        {"compound": "SOFT", "age_laps": 0},
    )
    assert said == "Box lap 34 for softs."


def test_missing_tyre_state_keeps_the_old_behaviour() -> None:
    # Never let an absent reading suppress a legitimate pit call.
    assert instruction({"box_lap": 30, "fit_compound": "SOFT"}, 30) == (
        "Box this lap for softs."
    )
    assert instruction({"box_lap": 30, "fit_compound": "SOFT"}, 30, {}) == (
        "Box this lap for softs."
    )


def test_stay_out_is_unchanged() -> None:
    assert instruction({}, 12) == "Stay out to the finish."


# ---------------------------------------------------------------------------
# The race-deciding call: the engine's own numbers said the stop was worthless
# ---------------------------------------------------------------------------

# Verbatim from the stored payload of the lap-33 call in the real race. The
# driver was P7; he boxed and finished P20, last of the classified runners.
LAP33 = {
    "box_lap": 32,
    "fit_compound": "SOFT",
    "stops_remaining": 1,
    "positions_gained_vs_stay_out": 0,
    "positions_lost_by_stopping": 6,
    "projected_finish_position": 18,
    "net_gain_vs_stay_out_s": 3.96,
}


def _is_a_box_instruction(said: str) -> bool:
    """Whether this actually tells the driver to pit.

    Checked on the imperative, not the word: "boxing costs you 6 places" is a
    refusal that happens to contain "box".
    """
    return said.lower().startswith("box ")


def test_the_real_lap_33_call_is_refused() -> None:
    said = instruction(LAP33, 33, {"compound": "MEDIUM", "age_laps": 10})
    assert not _is_a_box_instruction(said), said
    assert said.lower().startswith("stay out")
    # And it says why, in places, because that is what the driver can act on.
    assert "6 places" in said


def test_a_stop_that_actually_gains_places_is_still_called() -> None:
    # The guard must not mute genuine opportunities — that would be a worse
    # failure than the one it prevents.
    said = instruction(
        {
            "box_lap": 20,
            "fit_compound": "SOFT",
            "positions_gained_vs_stay_out": 3,
            "positions_lost_by_stopping": 1,
        },
        20,
        {"compound": "MEDIUM", "age_laps": 14},
    )
    assert said == "Box this lap for softs."


def test_a_stop_costing_nothing_is_still_called() -> None:
    # Gains nothing but loses nothing either (a free stop under a red flag,
    # say). No reason to refuse it.
    said = instruction(
        {
            "box_lap": 20,
            "fit_compound": "HARD",
            "positions_gained_vs_stay_out": 0,
            "positions_lost_by_stopping": 0,
        },
        20,
        {"compound": "MEDIUM", "age_laps": 14},
    )
    assert said == "Box this lap for hards."


def test_absent_or_malformed_position_data_does_not_mute_the_call() -> None:
    # Never let a missing field silence a legitimate stop.
    base = {"box_lap": 20, "fit_compound": "SOFT"}
    tyre = {"compound": "MEDIUM", "age_laps": 14}
    assert instruction(base, 20, tyre) == "Box this lap for softs."
    assert instruction({**base, "positions_gained_vs_stay_out": None}, 20, tyre) == (
        "Box this lap for softs."
    )
    assert instruction(
        {**base, "positions_gained_vs_stay_out": "n/a", "positions_lost_by_stopping": "x"},
        20,
        tyre,
    ) == "Box this lap for softs."


def _recovery_scenario(difficulty, advantage_s, laps_after=30, rejoin=14, current=5):
    """The Las Vegas shape: mid-race stop that drops the car into traffic."""
    plan = {
        "stops_remaining": 1,
        "projected_rejoin_position": rejoin,
        "stint_models": [{"lap_times_s": [95.0 - advantage_s] * laps_after}],
    }
    state = {
        "player_position": current,
        "drivers": [
            {"position": index, "delta_to_leader_s": index * 1.8}
            for index in range(1, 21)
        ],
    }
    rivals = [{"pace_s": 95.0, "confidence": "high"} for _ in range(8)]
    return StrategyEngine._expected_positions_recovered(plan, state, rivals, difficulty)


def test_a_stop_with_no_pace_advantage_recovers_nothing():
    """Verbatim from the 2026-08-10 Las Vegas race.

    A lap-19 stop from P5 projected P6 after rejoining P14 — nine cars
    passed for free — because recovery was granted as `laps * (1 -
    difficulty) * 0.9` before pace was considered at all. At Las Vegas that
    is 0.7 cars a lap. The driver refused the stop and was right to.

    A position must be bought with a time advantage over the cars ahead.
    """
    assert _recovery_scenario(difficulty=0.22, advantage_s=0.0) == 0.0
    assert _recovery_scenario(difficulty=0.22, advantage_s=0.05) == 0.0


def test_recovery_is_bounded_by_what_the_time_advantage_can_buy():
    vegas = _recovery_scenario(difficulty=0.22, advantage_s=1.2)
    assert 3.0 <= vegas <= 7.0, "a real tyre offset wins places, but not all nine"

    # Same car, same offset, a track where nobody passes.
    monaco = _recovery_scenario(difficulty=0.95, advantage_s=1.2)
    assert monaco < vegas / 1.8, "Monaco must cost far more per position"

    # Fewer laps to use the advantage means fewer places.
    late = _recovery_scenario(difficulty=0.22, advantage_s=1.2, laps_after=8)
    assert late < vegas / 2


def test_recovery_never_exceeds_the_places_the_stop_gave_away():
    huge = _recovery_scenario(difficulty=0.20, advantage_s=6.0, rejoin=8, current=5)
    assert huge <= 3.0


def test_a_train_costs_more_per_position_than_a_strung_out_field():
    """Spacing is measured from the field, not assumed."""
    plan = {
        "stops_remaining": 1,
        "projected_rejoin_position": 14,
        "stint_models": [{"lap_times_s": [94.0] * 30}],
    }
    rivals = [{"pace_s": 95.0, "confidence": "high"} for _ in range(8)]
    strung_out = {
        "player_position": 5,
        "drivers": [
            {"position": i, "delta_to_leader_s": i * 6.0} for i in range(1, 21)
        ],
    }
    train = {
        "player_position": 5,
        "drivers": [
            {"position": i, "delta_to_leader_s": i * 0.8} for i in range(1, 21)
        ],
    }
    assert StrategyEngine._expected_positions_recovered(
        plan, train, rivals, 0.22
    ) > StrategyEngine._expected_positions_recovered(plan, strung_out, rivals, 0.22)
