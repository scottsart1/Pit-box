"""Observed weather overrides must preserve executable physical tyre plans."""

import pytest


async def wet_inventory_race(stack, compound="WET", *, passed_entry=False, spare_count=2):
    store, _, engine, *_ = stack
    await store.update(
        mode_profile="race", session_type="Race", current_lap=10, total_laps=27,
        track_id=11, player_car_index=0, player_position=1, active_cars=1,
        weather="Heavy rain" if compound == "WET" else "Light rain",
        tyre={"compound": "HARD", "age_laps": 5, "wear": [20] * 4},
        track_length_m=5800, lap_distance_m=5750 if passed_entry else 100,
        fitted_tyre_set_idx=0,
        tyre_sets=[{"index": 0, "compound": "HARD", "available": True,
                    "fitted": True, "wear_pct": 20, "life_span_laps": 2,
                    "usable_life_laps": 7}] + [
            {"index": index, "compound": compound, "available": True,
             "wear_pct": 0, "life_span_laps": 9, "usable_life_laps": 9}
            for index in range(1, spare_count + 1)
        ],
    )
    history = {"compounds": {name: {
        "max_wear_per_lap_pct": 1., "wheel_wear_per_lap_pct": [1.] * 4,
        "wear_sample_size": 20, "slope_s_per_lap": .01, "sample_size": 20,
    } for name in ("SOFT", "MEDIUM", "HARD", "INTER", "WET")}}
    return engine, await store.snapshot_analysis(), history


@pytest.mark.asyncio
@pytest.mark.parametrize("compound", ["WET", "INTER"])
@pytest.mark.parametrize("passed_entry", [False, True])
async def test_immediate_weather_uses_two_distinct_sets_when_one_cannot_finish(stack, compound, passed_entry):
    engine, state, history = await wet_inventory_race(stack, compound, passed_entry=passed_entry)
    result = engine.compute(state, history)
    plan = result["recommended"]
    # Independent resource oracle: 18 completions remain. Current set has two,
    # each spare has nine: one replacement cannot finish; two can (1+8+9 or
    # 2+7+9). A passed pit entry makes the first two old-tyre laps unavoidable.
    assert plan["feasible"] and plan["legal"], plan["instruction"]
    assert plan["stops_remaining"] == 2
    assert plan["box_laps"][0] == 10 + int(passed_entry)
    assert plan["compounds"] == ["HARD", compound, compound]
    assert sorted(plan["tyre_set_indices"]) == [1, 2]
    assert sum(stint["laps"] for stint in plan["stint_models"]) == 18
    assert all(stint["laps"] <= 9 for stint in plan["stint_models"][1:])
    assert "No feasible finish" not in plan["instruction"]
    assert plan["weather_crossover"]["compound"] == compound
    assert plan["weather_crossover"]["box_lap"] == plan["box_laps"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("spare_count", [0, 1])
async def test_insufficient_weather_inventory_keeps_an_explicit_unsupported_finish(stack, spare_count):
    engine, state, history = await wet_inventory_race(stack, spare_count=spare_count)
    result = engine.compute(state, history)
    plan = result["recommended"]
    assert not plan["feasible"] and not plan["finish_projection_valid"]
    assert "No feasible finish" in plan["instruction"]
    assert all(index in range(1, spare_count + 1) for index in plan["tyre_set_indices"])
    assert not any(candidate["feasible"] for candidate in engine._candidate_pool)
