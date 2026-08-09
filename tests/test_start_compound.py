"""Choosing the tyre you start the race on.

Before the lights this is genuinely the driver's decision - they fit it in the
garage - so "what does starting on softs cost me" is a question the engine has to
be able to answer. It could not: every enumeration branch began from the tyre
telemetry reported, and ``start_compound`` was written into the override by three
different code paths and read by none.

The danger in fixing it is the mirror image. Once a lap is run, the tyre on the
car is a fact. An engine still modelling the tyre someone once asked for would be
planning a race nobody is driving.
"""

from __future__ import annotations

import pytest

from pitwall.strategy import StrategyEngine

planned = StrategyEngine._planned_start_compound


def _state(**overrides):
    base = {
        "current_lap": 0,
        "player_car_index": 0,
        "drivers": [{"car_idx": 0, "pit_stops": 0}],
        "strategy_override": {
            "enabled": True,
            "start_compound": "SOFT",
            "start_compound_explicit": True,
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# When the choice is honoured
# ---------------------------------------------------------------------------


def test_a_different_start_tyre_is_honoured_on_the_grid() -> None:
    assert planned(_state(), "MEDIUM", 0) == "SOFT"


def test_it_still_applies_on_lap_one() -> None:
    # The grid reports lap 1 in most recordings; refusing there would mean the
    # choice almost never took effect.
    assert planned(_state(current_lap=1), "MEDIUM", 0) == "SOFT"


def test_wet_tyres_are_a_legitimate_choice() -> None:
    state = _state(
        strategy_override={
            "enabled": True,
            "start_compound": "INTER",
            "start_compound_explicit": True,
        }
    )
    assert planned(state, "MEDIUM", 0) == "INTER"


# ---------------------------------------------------------------------------
# When the tyre on the car wins
# ---------------------------------------------------------------------------


def test_once_the_race_is_running_the_fitted_tyre_is_a_fact() -> None:
    assert planned(_state(current_lap=2), "MEDIUM", 0) is None
    assert planned(_state(current_lap=30), "MEDIUM", 0) is None


def test_a_used_set_is_never_reimagined_as_fresh() -> None:
    # Age > 0 means laps have been run on it, whatever the lap counter says.
    assert planned(_state(), "MEDIUM", 3) is None


def test_after_a_stop_the_plan_does_not_argue_with_the_tyre() -> None:
    state = _state(drivers=[{"car_idx": 0, "pit_stops": 1}])
    assert planned(state, "MEDIUM", 0) is None


def test_asking_for_the_tyre_already_fitted_changes_nothing() -> None:
    assert planned(_state(), "SOFT", 0) is None


def test_an_override_that_is_off_is_ignored() -> None:
    state = _state(
        strategy_override={
            "enabled": False,
            "start_compound": "SOFT",
            "start_compound_explicit": True,
        }
    )
    assert planned(state, "MEDIUM", 0) is None


def test_nonsense_is_ignored_rather_than_raced() -> None:
    for value in ("", None, "GRAVEL", "soft tyres please"):
        state = _state(
            strategy_override={
                "enabled": True,
                "start_compound": value,
                "start_compound_explicit": True,
            }
        )
        assert planned(state, "MEDIUM", 0) is None, value


def test_no_override_at_all() -> None:
    assert planned({"current_lap": 0}, "MEDIUM", 0) is None


# ---------------------------------------------------------------------------
# End to end: the answer actually changes
# ---------------------------------------------------------------------------


def _on_the_grid(state):
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = 1
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


@pytest.mark.asyncio
async def test_planning_a_soft_start_replans_the_whole_race(stack) -> None:
    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)

    on_mediums = await strategy.recompute()
    assert on_mediums["recommended"]["compounds"][0] == "MEDIUM"

    await store.update(
        strategy_override={
            "enabled": True,
            "locked": False,
            "plan": {},
            "plan_agreed": False,
            "start_compound": "SOFT",
            "start_compound_explicit": True,
            "start_compound_seen_fitted": "MEDIUM",
            "next_box_lap": None,
            "next_compound": None,
            "preferred_stops": None,
            "priority": "balanced",
            "source": "prerace",
            "note": "",
            "updated_at": 0.0,
        }
    )
    on_softs = await strategy.recompute()

    assert on_softs["recommended"]["compounds"][0] == "SOFT"
    for shape in on_softs["shapes"]:
        assert shape["compounds"][0] == "SOFT", shape
    # And it is a genuinely different race, not a relabelled one.
    assert (
        on_softs["recommended"]["box_lap"] != on_mediums["recommended"]["box_lap"]
        or on_softs["recommended"]["fit_compound"]
        != on_mediums["recommended"]["fit_compound"]
    )


@pytest.mark.asyncio
async def test_the_laps_still_add_up_with_a_chosen_start_tyre(stack) -> None:
    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)
    await store.update(
        strategy_override={
            "enabled": True,
            "locked": False,
            "plan": {},
            "plan_agreed": False,
            "start_compound": "HARD",
            "start_compound_explicit": True,
            "start_compound_seen_fitted": "MEDIUM",
            "next_box_lap": None,
            "next_compound": None,
            "preferred_stops": None,
            "priority": "balanced",
            "source": "prerace",
            "note": "",
            "updated_at": 0.0,
        }
    )

    result = await strategy.recompute()
    for shape in result["shapes"]:
        assert sum(shape["stint_laps"]) == result["laps_remaining"], shape


# ---------------------------------------------------------------------------
# Following the driver into the garage
# ---------------------------------------------------------------------------


def test_a_plan_that_merely_echoed_the_fitted_tyre_is_not_a_choice() -> None:
    """Every committed plan carries a first compound, usually the fitted one.

    Treating that as a decision would pin the engine to the tyre that happened
    to be on the car when the plan was agreed, so a driver who then fitted
    something else would be given a race modelled on a tyre they are not on.
    """
    state = _state(
        strategy_override={
            "enabled": True,
            "start_compound": "SOFT",
            "start_compound_explicit": False,
        }
    )
    assert planned(state, "MEDIUM", 0) is None


def test_fitting_a_different_tyre_afterwards_overrules_what_was_said() -> None:
    # Asked for softs while on mediums, then went and fitted hards. Bolting one
    # on is a stronger statement than asking for one.
    state = _state(
        strategy_override={
            "enabled": True,
            "start_compound": "SOFT",
            "start_compound_explicit": True,
            "start_compound_seen_fitted": "MEDIUM",
        }
    )
    assert planned(state, "HARD", 0) is None
    # ...but while the car is still on what it was, the choice stands.
    assert planned(state, "MEDIUM", 0) == "SOFT"


def test_fitting_exactly_what_was_asked_for_needs_no_override() -> None:
    state = _state(
        strategy_override={
            "enabled": True,
            "start_compound": "SOFT",
            "start_compound_explicit": True,
            "start_compound_seen_fitted": "MEDIUM",
        }
    )
    assert planned(state, "SOFT", 0) is None


@pytest.mark.asyncio
async def test_committing_a_plan_does_not_pin_the_starting_tyre(stack) -> None:
    """End to end, the case the driver asked about.

    Agree a plan without touching the start tyre, then change tyres in the
    garage. The engine has to follow the car.
    """
    from pitwall.prerace import PreRacePlanner

    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)

    await planner.propose()
    agreed = await planner.respond("lock it in")
    assert agreed["committed"] is True

    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["start_compound_explicit"] is False

    # Driver goes to the garage and bolts on softs instead.
    await store.mutate(lambda s: setattr(s.tyre, "compound", "SOFT"))
    result = await strategy.recompute()

    assert result["recommended"]["compounds"][0] == "SOFT"
    for shape in result["shapes"]:
        assert shape["compounds"][0] == "SOFT", shape


@pytest.mark.asyncio
async def test_asking_for_a_start_tyre_is_remembered_as_a_choice(stack) -> None:
    from pitwall.prerace import PreRacePlanner

    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)

    await planner.propose()
    await planner.respond("i want to start on softs")
    await planner.respond("lock it in")

    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["start_compound"] == "SOFT"
    assert override["start_compound_explicit"] is True
    assert override["start_compound_seen_fitted"] == "MEDIUM"

    result = await strategy.recompute()
    assert result["recommended"]["compounds"][0] == "SOFT"
