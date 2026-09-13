"""D28/D29: explicit elapsed-time comparisons, not compound-name bonuses."""

from copy import deepcopy

import pytest

from pitwall.strategy import StrategyEngine


@pytest.mark.parametrize("player,rival,gap,traffic,expected", [
    ([91, 89], [92, 92], -1.2, 0, 2.8),
    ([91, 89], [92, 92], -1.2, 5, -2.2),
    ([90], [93], -0.5, 0, 2.5),
    ([94], [93], -0.5, 0, -1.5),
])
def test_independent_cut_window_arithmetic(player, rival, gap, traffic, expected):
    result = StrategyEngine._cut_window_margin(player, rival, gap, 24, 24, traffic)
    assert result["margin_s"] == pytest.approx(expected)


def test_cut_window_gap_and_stop_costs_share_one_time_axis():
    compare = StrategyEngine._cut_window_margin
    base = compare([90], [93], -0.5, 24, 24)["margin_s"]
    assert compare([90], [93], -1.5, 24, 24)["margin_s"] == pytest.approx(base - 1)
    assert compare([90], [93], -0.5, 29, 24)["margin_s"] == pytest.approx(base - 5)
    assert compare([90], [93], -0.5, 24, 29)["margin_s"] == pytest.approx(base + 5)
    assert compare([90], [93], 0.5, 24, 24)["margin_s"] == pytest.approx(base + 1)


async def _race(stack, *, spare=True):
    store, _, engine, *_ = stack
    await store.update(session_uid=44, mode_profile="race", session_type="Race",
                       track_id=11, current_lap=10, total_laps=25,
                       player_car_index=0, player_position=2, active_cars=2,
                       tyre={"compound": "MEDIUM", "age_laps": 8, "wear": [25] * 4},
                       fitted_tyre_set_idx=0,
                       tyre_sets=[{"index": 0, "compound": "MEDIUM", "available": True,
                                   "fitted": True, "life_span_laps": 20,
                                   "usable_life_laps": 30}] + ([{
                                       "index": 1, "compound": "HARD", "available": True,
                                       "wear_pct": 0, "life_span_laps": 30,
                                       "usable_life_laps": 30}] if spare else []))
    def rival(state):
        target = state.drivers[1]
        target.active = True
        target.position = 1
        target.name = "Target"
        target.current_lap = 10
        target.tyre_compound = "MEDIUM"
        target.tyre_age = 8
        target.gap_to_player_s = -1.2
        target.lap_history = [{"lap_num": n, "lap_ms": 92000, "valid_flags": 1}
                              for n in range(5, 10)]
    await store.mutate(rival)
    return store, engine


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,our_laps,their_laps,gap,traffic,expected", [
    ("undercut", [92, 91, 89], [92, 92, 92], -1.2, 0, 2.8),
    ("undercut", [92, 91, 89], [92, 92, 92], -1.2, 5, -2.2),
    ("overcut", [92, 90], [92, 93], -0.5, 0, 2.5),
    ("overcut", [92, 90], [92, 93], -5.5, 0, -2.5),
    ("overcut", [92, 94], [92, 93], -0.5, 0, -1.5),
])
async def test_public_cut_tools_use_modeled_laps_and_explicit_response(
    stack, monkeypatch, kind, our_laps, their_laps, gap, traffic, expected
):
    store, engine = await _race(stack)
    await store.mutate(lambda state: setattr(state.drivers[1], "gap_to_player_s", gap))
    before = await store.snapshot_analysis()
    sentinel = {"sentinel": "live candidate pool"}
    engine._candidate_pool = [sentinel]
    box = 10 if kind == "undercut" else 11
    def compute(separate_engine, state, historical=None):
        assert separate_engine is not engine
        assert state["strategy_override"]["next_box_lap"] == box
        return {"recommended": {
            "box_laps": [box], "compounds": ["MEDIUM", "HARD"],
            "stops_remaining": 1, "feasible": True, "legal": True,
            "inventory_status": "known", "inventory_feasible": True,
            "tyre_set_indices": [1], "pit_stop_costs_s": [24], "traffic_cost_s": traffic,
            "stint_models": [{"lap_times_s": our_laps}],
        }}
    monkeypatch.setattr(StrategyEngine, "compute", compute)
    monkeypatch.setattr(StrategyEngine, "_cut_rival_laps", lambda *a, **kw: {
        "lap_times_s": their_laps, "pace_reference": {"source": "test supplied laps"},
        "feasible": True,
    })
    result = await getattr(engine, "evaluate_" + kind)()
    assert result["available"] is True
    assert result["margin_s"] == pytest.approx(expected)
    assert result["confidence"] == "low"
    assert result["conditional"] is True
    assert result["rival_response_assumed"] is True
    assert result["player_lap_times_s"] == our_laps
    assert result["rival_lap_times_s"] == their_laps
    assert result["tyre_set_indices"] == [1]
    assert engine._candidate_pool == [sentinel]
    after = await store.snapshot_analysis()
    for key in ("strategy", "strategy_override", "tyre", "tyre_sets"):
        assert after[key] == before[key]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["undercut", "overcut"])
async def test_cut_queries_cannot_offer_a_stop_without_a_physical_spare(stack, kind):
    _, engine = await _race(stack, spare=False)
    result = await getattr(engine, "evaluate_" + kind)()
    assert result["available"] is False
    assert "feasible" in result["reason"].lower()


@pytest.mark.asyncio
async def test_passed_pit_entry_prevents_an_immediate_undercut(stack):
    store, engine = await _race(stack)
    await store.update(track_length_m=5000, lap_distance_m=4990)
    result = await engine.evaluate_undercut()
    assert result["available"] is False
    assert "entry" in result["reason"].lower()


@pytest.mark.asyncio
async def test_actual_main_candidate_supplies_player_cut_laps_and_inventory(stack):
    store, engine = await _race(stack)
    state = await store.snapshot_analysis()
    probe_state = deepcopy(state)
    probe_state["strategy_override"] = {"enabled": True, "locked": True, "next_box_lap": 10}
    history = await engine.database.tyre_history_model(11, context=state)
    expected = StrategyEngine(store, engine.database).compute(probe_state, history)["recommended"]
    result = await engine.evaluate_undercut()
    assert result["available"] is True, result
    assert expected["legal"] and expected["feasible"]
    assert result["tyre_set_indices"] == expected["tyre_set_indices"]
    laps = [value for stint in expected["stint_models"] for value in stint["lap_times_s"]]
    assert result["player_lap_times_s"] == laps[:3]
    assert result["player_pit_loss_s"] == expected["pit_stop_costs_s"][0]
    assert result["traffic_penalty_s"] == expected["traffic_cost_s"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["undercut", "overcut"])
async def test_unknown_stock_remains_a_low_confidence_condition(stack, kind):
    store, engine = await _race(stack)
    await store.update(tyre_sets=[])
    result = await getattr(engine, "evaluate_" + kind)()
    assert result["available"] is True, result
    assert result["inventory_status"] == "unknown"
    assert result["inventory_feasible"] is None
    assert result["conditional"] is True
    assert result["confidence"] == "low"
    assert "sets must be available" in result["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change,reason", [
    ({"connected": False, "last_packet_at": 1.0}, "stale"),
    ({"total_laps": 10}, "distance"),
    ({"mode_profile": "practice"}, "distance"),
    ({"race_control_phase": "safety_car"}, "green"),
    ({"tyre": {"compound": "INTER", "age_laps": 8, "wear": [25] * 4}}, "wet"),
])
async def test_cut_window_declines_unsupported_racing_states(stack, change, reason):
    store, engine = await _race(stack)
    await store.update(**change)
    for function in (engine.evaluate_undercut, engine.evaluate_overcut):
        result = await function()
        assert result["available"] is False, result
        assert reason in result["reason"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("index,field,value,reason", [
    (1, "pit_status", 1, "in progress"),
    (1, "result_label", "Retired", "rival"),
    (0, "result_label", "Finished", "player"),
    (0, "pit_lane_timer_active", True, "player"),
])
async def test_cut_window_declines_finished_or_partly_served_stops(
    stack, index, field, value, reason
):
    store, engine = await _race(stack)
    def apply(state):
        state.drivers[index].active = True
        setattr(state.drivers[index], field, value)
    await store.mutate(apply)
    for function in (engine.evaluate_undercut, engine.evaluate_overcut):
        result = await function()
        assert result["available"] is False, result
        assert reason in result["reason"].lower()


def test_rival_observed_wear_loss_is_not_charged_twice(stack, monkeypatch):
    _, _, engine, *_ = stack
    target = {"tyre_compound": "MEDIUM", "tyre_age": 10, "tyre_wear": [70] * 4}
    monkeypatch.setattr(engine, "_deg_for", lambda *args: (0, "test", 0))
    monkeypatch.setattr(engine, "_wheel_wear_rates", lambda *args: ([0] * 4, "test", 0, {}))
    monkeypatch.setattr(engine, "_rival_pace_reference", lambda *args: {
        "observed_lap_s": 92, "normalized_lap_s": 92, "age_end_reference": True,
    })
    result = engine._cut_rival_laps({}, target, overcut=False)
    assert result["feasible"] is True
    assert result["lap_times_s"] == pytest.approx([92] * 3)
