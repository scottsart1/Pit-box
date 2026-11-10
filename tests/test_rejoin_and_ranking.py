"""Where a stop rejoins, what it costs, and which plan is called.

Three defects made the stop call wrong once rivals were projected to stop:

* the rejoin of every plan was counted from the gaps on the lap in progress,
  so a stop on lap 30 rejoined behind cars that would have stopped on lap 8,
  and a car in the pit lane was counted at its pit-lane gap;
* among plans level on position and points the latest first stop won however
  much slower it was;
* the places a stop could win back were priced at one field-wide spacing and
  a tax that ignored the pace difference, then rounded and floored.
"""

from __future__ import annotations

import pytest

from pitwall.proactive import ProactiveEngineer
from pitwall.strategy import (
    REJOIN_CLEAR_AIR_S,
    PlanRanking,
    RejoinTable,
    StrategyEngine,
)

LAP_S = 90.0
PIT_S = 22.0


def _rival(name, gap, position, *, laps=20, lap_s=LAP_S, stops=(), cost=PIT_S, **extra):
    return {
        "driver": name, "position": position, "current_gap_s": gap,
        "lap_times_s": [lap_s] * laps,
        "likely_stop_offsets_laps": list(stops),
        "pit_stop_costs_s": [cost] * len(stops),
        **extra,
    }


def _state(position=5, active=16, **extra):
    return {"player_position": position, "active_cars": active, "drivers": [], **extra}


def _field_behind(count=10, *, stop_offset=8, start=5):
    """Cars 2 s apart behind the player, all projected to stop after ``stop_offset`` laps."""
    ahead = [_rival(f"AHEAD{n}", -10.0 * (start - n), n) for n in range(1, start)]
    behind = [
        _rival(f"CAR{n}", 2.0 * n - 0.5, start + n, stops=(stop_offset,))
        for n in range(1, count + 1)
    ]
    return ahead + behind


# --- RV-07: the rejoin of a stop on the lap it is made ----------------------------


def test_a_later_stop_rejoins_ahead_of_cars_that_have_stopped_by_then():
    table = RejoinTable(_state(), [LAP_S] * 20, _field_behind())
    late, late_lost = table(12, PIT_S)
    early, early_lost = table(6, PIT_S)
    assert abs(late - 5) <= 1 and late_lost == 0
    assert early == 15 and early_lost == 10


def test_places_lost_are_measured_against_not_stopping():
    """A car 3 s a lap quicker passes whether the player stops or not."""
    quick = _rival("QUICK", 10.0, 6, lap_s=LAP_S - 3.0)
    table = RejoinTable(_state(), [LAP_S] * 20, [*_field_behind(0), quick])
    rejoin, lost = table(10, PIT_S)
    assert rejoin == 6
    assert lost == 0


def test_a_car_in_the_pit_lane_is_not_counted_at_its_lane_gap():
    in_lane = {
        "car_idx": 7, "position": 6, "gap_to_player_s": 3.0, "result_label": "active",
        "pit_lane_timer_active": True, "pit_status": 1, "pit_lane_time_ms": 2_000,
    }
    racing = {**in_lane, "car_idx": 8, "position": 7, "pit_lane_timer_active": False, "pit_status": 0}
    state = _state(drivers=[in_lane, racing])
    # The racing car 3 s back passes during a 22 s stop; the one in the lane
    # still has 20 s of its own stop to lose.
    assert StrategyEngine._rejoin_position(state, PIT_S) == 6


def _projected(driver, **state_extra):
    state = {
        "player_car_index": 0, "player_position": 1, "track_id": 3, "race_control_phase": "green",
        "safety_car": "none", "mode_profile": "race", "current_lap": 10, "total_laps": 30,
        "drivers": [driver], **state_extra,
    }
    return StrategyEngine(None, None)._project_rival_finish_times(state, {}, 21, LAP_S, PIT_S)[0]


def _lane_driver(**extra):
    return {
        "car_idx": 1, "name": "LANE", "position": 2, "tyre_compound": "MEDIUM", "tyre_age": 30,
        "pit_stops": 0, "gap_to_player_s": 1.0, "result_label": "active",
        "tyre_stints": [{"start_lap": 1, "end_lap": 255, "compound": "MEDIUM"}],
        "lap_history": [{"lap_ms": 90_000, "valid_flags": 1}],
        "pit_lane_timer_active": True, "pit_status": 1, "pit_lane_time_ms": 5_000, **extra,
    }


def test_a_car_in_the_pit_lane_is_making_its_stop_not_owing_another():
    projection = _projected(_lane_driver())
    assert projection["in_pit_lane"] is True
    assert projection["lane_loss_remaining_s"] == pytest.approx(PIT_S - 5.0)
    # Thirty laps on mediums and a second compound owed: both are being dealt
    # with in the lane, so no stop is projected for the old set.
    assert projection["stop_owed"] is False
    assert 1 not in projection["likely_stop_offsets_laps"]
    assert projection["lane_compound"] in {"SOFT", "HARD"}


def test_a_car_on_track_still_owes_its_stop():
    projection = _projected(_lane_driver(pit_lane_timer_active=False, pit_status=0))
    assert projection["in_pit_lane"] is False
    assert projection["lane_loss_remaining_s"] == 0.0
    assert projection["stop_owed"] is True


def test_the_lane_loss_is_carried_into_the_rejoin_table():
    lane = _rival("LANE", 3.0, 6, lane_loss_remaining_s=20.0)
    table = RejoinTable(_state(), [LAP_S] * 20, [*_field_behind(0), lane])
    assert table(1, PIT_S)[0] == 5


# --- F-06: the rejoin map ---------------------------------------------------------


def test_the_rejoin_map_names_the_cars_either_side():
    field = [*_field_behind(0), _rival("TSUNODA", 19.0, 6), _rival("ALBON", 24.0, 7)]
    row = RejoinTable(_state(), [LAP_S] * 20, field).neighbours(3, PIT_S)
    assert row["rejoin_position"] == 6
    assert (row["car_ahead"], row["gap_ahead_s"]) == ("TSUNODA", 3.0)
    assert (row["car_behind"], row["gap_behind_s"]) == ("ALBON", 2.0)
    assert row["clear_air"] is True


def test_a_rival_that_stops_first_is_behind_on_the_map():
    field = [*_field_behind(0), _rival("EARLY", 5.0, 6, stops=(2,))]
    table = RejoinTable(_state(), [LAP_S] * 20, field)
    before = table.neighbours(1, PIT_S)
    after = table.neighbours(3, PIT_S)
    assert before["car_ahead"] == "EARLY" and before["rejoin_position"] == 6
    assert after["car_behind"] == "EARLY" and after["rejoin_position"] == 5


@pytest.mark.parametrize("gap,clear", [(1.9, False), (REJOIN_CLEAR_AIR_S, True), (2.5, True)])
def test_clear_air_needs_two_seconds(gap, clear):
    field = [*_field_behind(0), _rival("AHEAD", PIT_S - gap, 6)]
    row = RejoinTable(_state(), [LAP_S] * 20, field).neighbours(1, PIT_S)
    assert row["car_ahead"] == "AHEAD"
    assert row["clear_air"] is clear


def test_without_a_projected_field_clear_air_is_unknown():
    row = RejoinTable(_state(), [LAP_S] * 20, []).neighbours(1, PIT_S)
    assert row["clear_air"] is None and row["car_ahead"] is None


@pytest.mark.asyncio
async def test_the_strategy_publishes_a_rejoin_map_from_the_next_lap(stack):
    from tools.strategy_probe import build_field, race_basics

    store, _, strategy, *_ = stack

    def setup(state):
        race_basics(state, total_laps=30, current_lap=12, position=8)
        state.weather = "Clear"
        state.drivers = build_field(current_lap=12, player_position=8, compound="MEDIUM", tyre_age=11)
        state.strategy = {"game_rejoin_position": 15}

    await store.mutate(setup)
    result = await strategy.recompute()
    rows = result["rejoin_map"]
    assert [row["box_lap"] for row in rows] == list(range(12, 27))
    assert all({"rejoin_position", "car_ahead", "gap_ahead_s", "car_behind", "clear_air"} <= set(row) for row in rows)
    window = result["game_window"]
    assert window["engine_rejoin_position"] == rows[0]["rejoin_position"]
    assert window["rejoin_disagreement"] == abs(rows[0]["rejoin_position"] - 15)


@pytest.mark.asyncio
async def test_the_rejoin_map_tool_marks_the_recommended_lap(stack):
    from tools.strategy_probe import build_field, race_basics

    store, _, _, _, _, tools = stack

    def setup(state):
        race_basics(state, total_laps=30, current_lap=12, position=8)
        state.weather = "Clear"
        state.drivers = build_field(current_lap=12, player_position=8, compound="MEDIUM", tyre_age=11)

    await store.mutate(setup)
    report = await tools.get_rejoin_map()
    assert report["available"] is True
    assert report["clear_air_threshold_s"] == REJOIN_CLEAR_AIR_S
    marked = [row["box_lap"] for row in report["rows"] if row["recommended"]]
    assert marked == ([report["recommended_box_lap"]] if report["recommended_box_lap"] in
                      [row["box_lap"] for row in report["rows"]] else [])


def _strategy_with_row(**row):
    return {"rejoin_map": [{"box_lap": 14, "rejoin_position": 11, **row}]}


def _box_text(state):
    return ProactiveEngineer._fallback_text(
        {"type": "strategy_change", "payload": {"instruction": "Box this lap for hards.", "box_lap": 14}},
        state,
    )


def test_the_box_call_says_where_the_car_comes_out():
    state = {"current_lap": 14, "strategy": _strategy_with_row(
        car_ahead="TSUNODA", gap_ahead_s=2.8, clear_air=True)}
    assert _box_text(state).endswith("Box this lap for hards. You come out behind Tsunoda in clear air.")


def test_the_box_call_gives_the_gap_into_traffic():
    state = {"current_lap": 14, "strategy": _strategy_with_row(
        car_ahead="TSUNODA", gap_ahead_s=0.8, clear_air=False)}
    assert _box_text(state).endswith("You come out 0.8 seconds behind Tsunoda.")


def test_a_stop_several_laps_away_carries_no_rejoin_clause():
    state = {"current_lap": 10, "strategy": _strategy_with_row(
        car_ahead="TSUNODA", gap_ahead_s=2.8, clear_air=True)}
    assert "come out" not in _box_text(state)


# --- ST-01: the later stop only while it is close to free --------------------------


def _plan(box_lap, time_s, *, position=8, expected=None, points=4.0):
    return {
        "legal": True, "feasible": True, "box_laps": [box_lap], "stops_remaining": 1,
        "projected_finish_position": position, "points_expected": points,
        "expected_finish_position": position if expected is None else expected,
        "risk_adjusted_time_s": time_s,
    }


def _first(plans, **ranking):
    order = PlanRanking(total_laps=30, **ranking)
    order.fit(plans)
    return min(plans, key=order.key)["box_laps"][0]


def test_a_later_stop_is_preferred_when_it_costs_almost_nothing():
    assert _first([_plan(12, 1500.0), _plan(16, 1501.5)], tolerance_s=3.0) == 16


def test_a_later_stop_is_not_preferred_when_it_costs_ten_seconds():
    assert _first([_plan(12, 1500.0), _plan(16, 1510.0)], tolerance_s=3.0) == 12


def test_the_tolerance_is_a_setting(monkeypatch):
    from pitwall.config import settings

    plans = [_plan(12, 1500.0), _plan(16, 1510.0)]
    monkeypatch.setattr(settings, "strategy_later_stop_tolerance_s", 15.0)
    assert _first(plans) == 16
    monkeypatch.setattr(settings, "strategy_later_stop_tolerance_s", 3.0)
    assert _first(plans) == 12


def test_outside_the_points_the_expected_position_separates_plans():
    plans = [
        _plan(16, 1500.0, position=14, expected=14.6, points=0.0),
        _plan(12, 1500.5, position=14, expected=13.4, points=0.0),
    ]
    assert _first(plans, tolerance_s=3.0) == 12


def test_a_red_flag_change_ranks_on_time():
    assert _first([_plan(12, 1500.0), _plan(16, 1501.5)], tolerance_s=3.0, red_flag_change=True) == 12


# --- ST-02: places won back are bought car by car ----------------------------------


def _recovery_plan(deficits, *, laps_after=20, pace=LAP_S - 0.5, rejoin=14):
    return {
        "stops_remaining": 1, "box_laps": [10], "projected_rejoin_position": rejoin,
        "pit_stop_costs_s": [PIT_S], "cars_put_ahead_s": deficits,
        "stint_models": [{"lap_times_s": [pace] * laps_after}],
    }


def _rivals_between(current=5, rejoin=14, pace=LAP_S):
    return [{"position": position, "pace_s": pace, "current_gap_s": 2.0 * (position - current)}
            for position in range(current + 1, rejoin + 1)]


def _recovered(deficits, **plan):
    return StrategyEngine._expected_positions_recovered(
        _recovery_plan(deficits, **plan), {"player_position": 5}, _rivals_between(), 0.3
    )


def test_recovery_is_measured_against_the_cars_ahead_after_the_stop():
    # Half a second a lap for 20 laps, 60% of it kept: six seconds, enough
    # for the car a second ahead and part of the next, not for a car 18 s up.
    near = _recovered([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    far = _recovered([18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0])
    assert near > far


def test_a_strung_out_field_costs_more_to_recover_through_than_a_train():
    train = _recovered([1.0 * n for n in range(1, 10)])
    strung_out = _recovered([5.0 * n for n in range(1, 10)])
    assert train > strung_out


def test_a_fractional_place_is_kept_until_it_becomes_a_position():
    value = _recovered([1.0 * n for n in range(1, 10)])
    assert value != round(value, 0)
    assert value == StrategyEngine._expected_positions_recovered(
        _recovery_plan([1.0 * n for n in range(1, 10)]), {"player_position": 5}, _rivals_between(), 0.3
    )


@pytest.mark.parametrize("recovered,places", [(5.994, 5), (6.001, 6)])
def test_five_point_nine_nine_four_is_not_six(monkeypatch, recovered, places):
    """The floor applies to the unrounded estimate, once."""
    engine = StrategyEngine(None, None)
    monkeypatch.setattr(StrategyEngine, "_expected_positions_recovered",
                        staticmethod(lambda *args: recovered))
    plan = {
        "stops_remaining": 1, "box_laps": [10], "projected_rejoin_position": 14,
        "positions_lost_in_lane": 9, "projected_time_s": 2000.0, "pit_stop_costs_s": [PIT_S],
        "stint_models": [{"lap_times_s": [LAP_S] * 20}],
    }
    engine._annotate_finish_projection(plan, {"player_position": 5, "active_cars": 20}, [], 0.3, True)
    assert plan["positions_recoverable"] == places
    assert plan["expected_positions_recovered"] == round(recovered, 2)
