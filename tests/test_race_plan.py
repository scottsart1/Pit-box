"""A driver's whole-race plan, projected onto the race that is actually happening.

The dangerous failure here is silent: a plan that stops matching any candidate
still reads as "your plan is active" on the dashboard while the engine quietly
ranks whatever it likes. So most of these tests are about what the plan looks
like *after* something has already gone differently.
"""

from __future__ import annotations

import pytest

from pitwall.race_plan import (
    PlanError,
    compound_rule_ok,
    describe_plan,
    normalise_plan,
    plan_matches,
    remaining_plan,
)

# A two-stopper at a 57-lap race: mediums, hards on 18, softs on 36.
TWO_STOP = {"compounds": ["MEDIUM", "HARD", "SOFT"], "box_laps": [18, 36]}


def _plan(**overrides):
    return normalise_plan({**TWO_STOP, **overrides}, total_laps=57)


# ---------------------------------------------------------------------------
# Stating the plan
# ---------------------------------------------------------------------------


def test_a_two_stop_plan_is_accepted() -> None:
    plan = _plan()
    assert plan["stops"] == 2
    assert plan["compounds"] == ["MEDIUM", "HARD", "SOFT"]
    assert plan["box_laps"] == [18, 36]


def test_a_no_stop_plan_is_a_plan() -> None:
    plan = normalise_plan({"compounds": ["HARD"], "box_laps": []}, total_laps=57)
    assert plan["stops"] == 0
    assert describe_plan(plan) == "No stop, hards to the flag."


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ({"compounds": [], "box_laps": []}, "compound for each stint"),
        ({"compounds": ["MEDIUM", "GRAVEL"], "box_laps": [18]}, "not a tyre"),
        ({"compounds": ["MEDIUM", "HARD"], "box_laps": []}, "box lap"),
        ({"compounds": ["MEDIUM", "HARD"], "box_laps": [18, 30]}, "box lap"),
        ({"compounds": ["MEDIUM", "HARD", "SOFT"], "box_laps": [30, 18]}, "after the one before"),
        ({"compounds": ["MEDIUM", "MEDIUM"], "box_laps": [18]}, "back to back"),
        ({"compounds": ["MEDIUM", "HARD"], "box_laps": [0]}, "start at lap 1"),
        ({"compounds": ["MEDIUM", "HARD"], "box_laps": [57]}, "too late"),
    ],
)
def test_unraceable_plans_are_refused_in_plain_words(raw, fragment) -> None:
    with pytest.raises(PlanError) as caught:
        normalise_plan(raw, total_laps=57)
    # These sentences go back over the radio, so they have to read like speech.
    assert fragment in str(caught.value)


def test_a_plan_that_would_be_disqualified_is_flagged() -> None:
    # One dry compound all race is a DQ, not a strategy.
    one_compound = normalise_plan(
        {"compounds": ["MEDIUM", "SOFT"], "box_laps": [18]}, total_laps=57
    )
    assert compound_rule_ok(one_compound)

    illegal = {"compounds": ["MEDIUM"], "box_laps": []}
    assert not compound_rule_ok(normalise_plan(illegal, total_laps=57))
    # Unless it rains, where the rule does not apply.
    assert compound_rule_ok(normalise_plan(illegal, total_laps=57), wet_race=True)


# ---------------------------------------------------------------------------
# Projecting it onto the remaining race
# ---------------------------------------------------------------------------


def test_before_the_first_stop_the_whole_plan_remains() -> None:
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=5)
    assert tail["box_laps"] == [18, 36]
    assert tail["compounds"] == ["MEDIUM", "HARD", "SOFT"]


def test_after_the_first_stop_only_the_second_remains() -> None:
    """The bug this exists to catch.

    Once a stop is made the ranker only ever produces one-stop candidates whose
    first compound is the hard now fitted. A plan still asserting two stops
    starting on mediums matches nothing at all.
    """
    tail = remaining_plan(_plan(), stops_completed=1, current_lap=20)
    assert tail["stops"] == 1
    assert tail["box_laps"] == [36]
    assert tail["compounds"] == ["HARD", "SOFT"]


def test_after_the_last_stop_the_plan_says_stay_out() -> None:
    tail = remaining_plan(_plan(), stops_completed=2, current_lap=40)
    assert tail["stops"] == 0
    assert tail["exhausted"] is True
    assert tail["compounds"] == ["SOFT"]


def test_a_stop_the_race_has_run_past_is_dropped() -> None:
    # Lap 30, still no stop: lap 18 is long gone and cannot be chased.
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=30)
    assert tail["box_laps"] == [36]
    assert tail["missed_stops"] == 1
    # And the tyre that stop would have fitted goes with it - the driver is
    # still on mediums and the next stop is the soft one.
    assert tail["compounds"] == ["MEDIUM", "SOFT"]


def test_being_a_lap_late_does_not_delete_your_own_stop() -> None:
    """Tolerance has to be applied consistently in both directions.

    The ranker will still match a stop planned for 18 at lap 20. If the
    projection dropped it at 19, the driver would lose the stop they were one
    lap away from making.
    """
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=19)
    assert tail["box_laps"] == [18, 36]
    assert tail["missed_stops"] == 0


def test_stopping_more_often_than_planned_does_not_go_negative() -> None:
    # An unplanned stop for damage; the plan has nothing left to say.
    tail = remaining_plan(_plan(), stops_completed=5, current_lap=40)
    assert tail["box_laps"] == []
    assert tail["stops"] == 0


# ---------------------------------------------------------------------------
# Matching candidates
# ---------------------------------------------------------------------------


def _candidate(stops, box_laps, compounds):
    return {"stops_remaining": stops, "box_laps": box_laps, "compounds": compounds}


def test_the_planned_candidate_matches() -> None:
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=5)
    assert plan_matches(
        _candidate(2, [18, 36], ["MEDIUM", "HARD", "SOFT"]), tail
    )


def test_a_nearby_lap_still_matches() -> None:
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=5)
    assert plan_matches(_candidate(2, [19, 35], ["MEDIUM", "HARD", "SOFT"]), tail)
    assert not plan_matches(_candidate(2, [24, 36], ["MEDIUM", "HARD", "SOFT"]), tail)


def test_the_wrong_tyre_never_matches_however_close_the_lap() -> None:
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=5)
    assert not plan_matches(_candidate(2, [18, 36], ["MEDIUM", "SOFT", "HARD"]), tail)


def test_the_wrong_number_of_stops_never_matches() -> None:
    tail = remaining_plan(_plan(), stops_completed=0, current_lap=5)
    assert not plan_matches(_candidate(1, [18], ["MEDIUM", "HARD"]), tail)


def test_the_fitted_tyre_is_not_the_drivers_to_override() -> None:
    """Matching ignores the candidate's first compound.

    After a stop onto hards, a plan written as MEDIUM-HARD-SOFT re-bases to a
    tail starting on HARD. But if the driver had taken softs instead of the
    planned hards, the tail still says HARD while telemetry says SOFT, and the
    remaining stop is what matters - not an argument about the tyre already on
    the car.
    """
    tail = remaining_plan(_plan(), stops_completed=1, current_lap=20)
    # The planned case: on hards, one stop left, fitting softs.
    assert plan_matches(_candidate(1, [36], ["HARD", "SOFT"]), tail)
    # Took softs instead of the planned hards, or pitted for inters in a shower.
    # The remaining stop is unchanged and still matches, because the tyre
    # already bolted on is a fact, not a preference to be argued with.
    assert plan_matches(_candidate(1, [36], ["INTER", "SOFT"]), tail)
    # What must still not match is the wrong tyre at the stop that is left.
    assert not plan_matches(_candidate(1, [36], ["HARD", "MEDIUM"]), tail)


def test_a_finished_plan_constrains_to_staying_out() -> None:
    tail = remaining_plan(_plan(), stops_completed=2, current_lap=40)
    assert plan_matches(_candidate(0, [], ["SOFT"]), tail)
    assert not plan_matches(_candidate(1, [45], ["SOFT", "MEDIUM"]), tail)


# ---------------------------------------------------------------------------
# Saying it out loud
# ---------------------------------------------------------------------------


def test_the_plan_reads_like_an_engineer_said_it() -> None:
    assert describe_plan(_plan()) == (
        "2-stop: start on mediums, box lap 18 for hards, box lap 36 for softs."
    )
