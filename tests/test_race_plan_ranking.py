"""A committed plan has to actually steer the recommendation.

The unit tests next door prove the projection maths. These prove the wiring: a
plan that validates, re-bases and matches perfectly in isolation is worth
nothing if the ranker never consults it, and the failure mode is silent - the
dashboard says the plan is active while the engine ranks freely.
"""

from __future__ import annotations

import pytest

from pitwall.race_plan import normalise_plan
from pitwall.strategy import StrategyEngine


def _race(state, *, current_lap, total_laps=57, compound="MEDIUM", age=0, stops=0):
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = current_lap
    state.total_laps = total_laps
    state.player_position = 8
    state.active_cars = 20
    state.track_id = 10
    state.player_car_index = 0
    state.tyre.compound = compound
    state.tyre.age_laps = age
    state.tyre.wear = [22.0, 22.0, 26.0, 26.0]
    state.tyre_sets = [
        {"compound": "SOFT", "available": True},
        {"compound": "MEDIUM", "available": True},
        {"compound": "HARD", "available": True},
    ]
    player = state.drivers[0]
    player.car_idx = 0
    player.is_player = True
    player.pit_stops = stops
    player.tyre_compound = compound


def _commit(plan_dict, total_laps=57):
    plan = normalise_plan(plan_dict, total_laps=total_laps)
    return {
        "enabled": True,
        "locked": True,
        "plan": plan,
        "plan_agreed": True,
        "next_box_lap": None,
        "next_compound": None,
        "preferred_stops": plan["stops"],
        "start_compound": plan["compounds"][0],
        "priority": "balanced",
        "source": "test",
        "note": "",
        "updated_at": 0.0,
    }


@pytest.mark.asyncio
async def test_a_committed_two_stop_is_what_gets_recommended(stack):
    store, _, strategy, _, _, _ = stack

    await store.mutate(lambda state: _race(state, current_lap=5))
    await store.update(
        strategy_override=_commit(
            {"compounds": ["MEDIUM", "HARD", "SOFT"], "box_laps": [18, 36]}
        )
    )

    result = await strategy.recompute()
    best = result["recommended"]
    override = best.get("driver_override", {})

    assert override.get("following_plan") is True, override.get("warning")
    assert best["stops_remaining"] == 2
    assert best["fit_compound"] == "HARD"
    assert abs(int(best["box_lap"]) - 18) <= 2


@pytest.mark.asyncio
async def test_the_plan_survives_its_own_first_stop(stack):
    """The regression that motivated re-basing.

    Lap 20, one stop made, now on hards. Every candidate the ranker builds is a
    one-stopper starting on hards. A plan still asserting "two stops from
    mediums" would match nothing and quietly stop constraining anything.
    """
    store, _, strategy, _, _, _ = stack

    await store.mutate(
        lambda state: _race(state, current_lap=20, compound="HARD", age=2, stops=1)
    )
    await store.update(
        strategy_override=_commit(
            {"compounds": ["MEDIUM", "HARD", "SOFT"], "box_laps": [18, 36]}
        )
    )

    result = await strategy.recompute()
    best = result["recommended"]
    override = best.get("driver_override", {})

    assert override.get("following_plan") is True, override.get("warning")
    assert override["plan_remaining"]["box_laps"] == [36]
    assert best["stops_remaining"] == 1
    assert best["fit_compound"] == "SOFT"


@pytest.mark.asyncio
async def test_a_committed_one_stop_is_not_quietly_upgraded(stack):
    """The point of committing: the engine stops re-deciding the race shape.

    Without the plan the ranker is free to prefer a two-stop. With it, a driver
    who said one stop gets one stop.
    """
    store, _, strategy, _, _, _ = stack

    await store.mutate(lambda state: _race(state, current_lap=4))
    await store.update(
        strategy_override=_commit(
            {"compounds": ["MEDIUM", "HARD"], "box_laps": [28]}
        )
    )

    result = await strategy.recompute()
    best = result["recommended"]

    assert best["stops_remaining"] == 1
    assert best["fit_compound"] == "HARD"
    assert best["driver_override"]["following_plan"] is True


@pytest.mark.asyncio
async def test_a_plan_the_race_has_outrun_says_so_instead_of_pretending(stack):
    """Lap 50 of 57, no stop made, plan said 18 and 36.

    Both stops are gone. The plan cannot be followed, and the one thing that
    must not happen is the dashboard continuing to claim it is being followed.
    """
    store, _, strategy, _, _, _ = stack

    await store.mutate(
        lambda state: _race(state, current_lap=50, compound="MEDIUM", age=50)
    )
    await store.update(
        strategy_override=_commit(
            {"compounds": ["MEDIUM", "HARD", "SOFT"], "box_laps": [18, 36]}
        )
    )

    result = await strategy.recompute()
    override = result["recommended"].get("driver_override", {})

    assert override["active"] is True
    assert override["plan_remaining"]["missed_stops"] == 2
    assert override["plan_remaining"]["box_laps"] == []
    # It still has to produce a recommendation, and it has to be honest about
    # whether that recommendation is the driver's plan.
    assert result["recommended"]["instruction"]


@pytest.mark.asyncio
async def test_the_old_single_stop_override_still_works(stack):
    """The narrow override predates this and is still the voice path.

    "Box lap 30 for hards" over the radio must not have been broken by adding
    the whole-race plan beside it.
    """
    store, _, strategy, _, _, _ = stack

    await store.mutate(lambda state: _race(state, current_lap=12))
    await store.update(
        strategy_override={
            "enabled": True,
            "locked": True,
            "plan": {},
            "plan_agreed": False,
            "start_compound": None,
            "next_box_lap": 30,
            "next_compound": "HARD",
            "preferred_stops": 1,
            "priority": "balanced",
            "source": "voice",
            "note": "",
            "updated_at": 0.0,
        }
    )

    result = await strategy.recompute()
    best = result["recommended"]

    assert best["stops_remaining"] == 1
    assert best["fit_compound"] == "HARD"
    assert int(best["box_lap"]) == 30
    # The whole-race fields stay absent when no whole-race plan was set.
    assert "plan_remaining" not in best.get("driver_override", {})


@pytest.mark.asyncio
async def test_a_stale_stop_count_does_not_eat_the_first_stop(stack) -> None:
    """Found by driving a real dashboard, not by reading code.

    Telemetry left over from a previous session reported a completed stop while
    the car sat on lap 1. The plan was re-based past its own first stop, and the
    driver - who had agreed that plan seconds earlier - was told the race had
    moved past it. Nobody pits before the lights.
    """
    store, _, strategy, _, _, _ = stack

    def grid_with_stale_stops(state):
        _race(state, current_lap=1)
        state.drivers[0].pit_stops = 2

    await store.mutate(grid_with_stale_stops)
    await store.update(
        strategy_override=_commit(
            {"compounds": ["MEDIUM", "SOFT", "MEDIUM"], "box_laps": [21, 32]}
        )
    )

    result = await strategy.recompute()
    remaining = result["recommended"]["driver_override"]["plan_remaining"]

    assert remaining["box_laps"] == [21, 32]
    assert remaining["missed_stops"] == 0
    assert remaining["compounds"] == ["MEDIUM", "SOFT", "MEDIUM"]


def _cand(box_laps, time_s, compounds=("MEDIUM", "HARD", "SOFT")):
    return {
        "box_laps": list(box_laps),
        "compounds": list(compounds),
        "risk_adjusted_time_s": time_s,
        "stops_remaining": len(box_laps),
    }


def _by_time(plan):
    return (float(plan["risk_adjusted_time_s"]),)


def test_the_lap_the_driver_named_is_what_they_are_told() -> None:
    """Tolerance is licence to adapt, not licence to round.

    A driver who agreed laps 14 and 34 was answered "box lap 16", then "box lap
    36" - the tolerance maxed out every time. Within noise, their lap wins.
    """
    tail = {"box_laps": [14, 34], "lap_tolerance": 2}
    picked, note = StrategyEngine._closest_to_plan(
        [_cand([16, 36], 5000.0), _cand([14, 34], 5000.9)], tail, _by_time
    )
    assert picked["box_laps"] == [14, 34]
    assert note == "", "nothing moved, so there is nothing to explain"


def test_a_genuinely_better_lap_still_wins_and_is_explained() -> None:
    # Nine seconds is not rounding. The driver gets the better lap and the
    # reason - a stop that silently moves is a plan that stops feeling theirs.
    tail = {"box_laps": [14, 34], "lap_tolerance": 2}
    picked, note = StrategyEngine._closest_to_plan(
        [_cand([16, 36], 5000.0), _cand([14, 34], 5009.0)], tail, _by_time
    )
    assert picked["box_laps"] == [16, 36]
    assert "14 to 16" in note, note
    assert "9s better" in note, note


def test_it_moves_as_little_as_it_has_to() -> None:
    tail = {"box_laps": [14, 34], "lap_tolerance": 2}
    picked, _ = StrategyEngine._closest_to_plan(
        [_cand([16, 36], 5000.0), _cand([15, 34], 5000.4), _cand([14, 34], 5001.2)],
        tail,
        _by_time,
    )
    assert picked["box_laps"] == [14, 34]


def test_a_stay_out_plan_needs_no_lap_preference() -> None:
    picked, note = StrategyEngine._closest_to_plan(
        [_cand([], 5000.0, ("MEDIUM",))], {"box_laps": []}, _by_time
    )
    assert picked["box_laps"] == []
    assert note == ""
