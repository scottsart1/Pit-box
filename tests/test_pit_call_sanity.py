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

from pitwall.brain import EngineerBrain  # noqa: E402

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
