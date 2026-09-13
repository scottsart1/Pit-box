"""Frozen scenario oracles: finite tyres and executable stop schedules."""

import pytest

from pitwall.race_plan import normalise_plan


def set_info(index, compound, life=40, wear=0, **extra):
    return {"index": index, "compound": compound, "available": True,
            "wear_pct": wear, "usable_life_laps": life + (5 if extra.get("fitted") else 0), "life_span_laps": life,
            "lap_delta_ms": 0, **extra}


async def race(stack, *, remaining=12, sets=None, wear=20, compound="MEDIUM"):
    store, _, engine, *_ = stack
    def configure(state):
        state.mode_profile = "race"
        state.session_type = "Race"
        state.current_lap = 10
        state.total_laps = 9 + remaining
        state.track_id = 0
        state.player_position = 1
        state.active_cars = 1
        state.tyre.compound = compound
        state.tyre.age_laps = 5
        state.tyre.wear = [wear] * 4
        state.completed_laps = [{"lap_num": 1, "compound": "HARD", "valid": True,
                                 "lap_time_ms": 90000}]
        state.tyre_sets = sets or []
    await store.mutate(configure)
    snapshot = await store.snapshot_analysis()
    history = {"compounds": {name: {"max_wear_per_lap_pct": 1.0,
               "wheel_wear_per_lap_pct": [1.] * 4, "wear_sample_size": 20,
               "slope_s_per_lap": 0.01, "sample_size": 20}
               for name in ("SOFT", "MEDIUM", "HARD")}}
    return engine, snapshot, history


def test_driver_plan_can_request_a_distinct_set_of_the_same_compound():
    assert normalise_plan({"compounds": ["MEDIUM", "MEDIUM"],
                           "box_laps": [15]}, 30)["stops"] == 1


@pytest.mark.asyncio
async def test_exhausted_inventory_does_not_invent_fresh_sets(stack):
    engine, state, history = await race(stack, sets=[set_info(1, "HARD", available=False)])
    result = engine.compute(state, history)
    assert not any(p["stops_remaining"] for p in engine._candidate_pool), result["recommended"]


@pytest.mark.asyncio
async def test_fitted_available_set_is_not_a_fresh_spare(stack):
    engine, state, history = await race(stack, sets=[set_info(1, "MEDIUM", fitted=True)])
    state["fitted_tyre_set_idx"] = 1
    result = engine.compute(state, history)
    assert not any(p["stops_remaining"] for p in engine._candidate_pool), result["recommended"]


@pytest.mark.asyncio
async def test_same_compound_replacement_survives_other_available_compounds(stack):
    engine, state, history = await race(stack, wear=75, sets=[
        set_info(1, "MEDIUM"), set_info(2, "HARD", wear=65)])
    result = engine.compute(state, history)
    assert result["recommended"].get("fit_compound") == "MEDIUM", result["recommended"]


@pytest.mark.asyncio
async def test_inventory_ids_are_not_reused_as_fresh_tyres(stack):
    engine, state, history = await race(stack, remaining=40, sets=[
        set_info(1, "MEDIUM", 14), set_info(2, "HARD", 14)])
    engine.compute(state, history)
    for plan in engine._candidate_pool:
        if plan["feasible"]:
            assert plan["stops_remaining"] <= 2, plan
            assert "tyre_set_indices" in plan, plan
            ids = plan["tyre_set_indices"]
            assert len(ids) == len(set(ids)), plan


@pytest.mark.asyncio
async def test_nondominated_faster_worn_set_is_preserved(stack):
    engine, state, history = await race(stack, wear=75, sets=[
        set_info(1, "HARD", lap_delta_ms=2000), set_info(2, "HARD", wear=5)])
    result = engine.compute(state, history)
    assert result["recommended"].get("tyre_set_indices") == [2], result["recommended"]


@pytest.mark.asyncio
async def test_short_horizon_can_need_two_stops(stack):
    engine, state, history = await race(stack, remaining=12, sets=[
        set_info(0, "MEDIUM", 2, fitted=True), set_info(1, "HARD", 5),
        set_info(2, "MEDIUM", 5)])
    state["fitted_tyre_set_idx"] = 0
    result = engine.compute(state, history)
    rec = result["recommended"]
    assert rec["feasible"] and rec["stops_remaining"] == 2, rec
    assert sum(s["laps"] for s in rec["stint_models"]) == 12


@pytest.mark.asyncio
async def test_three_stop_search_includes_urgent_first_stop(stack):
    engine, state, history = await race(stack, remaining=36, sets=[
        set_info(0, "MEDIUM", 3, fitted=True), set_info(1, "HARD", 11),
        set_info(2, "MEDIUM", 11), set_info(3, "HARD", 11)])
    state["fitted_tyre_set_idx"] = 0
    result = engine.compute(state, history)
    rec = result["recommended"]
    assert rec["feasible"] and rec["stops_remaining"] == 3, rec
    assert rec["box_laps"][0] == 12, rec
    assert sum(s["laps"] for s in rec["stint_models"]) == 36


@pytest.mark.asyncio
async def test_unknown_stock_is_explicitly_conditional(stack):
    engine, state, history = await race(stack, wear=75)
    result = engine.compute(state, history)
    assert result["tyre_inventory"]["status"] == "unknown"
    for plan in engine._candidate_pool:
        if plan["stops_remaining"]:
            assert plan["inventory_feasible"] is None
            assert set(plan["tyre_set_indices"]) == {None}


@pytest.mark.asyncio
async def test_two_medium_spares_keep_the_second_sets_reported_wear(stack):
    engine, state, history = await race(stack, remaining=8, compound="HARD", sets=[
        set_info(0, "HARD", 1, fitted=True), set_info(1, "MEDIUM", 4),
        set_info(2, "MEDIUM", 3, wear=35)])
    state["fitted_tyre_set_idx"] = 0
    result = engine.compute(state, history)
    rec = result["recommended"]
    assert rec["feasible"] and rec["stops_remaining"] == 2, rec
    assert sorted(rec["tyre_set_indices"]) == [1, 2]
    wear_by_id = {stint["tyre_set_index"]: stint["starting_wear_pct"] for stint in rec["stint_models"][1:]}
    assert wear_by_id == {1: 0, 2: 35}
    state["tyre_sets"].reverse()
    reordered = engine.compute(state, history)["recommended"]
    assert reordered["tyre_set_indices"] == rec["tyre_set_indices"]
    assert reordered["projected_time_s"] == rec["projected_time_s"]


@pytest.mark.asyncio
async def test_red_flag_change_can_be_followed_by_a_later_stop(stack):
    engine, state, history = await race(stack, remaining=22, sets=[
        set_info(0, "MEDIUM", 1, fitted=True), set_info(1, "HARD", 11),
        set_info(2, "MEDIUM", 11)])
    state.update(fitted_tyre_set_idx=0, red_flag_active=True, race_control_phase="red_flag")
    result = engine.compute(state, history)
    # The other worker owns phase-specific pit costs; here the oracle covers
    # resource feasibility and that the free initial change consumes no lap.
    candidates = [p for p in engine._candidate_pool if p["feasible"] and p["stops_remaining"] == 2
                  and p["box_laps"][0] == state["current_lap"]]
    assert candidates, result["recommended"]
    assert all(p["stint_models"][0]["laps"] == 0 for p in candidates)
    assert all(sum(s["laps"] for s in p["stint_models"]) == 22 for p in candidates)


@pytest.mark.asyncio
async def test_red_flag_zero_lap_old_stint_can_remove_an_exhausted_set(stack):
    engine, state, history = await race(stack, remaining=8, wear=95, sets=[
        set_info(0, "MEDIUM", 0, fitted=True, usable_life_laps=3),
        set_info(1, "HARD", 8)])
    state.update(fitted_tyre_set_idx=0, red_flag_active=True, race_control_phase="red_flag")
    result = engine.compute(state, history)
    rec = result["recommended"]
    assert rec["feasible"] and rec["tyre_set_indices"] == [1], rec
    assert rec["stint_models"][0]["laps"] == 0
