"""What the driver is offered to choose between before the lights go out.

A driver deciding a race is choosing a *shape* - one stop or two - not between
five variants of the shape the model already prefers. Two things have to be true
for that choice to be worth making: every shape has to be on the table, and none
of them may be described in a way that flatters an unraceable plan.
"""

from __future__ import annotations

import pytest

from pitwall.strategy import StrategyEngine


def _plan(stops, box_laps, compounds, time_s, *, feasible=True, legal=True, wear=70.0):
    return {
        "stops_remaining": stops,
        "box_laps": list(box_laps),
        "compounds": list(compounds),
        "risk_adjusted_time_s": time_s,
        "projected_max_wear_pct": wear,
        "projected_finish_position": 8,
        "feasible": feasible,
        "legal": legal,
        "reason": "",
    }


def _rank(plan):
    return (float(plan["risk_adjusted_time_s"]),)


# ---------------------------------------------------------------------------
# Every shape reaches the table
# ---------------------------------------------------------------------------


def test_the_best_plan_of_each_shape_is_picked() -> None:
    plans = [
        _plan(1, [23], ["MEDIUM", "HARD"], 5000.0),
        _plan(1, [25], ["MEDIUM", "HARD"], 4980.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "MEDIUM"], 5010.0),
        _plan(3, [16, 27, 43], ["MEDIUM", "SOFT", "MEDIUM", "SOFT"], 5040.0),
    ]
    picked = StrategyEngine._best_per_shape(plans)

    assert [p["stops_remaining"] for p in picked] == [1, 2, 3]
    assert picked[0]["box_laps"] == [25], "should take the quicker one-stop"


def test_a_feasible_plan_beats_a_quicker_impossible_one() -> None:
    """Within a shape, achievability comes before modelled pace.

    Skipping a stop always models quicker. If that alone chose the
    representative, every shape would be represented by its most optimistic
    unraceable variant.
    """
    plans = [
        _plan(1, [23], ["MEDIUM", "HARD"], 4900.0, feasible=False, wear=104.0),
        _plan(1, [30], ["MEDIUM", "HARD"], 5100.0, feasible=True, wear=88.0),
    ]
    picked = StrategyEngine._best_per_shape(plans)
    assert len(picked) == 1
    assert picked[0]["box_laps"] == [30]


def test_an_impossible_shape_is_still_offered() -> None:
    """Showing nothing reads as "that option does not exist".

    A driver who wants to one-stop needs to be told it runs out of tyre, which
    is an answer they can act on. Silence is not.
    """
    plans = [
        _plan(1, [23], ["MEDIUM", "HARD"], 4900.0, feasible=False, wear=104.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "MEDIUM"], 5010.0),
    ]
    picked = StrategyEngine._best_per_shape(plans)
    assert [p["stops_remaining"] for p in picked] == [1, 2]


def test_a_disqualifying_shape_is_never_offered() -> None:
    # One dry compound all race is not a trade-off, it is a DQ.
    plans = [
        _plan(1, [23], ["MEDIUM", "MEDIUM"], 4900.0, legal=False),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "MEDIUM"], 5010.0),
    ]
    assert [p["stops_remaining"] for p in StrategyEngine._best_per_shape(plans)] == [2]


# ---------------------------------------------------------------------------
# How the choice is presented
# ---------------------------------------------------------------------------


def test_shapes_lead_with_the_achievable_ones() -> None:
    plans = [
        _plan(1, [23], ["MEDIUM", "HARD"], 4900.0, feasible=False, wear=91.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "MEDIUM"], 5010.0),
        _plan(3, [16, 27, 43], ["MEDIUM", "SOFT", "MEDIUM", "SOFT"], 5040.0),
    ]
    shapes = StrategyEngine._race_shapes(plans, _rank)

    assert [s["stops"] for s in shapes] == [2, 3, 1]
    assert [s["feasible"] for s in shapes] == [True, True, False]


def test_an_impossible_shape_is_never_given_a_time_advantage() -> None:
    """The bug this exists to prevent.

    On the grid a one-stop modelled 57 seconds *faster* than the recommendation
    while projecting 91% wear, because it skips a pit stop. Presented as
    "-57.7s" that is an argument for ruining your race. A plan that cannot be
    driven gets no time comparison at all - only the reason it fails.
    """
    plans = [
        _plan(1, [23], ["MEDIUM", "HARD"], 4900.0, feasible=False, wear=91.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "MEDIUM"], 5010.0),
    ]
    shapes = {s["stops"]: s for s in StrategyEngine._race_shapes(plans, _rank)}

    assert shapes[1]["cost_vs_best_s"] is None
    assert "91% wear" in shapes[1]["verdict"]
    assert shapes[2]["cost_vs_best_s"] == 0.0
    assert shapes[2]["verdict"] == "achievable"


def test_cost_is_measured_against_the_best_achievable_shape() -> None:
    plans = [
        _plan(1, [23], ["MEDIUM", "HARD"], 4900.0, feasible=False, wear=91.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "MEDIUM"], 5010.0),
        _plan(3, [16, 27, 43], ["MEDIUM", "SOFT", "MEDIUM", "SOFT"], 5040.0),
    ]
    shapes = {s["stops"]: s for s in StrategyEngine._race_shapes(plans, _rank)}
    # Not against the impossible one-stop, which is quicker on paper.
    assert shapes[3]["cost_vs_best_s"] == 30.0


def test_nothing_achievable_still_returns_the_options() -> None:
    # Late in a wrecked race every shape may be infeasible. The driver still has
    # to pick one, so they still have to be shown.
    plans = [
        _plan(1, [40], ["MEDIUM", "HARD"], 4900.0, feasible=False, wear=101.0),
        _plan(2, [40, 50], ["MEDIUM", "HARD", "SOFT"], 5010.0, feasible=False, wear=99.0),
    ]
    shapes = StrategyEngine._race_shapes(plans, _rank)
    assert len(shapes) == 2
    assert all(s["cost_vs_best_s"] is None for s in shapes)


def test_no_candidates_is_not_a_crash() -> None:
    assert StrategyEngine._race_shapes([], _rank) == []
    assert StrategyEngine._best_per_shape([]) == []


# ---------------------------------------------------------------------------
# End to end, on the grid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_grid_offers_a_real_choice_of_races(stack) -> None:
    """Lap 0, nothing run yet. This is the moment the panel has to fill."""
    store, _, strategy, _, _, _ = stack

    def on_the_grid(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 0
        state.total_laps = 57
        state.player_position = 8
        state.active_cars = 20
        state.track_id = 10
        state.player_car_index = 0
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 0
        state.tyre.wear = [0.0, 0.0, 0.0, 0.0]
        state.tyre_sets = [
            {"compound": "SOFT", "available": True},
            {"compound": "MEDIUM", "available": True},
            {"compound": "HARD", "available": True},
        ]
        player = state.drivers[0]
        player.car_idx = 0
        player.is_player = True
        player.pit_stops = 0
        player.tyre_compound = "MEDIUM"

    await store.mutate(on_the_grid)
    result = await strategy.recompute()

    assert result["available"] is True, result.get("reason")
    shapes = result["shapes"]
    # A one-stop and a two-stop at minimum: the choice a driver actually makes.
    assert {1, 2} <= {s["stops"] for s in shapes}
    for shape in shapes:
        assert shape["compounds"], shape
        assert len(shape["box_laps"]) == shape["stops"]
        if not shape["feasible"]:
            assert shape["cost_vs_best_s"] is None, shape


# ---------------------------------------------------------------------------
# Wet tyres are not a dry-race strategy
# ---------------------------------------------------------------------------


def test_a_dry_race_is_never_offered_intermediates() -> None:
    """Fitting inters in the dry waives the two-dry-compound rule.

    That makes such a plan "legal" when the honest dry equivalent is not, so on
    a first frame with an unknown fitted compound the only legal one-stop came
    back as a switch to intermediates. It is a rules loophole, not a race.
    """
    plans = [
        _plan(1, [10], ["MEDIUM", "INTER"], 4900.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "SOFT"], 5010.0),
    ]
    shapes = StrategyEngine._race_shapes(plans, _rank, allow_wet=False)
    assert [s["stops"] for s in shapes] == [2]


def test_when_it_rains_they_are_exactly_what_you_want() -> None:
    plans = [
        _plan(1, [10], ["MEDIUM", "INTER"], 4900.0),
        _plan(2, [16, 39], ["MEDIUM", "HARD", "SOFT"], 5010.0),
    ]
    shapes = StrategyEngine._race_shapes(plans, _rank, allow_wet=True)
    assert {s["stops"] for s in shapes} == {1, 2}


def test_the_tyre_already_fitted_is_never_filtered_out() -> None:
    # Already on inters, dry line coming: the plan to come off them must survive.
    plans = [_plan(1, [10], ["INTER", "MEDIUM"], 4900.0)]
    shapes = StrategyEngine._race_shapes(plans, _rank, allow_wet=False)
    assert [s["stops"] for s in shapes] == [1]
