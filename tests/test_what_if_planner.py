"""A what-if is priced by the planner it is compared with.

The old what-if summed two stints and a stop on a dry track and set that
against the recommended plan's Monte Carlo quantile. On a wet track the
engine's own recommended intermediate stop priced six minutes slower than
itself, and slicks priced ahead of intermediates in heavy rain.
"""

from __future__ import annotations

import pytest

from tools.strategy_probe import build_field, race_basics, wet_setup


def _dry_race(state, *, current_lap=11, total_laps=30, phase="green"):
    race_basics(state, total_laps=total_laps, current_lap=current_lap, position=6)
    state.weather = "Clear"
    state.race_control_phase = phase
    state.safety_car = "full" if phase == "safety_car" else "none"
    state.tyre.compound, state.tyre.age_laps = "MEDIUM", max(0, current_lap - 1)
    state.tyre.wear = [2.5 * max(0, current_lap - 1)] * 4
    state.tyre_sets = [
        {"compound": name, "available": True, "index": index}
        for index, name in enumerate(("SOFT", "MEDIUM", "HARD", "HARD"))
    ]
    state.drivers = build_field(
        current_lap=max(1, current_lap), player_position=6, compound="MEDIUM",
        tyre_age=max(0, current_lap - 1),
    )


@pytest.mark.asyncio
async def test_what_if_for_the_recommended_stop_is_the_recommended_plan(stack):
    store, _, strategy, *_ = stack
    await store.mutate(_dry_race)
    plan = (await strategy.recompute())["recommended"]
    assert plan["box_lap"] is not None
    result = await strategy.what_if(
        "same stop", lap=int(plan["box_lap"]), compound=str(plan["fit_compound"])
    )
    assert result["available"] is True
    assert result["matches_recommended"] is True
    assert result["delta_to_best_s"] == 0
    assert result["position_delta_to_best"] == 0


@pytest.mark.asyncio
async def test_what_if_prices_an_intermediate_on_the_track_it_will_run_on(stack):
    store, _, strategy, *_ = stack

    def setup(state):
        wet_setup(state, total_laps=40, current_lap=15, position=10)
        state.tyre.compound, state.tyre.age_laps, state.tyre.wear = "MEDIUM", 14, [30.0] * 4
        state.completed_laps = [
            {"lap_num": n, "lap_time_ms": 90_000 if n < 11 else 104_000, "valid": True,
             "pit_status": 0, "weather": "Clear" if n < 11 else "Heavy rain",
             "compound": "MEDIUM", "track_temp_c": 24}
            for n in range(1, 15)
        ]
        state.drivers = build_field(
            current_lap=15, player_position=10, compound="MEDIUM", tyre_age=14,
            lap_ms=lambda n: 90_000 if n < 11 else 104_000,
        )

    await store.mutate(setup)
    inter = await strategy.what_if("box for inters", lap=15, compound="INTER")
    hard = await strategy.what_if("box for hards", lap=15, compound="HARD")
    assert inter["weather_priced"] is True
    assert inter["delta_to_best_s"] < hard["delta_to_best_s"]
    assert abs(inter["delta_to_best_s"]) < 5.0


@pytest.mark.asyncio
async def test_what_if_on_the_grid_has_no_phantom_lap(stack):
    store, _, strategy, *_ = stack
    await store.mutate(lambda state: _dry_race(state, current_lap=0, total_laps=30))
    result = await strategy.what_if("box lap 12 for hards", lap=12, compound="HARD")
    assert result["available"] is True
    # The planner's stints add up to the race distance.
    query = dict(await store.snapshot_analysis())
    query["strategy_override"] = {"enabled": True, "locked": True, "next_box_lap": 12,
                                  "next_compound": "HARD", "preferred_stops": 1}
    query["_what_if"] = True
    plan = strategy.compute(query)["recommended"]
    assert sum(stint["laps"] for stint in plan["stint_models"]) == 30


@pytest.mark.asyncio
async def test_what_if_under_a_safety_car_charges_green_for_a_later_lap(stack):
    store, _, strategy, *_ = stack
    await store.mutate(lambda state: _dry_race(state, phase="safety_car"))
    query = dict(await store.snapshot_analysis())
    query["strategy_override"] = {"enabled": True, "locked": True, "next_box_lap": 16,
                                  "next_compound": "HARD", "preferred_stops": 1}
    query["_what_if"] = True
    result = strategy.compute(query)
    plan = result["recommended"]
    assert plan["box_laps"] == [16]
    assert plan["pit_stop_costs_s"][0] == result["neutralisation"]["base_pit_loss_s"]


@pytest.mark.asyncio
async def test_what_if_for_a_compound_with_no_set_says_so(stack):
    store, _, strategy, *_ = stack

    def setup(state):
        _dry_race(state)
        state.tyre_sets = [{"compound": "HARD", "available": True, "index": 0},
                           {"compound": "MEDIUM", "available": True, "index": 1}]

    await store.mutate(setup)
    result = await strategy.what_if("box lap 14 for softs")
    assert result["available"] is False
    assert result["reason"] == "No soft set can be fitted at that stop."
    assert (result["box_lap"], result["compound"]) == (14, "SOFT")


@pytest.mark.asyncio
async def test_what_if_after_the_flag_is_refused(stack):
    store, _, strategy, *_ = stack
    await store.mutate(_dry_race)
    result = await strategy.what_if("box lap 30 for hards")
    assert result["available"] is False
    assert result["reason"] == "That stop is after the race distance."


@pytest.mark.asyncio
async def test_the_rivals_view_reports_where_the_player_is_projected(stack):
    from pitwall.engineering import strategic_rivals

    store, _, strategy, *_ = stack
    await store.mutate(_dry_race)
    plan = await strategy.recompute()
    view = strategic_rivals(await store.snapshot_analysis())
    assert view["available"] is True
    assert view["player_projected_position"] == plan["recommended"]["projected_finish_position"]
    assert view["player_expected_position"] == plan["recommended"]["expected_finish_position"]


@pytest.mark.asyncio
async def test_the_rivals_view_works_in_a_sprint(stack):
    from pitwall.engineering import strategic_rivals

    store, _, strategy, *_ = stack

    def sprint(state):
        _dry_race(state, total_laps=17)
        state.mode_profile = "sprint"
        state.session_type = "Sprint"

    await store.mutate(sprint)
    await strategy.recompute()
    assert strategic_rivals(await store.snapshot_analysis())["available"] is True
