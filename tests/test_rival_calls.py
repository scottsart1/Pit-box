"""Rival stops are called at pit entry, about the car that matters, with the cover arithmetic.

Stops were announced from the stop counter a lap late, for every car on track;
two stops in one pass kept the higher car index whichever was nearer; and any
car within 25 s behind raised "undercut threat" with the same generic advice.
"""

from __future__ import annotations

import pytest

from pitwall.proactive import ProactiveEngineer
from pitwall.state import StateStore
from pitwall.strategy import COVER_HOLD_MARGIN_S, StrategyEngine


class _Brain:
    database = None


class _Strategy:
    def __init__(self, covers=None):
        self.covers = covers or {}
        self.asked: list[int] = []

    async def recompute(self):
        return {}

    async def evaluate_cover(self, car_idx):
        self.asked.append(car_idx)
        return self.covers.get(car_idx, {"available": False})


def _engineer(covers=None) -> ProactiveEngineer:
    return ProactiveEngineer(StateStore(), _Brain(), None, None, _Strategy(covers))  # type: ignore[arg-type]


def _car(idx, position, gap, *, stops=0, in_pit=False, compound="MEDIUM", name=None, **extra):
    return {
        "car_idx": idx, "name": name or f"CAR{idx}", "position": position, "gap_to_player_s": gap,
        "pit_stops": stops, "pit_status": 1 if in_pit else 0, "pit_lane_timer_active": in_pit,
        "tyre_compound": compound, "active": True, **extra,
    }


def _state(*cars, player_position=2, mode="race", lap=12, **extra):
    player = _car(0, player_position, 0.0)
    return {
        "mode_profile": mode, "player_car_index": 0, "player_position": player_position,
        "current_lap": lap, "tyre": {"compound": "MEDIUM"}, "drivers": [player, *cars], **extra,
    }


async def _pass(engineer, state):
    """One detection pass, as _detect runs it."""
    await engineer._detect_rival_stops(state)
    engineer._last_pit_stops = {int(d["car_idx"]): int(d.get("pit_stops", 0)) for d in state["drivers"]}


def _calls(engineer, kind="rival_pitting"):
    return [item for item in engineer.pending if item["type"] == kind]


# --- RV-05: the stop that matters, at entry, once ------------------------------------


@pytest.mark.asyncio
async def test_a_stop_is_called_at_pit_entry():
    engineer = _engineer()
    await _pass(engineer, _state(_car(1, 3, 2.0)))
    await _pass(engineer, _state(_car(1, 3, 2.0, in_pit=True)))
    calls = _calls(engineer)
    assert len(calls) == 1
    assert calls[0]["payload"]["driver"] == "CAR1" and calls[0]["payload"]["seen_in_lane"] is True


@pytest.mark.asyncio
async def test_a_backmarker_stopping_is_not_radio():
    engineer = _engineer()
    await _pass(engineer, _state(_car(18, 19, 60.0)))
    await _pass(engineer, _state(_car(18, 19, 60.0, in_pit=True)))
    assert _calls(engineer) == []


@pytest.mark.asyncio
async def test_two_cars_stopping_together_names_the_nearer():
    covers = {3: {"available": True, "side": "behind", "cover_range_s": 3.0, "options": [
        {"box_in_laps": 1, "margin_s": 0.9, "holds": True}]}}
    engineer = _engineer(covers)
    before = _state(_car(3, 5, 1.8), _car(17, 6, 14.0), player_position=4)
    during = _state(_car(3, 5, 1.8, in_pit=True), _car(17, 6, 14.0, in_pit=True), player_position=4)
    await _pass(engineer, before)
    await _pass(engineer, during)
    calls = _calls(engineer)
    assert len(calls) == 1
    assert calls[0]["payload"]["driver"] == "CAR3"
    assert calls[0]["payload"]["also"] == ["CAR17"]


@pytest.mark.asyncio
async def test_a_car_twelve_seconds_back_is_not_an_undercut_threat():
    covers = {7: {"available": True, "side": "behind", "cover_range_s": 3.4, "options": [
        {"box_in_laps": 1, "margin_s": 9.0, "holds": True}]}}
    engineer = _engineer(covers)
    await _pass(engineer, _state(_car(7, 8, 12.0)))
    await _pass(engineer, _state(_car(7, 8, 12.0, in_pit=True)))
    assert _calls(engineer) == [] and _calls(engineer, "undercut_threat") == []


@pytest.mark.asyncio
async def test_a_stop_seen_at_entry_is_not_called_again_from_the_counter():
    engineer = _engineer()
    await _pass(engineer, _state(_car(1, 3, 2.0)))
    await _pass(engineer, _state(_car(1, 3, 2.0, in_pit=True)))
    await _pass(engineer, _state(_car(1, 3, 24.0, stops=1)))
    assert len(_calls(engineer)) == 1


@pytest.mark.asyncio
async def test_a_stop_the_lane_never_showed_comes_from_the_counter():
    engineer = _engineer()
    await _pass(engineer, _state(_car(1, 3, 2.0)))
    await _pass(engineer, _state(_car(1, 3, 24.0, stops=1)))
    calls = _calls(engineer)
    assert len(calls) == 1 and calls[0]["payload"]["seen_in_lane"] is False


@pytest.mark.asyncio
async def test_a_car_the_driver_asked_about_is_called_wherever_it_is():
    engineer = _engineer()
    instructions = [{"text": "tell me when Norris stops"}]
    await _pass(engineer, _state(_car(9, 12, 30.0, name="Norris"), standing_instructions=instructions))
    await _pass(engineer, _state(_car(9, 12, 30.0, name="Norris", in_pit=True), standing_instructions=instructions))
    assert [call["payload"]["driver"] for call in _calls(engineer)] == ["Norris"]


def test_a_team_mate_alone_is_not_radio():
    state = _state(_car(5, 15, 40.0, is_teammate=True))
    assert ProactiveEngineer._rival_relevance(state["drivers"][1], state, None) == 1


@pytest.mark.asyncio
async def test_practice_stops_are_not_radio():
    engineer = _engineer()
    await _pass(engineer, _state(_car(1, 3, 2.0), mode="practice"))
    await _pass(engineer, _state(_car(1, 3, 2.0, in_pit=True), mode="practice"))
    assert _calls(engineer) == []


# --- F-05: the cover arithmetic and what is said --------------------------------------


def _cover_state(*, gap=2.0, plan_laps=(90.0, 90.2, 90.4, 90.6), rival_age=15):
    rival = {
        "car_idx": 1, "name": "SAINZ", "position": 4, "gap_to_player_s": gap, "tyre_compound": "MEDIUM",
        "tyre_age": rival_age, "current_lap": 20, "pit_stops": 0, "pit_status": 1, "pit_lane_timer_active": True,
        "tyre_stints": [{"start_lap": 1, "end_lap": 255, "compound": "MEDIUM"}],
        "lap_history": [{"lap_num": n, "lap_ms": 90_500, "valid_flags": 1} for n in range(1, 20)],
    }
    return {
        "mode_profile": "race", "track_id": 3, "current_lap": 20, "total_laps": 50, "player_car_index": 0,
        "player_position": 3, "tyre": {"compound": "MEDIUM", "age_laps": 19}, "race_control_phase": "green",
        "safety_car": "none",
        "drivers": [{"car_idx": 0, "position": 3, "gap_to_player_s": 0.0}, rival],
        "strategy": {"plans": [{"stops_remaining": 0, "stint_models": [{"lap_times_s": list(plan_laps)}]}]},
    }


def test_cover_margin_is_gap_minus_what_fresh_tyres_gain():
    cover = StrategyEngine(None, None).cover_from_state(_cover_state(gap=2.0), 1)
    assert cover["available"] is True and cover["side"] == "behind"
    gains = cover["gain_per_lap_s"]
    for option in cover["options"]:
        k = option["box_in_laps"]
        assert option["margin_s"] == pytest.approx(2.0 - sum(gains[:k]), abs=0.02)
        assert option["holds"] is (option["margin_s"] >= COVER_HOLD_MARGIN_S)
    # The out-lap is on a cold set: the first lap gains less than the third.
    assert gains[0] < gains[2]


def test_the_player_laps_come_from_the_stay_out_plan():
    slow = StrategyEngine(None, None).cover_from_state(_cover_state(plan_laps=(91.0, 91.5, 92.0, 92.5)), 1)
    quick = StrategyEngine(None, None).cover_from_state(_cover_state(plan_laps=(89.0, 89.0, 89.0, 89.0)), 1)
    assert sum(slow["gain_per_lap_s"]) > sum(quick["gain_per_lap_s"])


def test_a_car_ahead_that_stops_gives_the_pace_to_find():
    cover = StrategyEngine(None, None).cover_from_state(_cover_state(gap=-1.5), 1)
    assert cover["side"] == "ahead" and cover["clear_air_laps"] == 2
    assert cover["pace_needed_s"] == pytest.approx(max(0.0, 1.5 + sum(cover["gain_per_lap_s"][:2])), abs=0.1)


def test_no_cover_arithmetic_on_wet_tyres():
    state = _cover_state()
    state["drivers"][1]["tyre_compound"] = "INTER"
    assert StrategyEngine(None, None).cover_from_state(state, 1)["available"] is False


def _said(**payload):
    return ProactiveEngineer._fallback_text({"type": "rival_pitting", "payload": payload}, {})


def test_a_cover_that_holds_is_called_with_its_margin():
    text = _said(driver="SAINZ", side="behind", gap_to_player_s=2.1, cover_holds_next_lap=True, cover_margin_s=0.8)
    assert text == "Sainz is in the pits, 2.1 behind. Box next lap and you stay ahead by 0.8."


def test_a_cover_that_fails_says_so():
    text = _said(driver="SAINZ", side="behind", gap_to_player_s=1.2, cover_holds_next_lap=False, cover_margin_s=-0.6)
    assert text == "Sainz is in the pits, 1.2 behind. You can't cover it; stay out and run your race."


def test_a_car_ahead_stopping_is_said_as_clear_air():
    text = _said(driver="SAINZ", side="ahead", gap_to_player_s=-1.5, clear_air_laps=2, pace_needed_s=3.4)
    assert text == "Sainz ahead is in. Clear air now: 2 laps to make 3.4 seconds."


def test_the_others_in_the_lane_are_named():
    text = _said(driver="SAINZ", side="behind", gap_to_player_s=2.1, also=["NORRIS"])
    assert text.endswith("Norris is in too.")


@pytest.mark.asyncio
async def test_the_window_call_is_made_once_a_stint():
    engineer = _engineer()
    cars = [(index, 10 + index, 20.0 + index) for index in range(1, 6)]
    on_track = _state(*[_car(i, p, g) for i, p, g in cars], strategy={"recommended": {"box_lap": 18}})
    await _pass(engineer, on_track)
    for number, (index, position, gap) in enumerate(cars):
        lap = 12 + number
        entering = [_car(i, p, g, in_pit=(i == index), stops=(1 if i < index else 0)) for i, p, g in cars]
        await _pass(engineer, {**on_track, "current_lap": lap, "drivers": [on_track["drivers"][0], *entering]})
        leaving = [_car(i, p, g, stops=(1 if i <= index else 0), compound="HARD" if i <= index else "MEDIUM")
                   for i, p, g in cars]
        await _pass(engineer, {**on_track, "current_lap": lap, "drivers": [on_track["drivers"][0], *leaving]})
    windows = _calls(engineer, "pit_window_open")
    assert len(windows) == 1
    payload = windows[0]["payload"]
    assert (payload["cars"], payload["compound"], payload["first_lap"], payload["last_lap"]) == (3, "MEDIUM", 12, 14)
    text = ProactiveEngineer._fallback_text(windows[0], {})
    assert text == "The window is open: 3 cars on mediums have stopped, laps 12 to 14. Ours is lap 18."


@pytest.mark.asyncio
async def test_the_cover_tool_resolves_the_car_by_name(stack):
    store, _, _, _, _, tools = stack
    state = _cover_state(gap=2.0)

    def setup(live):
        from pitwall.state import DriverState

        live.mode_profile, live.track_id, live.current_lap, live.total_laps = "race", 3, 20, 50
        live.player_car_index, live.player_position = 0, 3
        live.tyre.compound, live.tyre.age_laps = "MEDIUM", 19
        live.drivers[0] = DriverState(car_idx=0, name="ME", active=True, position=3, gap_to_player_s=0.0)
        rival = state["drivers"][1]
        live.drivers[1] = DriverState(
            car_idx=1, name="SAINZ", active=True, position=4, gap_to_player_s=2.0, tyre_compound="MEDIUM",
            tyre_age=15, current_lap=20, pit_status=1, pit_lane_timer_active=True,
        )
        live.drivers[1].tyre_stints = rival["tyre_stints"]
        live.drivers[1].lap_history = rival["lap_history"]

    await store.mutate(setup)
    cover = await tools.evaluate_cover("Sainz")
    assert cover["available"] is True and cover["driver"] == "SAINZ"
    assert [option["box_in_laps"] for option in cover["options"]] == [1, 2, 3]
    assert "Margin is the gap" in cover["basis"]
    assert (await tools.evaluate_cover("nobody at all"))["available"] is False
