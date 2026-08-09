"""Every plan must run exactly the laps that are left. No more, no less.

On the grid ``current_lap`` is 0, and the multi-stop branches derived the final
stint from the race distance rather than from what remained. The stints then
summed to ``total_laps + 1``: a phantom lap, roughly 95 seconds, added to every
multi-stop plan and to nothing else.

It never showed mid-race, because from lap 1 onwards the two expressions agree.
It showed exactly where the pre-race panel reads them - the same car, same tyres,
same race, projected P20 at lap 0 and P5 at lap 1.
"""

from __future__ import annotations

import pytest


def _race(state, *, current_lap, total_laps=57):
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = current_lap
    state.total_laps = total_laps
    state.player_position = 8
    state.active_cars = 20
    state.track_id = 10
    state.player_car_index = 0
    state.tyre.compound = "MEDIUM"
    state.tyre.age_laps = max(0, current_lap - 1)
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


def _laps_in(plan) -> int:
    return sum(int(stint["laps"]) for stint in plan.get("stint_models", []))


@pytest.mark.asyncio
@pytest.mark.parametrize("current_lap, expected", [(0, 57), (1, 57), (12, 46), (30, 28)])
async def test_every_plan_runs_exactly_the_laps_that_remain(
    stack, current_lap, expected
) -> None:
    store, _, strategy, _, _, _ = stack
    await store.mutate(lambda s: _race(s, current_lap=current_lap))

    result = await strategy.recompute()
    assert result["available"] is True
    assert result["laps_remaining"] == expected

    checked = 0
    for plan in result["plans"] + [result["recommended"]]:
        if not plan.get("stint_models"):
            continue
        checked += 1
        assert _laps_in(plan) == expected, (
            f"{plan.get('stops_remaining')}-stop at lap {current_lap} "
            f"runs {_laps_in(plan)} laps, race has {expected} left"
        )
    assert checked, "no plans carried stint models to check"

    # The top five are often all the same shape, so they alone do not exercise
    # the one-, two- and three-stop branches. The shapes list forces one of each.
    assert result["shapes"], "no shapes offered"
    for shape in result["shapes"]:
        assert sum(shape["stint_laps"]) == expected, (
            f"{shape['stops']}-stop shape at lap {current_lap} runs "
            f"{shape['stint_laps']} = {sum(shape['stint_laps'])}, "
            f"race has {expected} left"
        )
        assert len(shape["stint_laps"]) == shape["stops"] + 1, shape


@pytest.mark.asyncio
async def test_the_grid_and_lap_one_agree_on_a_two_stop(stack) -> None:
    """The symptom that exposed it.

    Lap 0 and lap 1 are one lap apart, so the same shape should project within
    a lap of itself. A whole phantom stint's worth of difference is the bug.
    """
    store, _, strategy, _, _, _ = stack

    projections = {}
    for lap in (0, 1):
        await store.mutate(lambda s, lap=lap: _race(s, current_lap=lap))
        result = await strategy.recompute()
        two_stop = next(
            (s for s in result["shapes"] if s["stops"] == 2), None
        )
        assert two_stop is not None, f"no two-stop shape at lap {lap}"
        projections[lap] = two_stop

    grid, racing = projections[0], projections[1]
    assert grid["feasible"] == racing["feasible"]
    assert abs(
        int(grid["projected_finish_position"] or 0)
        - int(racing["projected_finish_position"] or 0)
    ) <= 1, (grid, racing)


@pytest.mark.asyncio
async def test_a_three_stop_also_accounts_for_every_lap(stack) -> None:
    store, _, strategy, _, _, _ = stack
    await store.mutate(lambda s: _race(s, current_lap=0, total_laps=70))

    result = await strategy.recompute()
    three = [p for p in result["plans"] if p.get("stops_remaining") == 3]
    three += [
        p
        for p in [result["recommended"]]
        if p.get("stops_remaining") == 3 and p.get("stint_models")
    ]
    for plan in three:
        assert _laps_in(plan) == result["laps_remaining"], plan.get("box_laps")
