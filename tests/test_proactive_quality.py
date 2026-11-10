"""What the driver actually hears from the unsolicited radio.

Each test drives the real detection and delivery path (or the real template)
through a situation from a race, and checks the words that reach the speaker:
a call that must be made, a call that must not be, or wording that must be
right.
"""

from __future__ import annotations

import json
import time as real_time
import types

import pytest

from pitwall import proactive as proactive_module
from pitwall.brain import EngineerBrain
from pitwall.config import settings
from pitwall.proactive import ProactiveEngineer

MOJIBAKE = ("Ã©", "Ã¨", "Ã¼", "Ã¶", "Ã±", "â€", "�")


class _Clock:
    """Stands in for the proactive module's clock so minutes pass instantly."""

    def __init__(self) -> None:
        self.offset = 0.0

    def monotonic(self) -> float:
        return 50_000.0 + self.offset

    def time(self) -> float:
        return real_time.time() + self.offset


class _Brain:
    """No model: every call falls back to its deterministic template."""

    def __init__(self, database, during=None, reply=None) -> None:
        self.database = database
        self.during = during
        self.reply = reply

    async def proactive(self, event):
        if self.during is not None:
            await self.during(event)
        if self.reply is None:
            raise RuntimeError("narration unavailable")
        return self.reply

    async def record_spoken_call(self, text):
        return None


class _Voice:
    is_busy = False
    busy_reason = ""
    realtime_active = False

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak_text(self, text):
        self.spoken.append(text)
        return True


class _Setup:
    async def learn_current_session(self):
        return True


class _Strategy:
    async def recompute(self):
        return {}


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(proactive_module, "time", fake)
    # A non-critical call waits for a calm stretch and a spacing interval.
    # Both are real-time properties of the car, not what is being tested.
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    return fake


async def _race(stack, brain=None, **values):
    store, database, *_ = stack
    voice = _Voice()
    engineer = ProactiveEngineer(
        store, brain or _Brain(database), voice, _Setup(), _Strategy()  # type: ignore[arg-type]
    )

    async def no_refresh(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(no_refresh, engineer)
    state = {
        "connected": True, "session_uid": 7, "game_paused": False,
        "mode_profile": "race", "current_lap": 10, "total_laps": 50,
        "speed_kph": 250, "throttle": 1.0, "brake": 0.0, "lateral_g": 0.2,
        "race_control_phase": "green", "fuel_laps_delta": 2.0,
        "track_length_m": 5400.0, "lap_distance_m": 1000.0,
    }
    state.update(values)
    await store.update(**state)
    await store.mutate(lambda s: s.proactive.update({"enabled": True, "cadence_laps": 99}))
    await engineer._reset_for_session(7)
    return store, engineer, voice


async def _tick(store, engineer, passes: int = 3) -> None:
    await engineer._detect(await store.snapshot_radio())
    for _ in range(passes):
        await engineer._deliver(await store.snapshot_radio())


def _set_tyre(**changes):
    def apply(state):
        for key, value in changes.items():
            setattr(state.tyre, key, value)
    return apply


# --- fuel ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_positive_fuel_margin_is_never_called_as_a_shortfall(stack, clock):
    """+0.2 laps is "on target" to the fuel model; the radio said "short by 0.2"."""
    store, engineer, voice = await _race(stack, fuel_laps_delta=0.2)
    for _ in range(4):
        await _tick(store, engineer)
        clock.offset += 200
    assert not [text for text in voice.spoken if "Fuel" in text], voice.spoken
    event = {"type": "fuel_warning", "payload": {}, "session_uid": 7, "expires_at": real_time.time() + 3600}
    assert ProactiveEngineer._event_still_relevant(event, {"session_uid": 7, "fuel_laps_delta": 0.2}) is False
    # The template never turns a surplus into a deficit, whatever reaches it.
    surplus = ProactiveEngineer._fallback_text({"type": "fuel_warning", "payload": {}}, {"fuel_laps_delta": 0.2})
    assert "short" not in surplus.lower() and "lift" not in surplus.lower()


@pytest.mark.asyncio
async def test_an_unchanged_fuel_shortfall_is_called_once_until_it_moves(stack, clock):
    store, engineer, voice = await _race(stack, fuel_laps_delta=-0.6)
    for _ in range(5):
        await _tick(store, engineer)
        clock.offset += 200
    fuel = [text for text in voice.spoken if text.startswith("Fuel")]
    assert fuel == ["Fuel is short by 0.6 laps. Start lifting and coasting on the long straights."]

    await store.update(fuel_laps_delta=-1.2)
    await _tick(store, engineer)
    fuel = [text for text in voice.spoken if text.startswith("Fuel")]
    assert len(fuel) == 2 and "1.2" in fuel[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("wander", [(-0.55, -0.45), (-0.15, -0.08)])
async def test_a_fuel_margin_wandering_by_tenths_is_called_once(stack, clock, wander):
    """The margin moves by tenths within a lap; that is not news every 90 s."""
    store, engineer, voice = await _race(stack, fuel_laps_delta=wander[0])
    for index in range(10):
        await store.update(fuel_laps_delta=wander[index % 2])
        await _tick(store, engineer)
        clock.offset += 95
    assert len([text for text in voice.spoken if text.startswith("Fuel")]) == 1, voice.spoken

    # Back to a real margin, then short again: that is a new shortfall.
    await store.update(fuel_laps_delta=0.2)
    await _tick(store, engineer)
    clock.offset += 95
    await store.update(fuel_laps_delta=-0.3)
    await _tick(store, engineer)
    assert len([text for text in voice.spoken if text.startswith("Fuel")]) == 2, voice.spoken


# --- repeat control -----------------------------------------------------------


@pytest.mark.asyncio
async def test_safety_car_delta_breaches_keep_being_called_while_they_last(stack, clock):
    """The delta warning repeats on a 12 s cooldown by design; the wording filter ate it."""
    store, engineer, voice = await _race(stack)
    await store.update(race_control_phase="safety_car", safety_car="full",
                       safety_car_delta_valid=True, safety_car_delta_s=-0.3)
    await _tick(store, engineer)
    clock.offset += 13
    await store.update(safety_car_delta_s=-1.4)
    await _tick(store, engineer)
    clock.offset += 13
    await _tick(store, engineer)
    delta_calls = [text for text in voice.spoken if "safety-car delta" in text]
    assert len(delta_calls) == 3, voice.spoken


@pytest.mark.asyncio
async def test_a_vsc_ending_is_announced_after_the_vsc(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(race_control_phase="vsc", safety_car="virtual")
    await _tick(store, engineer)
    clock.offset += 45
    await store.update(race_control_phase="vsc_ending")
    await _tick(store, engineer)
    race_control = [text for text in voice.spoken if text.startswith("Race control:")]
    assert race_control == ["Race control: VSC.", "Race control: VSC ending."], voice.spoken


@pytest.mark.asyncio
async def test_a_flapping_phase_is_not_announced_twice(stack, clock):
    """The guard the wording filter used to provide for race control stays."""
    store, engineer, voice = await _race(stack)
    await store.update(race_control_phase="safety_car", safety_car="full")
    await _tick(store, engineer)
    clock.offset += 2
    await store.update(race_control_phase="green", safety_car="none")
    await engineer._detect(await store.snapshot_radio())
    await store.update(race_control_phase="safety_car", safety_car="full")
    await _tick(store, engineer)
    assert voice.spoken.count("Race control: Safety car.") == 1, voice.spoken


@pytest.mark.asyncio
async def test_a_second_blue_flag_and_a_second_penalty_are_both_heard(stack, clock):
    store, engineer, voice = await _race(stack)
    for _ in range(2):
        await store.update(fia_flag="blue")
        await _tick(store, engineer)
        await store.update(fia_flag="none")
        await _tick(store, engineer)
        clock.offset += 20
    assert len([text for text in voice.spoken if text.startswith("Blue flag")]) == 2, voice.spoken

    await store.update(penalties_s=5)
    await _tick(store, engineer)
    clock.offset += 30
    await store.update(penalties_s=10)
    await _tick(store, engineer)
    penalties = [text for text in voice.spoken if "penalty" in text]
    assert penalties == [
        "You have picked up a 5 second penalty. Keep it clean from here.",
        "Another 5 second penalty; that is 10 seconds in total. Keep it clean from here.",
    ]


@pytest.mark.asyncio
async def test_a_tyre_warning_is_repeated_only_when_the_level_moves(stack, clock):
    """Six identical "protect the limiting tyre" calls in sixteen minutes, before."""
    store, engineer, voice = await _race(stack)
    await store.mutate(_set_tyre(wear=[50.0, 52.0, 71.0, 60.0]))
    for _ in range(6):
        await _tick(store, engineer)
        clock.offset += 150
    assert len([text for text in voice.spoken if text.startswith("Tyre warning")]) == 1, voice.spoken

    await store.mutate(_set_tyre(wear=[55.0, 57.0, 83.0, 70.0]))
    await _tick(store, engineer)
    tyre = [text for text in voice.spoken if text.startswith("Tyre warning")]
    assert len(tyre) == 2 and "83 percent" in tyre[-1]


def test_differently_typed_calls_with_the_same_content_are_still_swallowed(stack):
    """The case the wording filter exists for is untouched."""
    store, database, *_ = stack
    engineer = ProactiveEngineer(store, _Brain(database), _Voice(), _Setup(), _Strategy())  # type: ignore[arg-type]
    engineer._last_spoken_text = "Practice complete, P8; the qualifying reference is set."
    engineer._last_spoken_type = "progress_update"
    engineer._last_spoken_at = real_time.monotonic()
    event = {"type": "corner_coaching", "critical": False, "payload": {}}
    assert engineer._repeat_reason(
        event, "Practice complete, P8, and the qualifying reference is set."
    ) == "near-duplicate of the previous call"
    # A FLASH or safety call is never dropped for sounding like the last one.
    flash = {"type": "race_control", "flash": True, "critical": True, "payload": {"to": "green"}}
    assert engineer._repeat_reason(flash, "Practice complete, P8.") is None


# --- damage and power-unit wear -----------------------------------------------


@pytest.mark.asyncio
async def test_a_second_hit_inside_the_old_cooldown_is_called(stack, clock):
    """25% wing, then 65% thirty seconds later: the critical one was never said."""
    store, engineer, voice = await _race(stack)
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 25}))
    await _tick(store, engineer)
    clock.offset += 30
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 65}))
    await _tick(store, engineer)
    damage = [text for text in voice.spoken if "wing" in text.lower()]
    assert len(damage) == 2 and "65 percent" in damage[-1], voice.spoken

    # A fault that has already been reported is not the news of a later hit.
    clock.offset += 30
    await store.mutate(lambda s: s.damage.update({"drs_fault": True}))
    await _tick(store, engineer)
    clock.offset += 30
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 85}))
    await _tick(store, engineer)
    assert "85 percent" in voice.spoken[-1], voice.spoken


@pytest.mark.asyncio
async def test_every_worn_power_unit_component_is_named(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(component_wear={"ice": 74.0, "mguh": 78.0, "mguk": 72.0, "tc": 81.0, "es": 40.0})
    await _tick(store, engineer)
    text = next(text for text in voice.spoken if "wear" in text and "percent" in text)
    for name in ("ICE 74", "MGU-H 78", "MGU-K 72", "Turbo 81"):
        assert name in text, text


@pytest.mark.asyncio
async def test_a_new_hit_after_a_wing_change_is_called_however_soon(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 25}))
    await _tick(store, engineer)
    clock.offset += 40
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 0}))
    await _tick(store, engineer)
    clock.offset += 40
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 30}))
    await _tick(store, engineer)
    damage = [text for text in voice.spoken if "wing" in text.lower()]
    assert len(damage) == 2 and "30 percent" in damage[-1], voice.spoken


@pytest.mark.asyncio
async def test_a_component_held_back_by_the_cooldown_is_called_when_it_ends(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(component_wear={"ice": 74.0, "mguk": 40.0})
    await _tick(store, engineer)
    clock.offset += 30
    await store.update(component_wear={"ice": 74.0, "mguk": 71.0})
    await _tick(store, engineer)
    clock.offset += 120
    await _tick(store, engineer)
    assert [text for text in voice.spoken if "MGU-K" in text], voice.spoken


# --- FLASH calls --------------------------------------------------------------


def test_a_flash_call_outranks_a_critical_call_queued_before_it(stack):
    store, database, *_ = stack
    engineer = ProactiveEngineer(store, _Brain(database), _Voice(), _Setup(), _Strategy())  # type: ignore[arg-type]
    engineer._enqueue("penalty", {"penalties_s": 5}, critical=True, cooldown_s=0.0)
    engineer._enqueue(
        "yellow_ahead", {"flag": "yellow", "zone": 3, "distance_ahead_m": 500.0},
        critical=True, cooldown_s=0.0,
    )
    assert [event["type"] for event in engineer._candidates({})] == ["yellow_ahead", "penalty"]


@pytest.mark.asyncio
async def test_a_yellow_waved_during_narration_is_spoken_before_the_narrated_call(stack, clock):
    store, database, *_ = stack
    zones = [{"index": 3, "start_m": 2200.0, "flag": "yellow"}]
    holder: dict = {}

    async def yellow_appears(event):
        if event["type"] == "penalty":
            await store.update(marshal_zones=zones, lap_distance_m=1700.0)
            await holder["engineer"]._detect(await store.snapshot_radio())

    brain = _Brain(database, during=yellow_appears, reply="Five-second penalty. Keep it clean.")
    store, engineer, voice = await _race(stack, brain=brain)
    holder["engineer"] = engineer
    await store.update(penalties_s=5)
    await _tick(store, engineer, passes=1)
    assert voice.spoken == [
        "Yellow flag, sector ahead, 500 metres. Lift and no overtaking.",
        "Five-second penalty. Keep it clean.",
    ]


@pytest.mark.asyncio
async def test_a_yellow_is_said_with_the_distance_left_and_not_after_the_zone(stack, clock):
    store, engineer, voice = await _race(
        stack, lap_distance_m=1700.0, marshal_zones=[{"index": 3, "start_m": 2200.0, "flag": "yellow"}]
    )
    await engineer._detect(await store.snapshot_radio())
    await store.update(lap_distance_m=1900.0)
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == ["Yellow flag, sector ahead, 300 metres. Lift and no overtaking."]

    await store.update(current_lap=11, lap_distance_m=1700.0)
    await engineer._detect(await store.snapshot_radio())
    await store.update(lap_distance_m=2250.0)  # already in the zone
    await engineer._deliver(await store.snapshot_radio())
    assert len(voice.spoken) == 1, voice.spoken


def test_a_red_zone_is_not_a_yellow_with_a_different_colour():
    text = ProactiveEngineer._fallback_text(
        {"type": "yellow_ahead", "payload": {"flag": "red", "distance_ahead_m": 300.0}}, {}
    )
    assert "flag flag" not in text and "overtaking" not in text
    assert text.startswith("Red flag")


# --- templates ----------------------------------------------------------------


def _every_template() -> dict[str, str]:
    payloads = {
        "progress_update": {"lap": 12, "target": {}},
        "race_control": {"to": "vsc_ending"},
        "yellow_ahead": {"flag": "yellow", "distance_ahead_m": 400},
        "strategy_change": {"instruction": "Box lap 20 for hards."},
        "driver_check": {"laps_to_window": 2},
        "tyre_wear": {"wear_fl_fr_rl_rr": [70, 71, 66, 65]},
        "lap_deleted": {"lap": 4},
        "quali_clear_air": {"target": {"target": "1:16.900"}},
        "blue_flag": {"flag": "blue"},
        "penalty_service": {"drive_through": 1},
        "undercut_threat": {"driver": "Norris"},
        "safety_car_delta": {"safety_car_delta_s": -0.6, "phase": "vsc"},
        "corner_coaching": {"name": "Corner 4"},
        "fuel_warning": {},
        "damage": {"front_left_wing": 40},
        "compound_requirement": {"laps_remaining": 5},
        "weather_crossover": {"worth_stopping": True, "compound": "INTER", "reason": "Track is wet."},
        "penalty": {"penalties_s": 10, "added_s": 5, "total_s": 10},
        "warning": {"corner_cutting_warnings": 3},
        "rival_pitted": {"driver": "Norris"},
        "race_start": {"grid_position": 3, "clean_side": True},
        "sc_restart": {},
        "energy_low": {"ers_pct": 10},
        "component_wear": {"component": "mguh", "wear_pct": 78},
        "engine_failure": {"blown": True},
        "rival_pace": {"driver": "Chaser", "gap_to_player_s": 2.5},
    }
    return {
        kind: ProactiveEngineer._fallback_text({"type": kind, "payload": payload}, {"fuel_laps_delta": -0.6})
        for kind, payload in payloads.items()
    }


def test_no_spoken_template_carries_mojibake():
    """The driver_check question read a mis-decoded dash aloud."""
    for kind, text in _every_template().items():
        for marker in MOJIBAKE:
            assert marker not in text, f"{kind}: {text!r}"
    check = _every_template()["driver_check"]
    assert check.endswith("?") and "rears" in check


def test_templates_say_things_the_way_a_driver_hears_them():
    texts = _every_template()
    assert texts["race_control"] == "Race control: VSC ending."
    # A weather stop leads with the instruction; the pit entry is a fixed
    # point on the lap and the evidence can follow it.
    assert texts["weather_crossover"] == "Box this lap for inters. Track is wet."
    assert texts["penalty"].startswith("Another 5 second penalty; that is 10 seconds in total.")
    assert texts["progress_update"] == "Lap 12: target still building. Keep the lap clean."
    assert "MGU-H" in texts["component_wear"]
    with_instruction = ProactiveEngineer._fallback_text(
        {"type": "progress_update", "payload": {
            "lap": 12, "target": {"target": "1:31.200"},
            "racing_line": {"top_opportunity": "brake five metres later into turn 4."},
        }},
        {},
    )
    assert with_instruction == "Lap 12: target 1:31.200. Brake five metres later into turn 4."


# --- the pit plan as the unsolicited radio says it ----------------------------

_NO_GAIN = {
    "box_lap": 33, "fit_compound": "SOFT", "instruction": "Box lap 33 for SOFT.",
    "stops_remaining": 1, "positions_gained_vs_stay_out": 0, "positions_lost_by_stopping": 6,
}


@pytest.mark.asyncio
async def test_a_flag_call_says_stay_out_when_the_stop_gains_nothing(stack, clock):
    """Asked, the radio said "Stay out"; the next flag call said "Box lap 33"."""
    store, engineer, voice = await _race(stack, current_lap=33, total_laps=37)
    await store.mutate(lambda s: setattr(s, "strategy", {"recommended": dict(_NO_GAIN)}))
    await store.update(race_control_phase="safety_car", safety_car="full")
    await _tick(store, engineer)
    assert voice.spoken[0] == (
        "Race control: Safety car. Stay out — boxing costs you 6 places and gains nothing."
    )


@pytest.mark.asyncio
async def test_no_proactive_call_asks_for_the_tyre_just_fitted(stack, clock):
    store, engineer, voice = await _race(stack, current_lap=33, total_laps=37)
    plan = {"box_lap": 33, "fit_compound": "SOFT", "instruction": "Box lap 33 for SOFT.", "stops_remaining": 1}
    await store.mutate(lambda s: setattr(s, "strategy", {"recommended": plan}))
    await store.mutate(_set_tyre(compound="SOFT", age_laps=1, wear=[3.0, 3.0, 84.0, 4.0]))
    await store.update(race_control_phase="vsc", safety_car="virtual")
    await _tick(store, engineer)
    assert voice.spoken, "nothing was said"
    for text in voice.spoken:
        assert "box" not in text.lower(), voice.spoken
    assert "Race control: VSC. Stay out — you're on fresh softs." in voice.spoken


def test_the_narrated_payload_carries_the_same_guarded_call(stack):
    store, database, *_ = stack
    engineer = ProactiveEngineer(store, _Brain(database), _Voice(), _Setup(), _Strategy())  # type: ignore[arg-type]
    state = {"mode_profile": "race", "current_lap": 33, "race_control_phase": "green",
             "tyre": {"wear": [40, 40, 84, 40]}, "strategy": {"recommended": dict(_NO_GAIN)}}
    event = {"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [40, 40, 84, 40], "strategy": {}}}
    engineer._refresh_payload(event, state)
    assert event["payload"]["strategy"]["instruction"].startswith("Stay out — boxing costs you 6 places")
    # The engine's mandatory-compound wording is not flattened into "Box lap".
    mandatory = {**_NO_GAIN, "instruction": "A second compound is mandatory — box lap 33 for SOFT.",
                 "stop_required_reason": "mandatory second dry compound",
                 "positions_gained_vs_stay_out": 0, "positions_lost_by_stopping": 0}
    state["strategy"] = {"recommended": mandatory}
    engineer._refresh_payload(event, state)
    assert event["payload"]["strategy"]["instruction"] == mandatory["instruction"]


# --- a declined stop stays declined -------------------------------------------

_BOX_18 = {"box_lap": 18, "fit_compound": "HARD", "instruction": "Box lap 18 for HARD.", "stops_remaining": 1}


async def _declined(stack, utterance: str):
    store, database, _strategy, _setup, _analysis, tools = stack
    _, engineer, voice = await _race(stack, current_lap=17, mode_profile="race")

    def plan(state):
        state.tyre.wear = [55.0, 56.0, 70.0, 66.0]
        state.tyre.compound = "MEDIUM"
        state.strategy = {"recommended": dict(_BOX_18), "plans": []}
        state.analysis["last_lap_analyzed"] = 16
        state.proactive["cadence_laps"] = 2

    await store.mutate(plan)
    brain = EngineerBrain(store, tools, database)
    await brain._fast_answer(utterance)
    return store, engineer, voice


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    ["No, I'm not boxing, staying out on these.", "stop telling me about the pit stop"],
)
async def test_a_declined_stop_is_not_called_by_any_other_proactive_call(stack, clock, utterance):
    store, engineer, voice = await _declined(stack, utterance)
    for lap in (17, 18, 19, 20, 21):
        await store.update(current_lap=lap)
        await store.mutate(lambda s, done=lap - 1: s.analysis.update({"last_lap_analyzed": done}))
        await _tick(store, engineer)
        clock.offset += 90
    assert voice.spoken, "the tyre warning and progress updates still belong on the radio"
    assert any(text.startswith("Tyre warning") for text in voice.spoken)
    for text in voice.spoken:
        assert "box" not in text.lower(), voice.spoken


@pytest.mark.asyncio
async def test_a_tyre_past_the_hard_limit_still_carries_the_plan(stack, clock):
    """"Unless it is safety-critical" is part of the rule the driver set."""
    store, engineer, voice = await _declined(stack, "stop telling me about the pit stop")
    await store.mutate(_set_tyre(wear=[55.0, 56.0, 85.0, 66.0]))
    await _tick(store, engineer)
    tyre = [text for text in voice.spoken if text.startswith("Tyre warning")]
    assert tyre and "box" in tyre[-1].lower(), voice.spoken


@pytest.mark.asyncio
async def test_the_tyre_check_is_not_asked_about_a_declined_window(stack, clock):
    store, engineer, _ = await _declined(stack, "No, I'm not boxing, staying out on these.")
    await store.mutate(lambda s: s.strategy["recommended"].update({"box_lap": 19}))
    await engineer._detect(await store.snapshot_radio())
    assert not [event for event in engineer.pending if event["type"] == "driver_check"]


@pytest.mark.asyncio
async def test_the_narrator_is_told_about_the_decline_and_hears_the_driver(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    await store.update(connected=True, session_uid=7, mode_profile="race", current_lap=17)
    await store.update(strategy_hold={"active": True, "until_lap": 22})
    await store.append_radio("driver", "No, I'm not boxing, staying out on these.")
    brain = EngineerBrain(store, tools, database)
    prompts: list[str] = []

    async def capture(self, prompt, *args, **kwargs):
        prompts.append(prompt)
        return "Rears at seventy."

    brain._run = types.MethodType(capture, brain)
    await brain.proactive({"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [55, 56, 70, 66]}})
    assert "DRIVER DECLINED THE PIT CALL" in prompts[0]
    assert "lap 22" in prompts[0]
    recent = prompts[0].split("RECENT DRIVER RADIO: ", 1)[1].splitlines()[0]
    assert json.loads(recent) == ["No, I'm not boxing, staying out on these."]


# --- a call that went stale while it was being worded -------------------------


@pytest.mark.asyncio
async def test_a_box_call_is_not_spoken_after_the_driver_declined_it_mid_narration(stack, clock):
    """'Copy, we stay out' then 'Box this lap for hards, box box' — back to back."""
    store, database, *_ = stack

    async def driver_declines(event):
        await store.update(strategy_hold={"active": True, "until_lap": 22})

    brain = _Brain(database, during=driver_declines, reply="Box this lap for hards, box box.")
    store, engineer, voice = await _race(stack, brain=brain, current_lap=17)
    await store.mutate(lambda s: setattr(s, "strategy", {"recommended": dict(_BOX_18, box_lap=17)}))
    engineer._enqueue("strategy_change", dict(_BOX_18, box_lap=17), critical=True, cooldown_s=0.0)
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == []
    with database._connect() as db:
        outcome = db.execute(
            "SELECT outcome FROM proactive_calls WHERE event_type='strategy_change'"
        ).fetchone()["outcome"]
    assert "while it was being worded" in outcome


@pytest.mark.asyncio
async def test_a_call_worded_while_the_driver_keyed_the_radio_waits_for_them(stack, clock):
    store, database, *_ = stack

    async def driver_keys_radio(event):
        await store.update(ptt_pressed=True)

    brain = _Brain(database, during=driver_keys_radio, reply="Rear left at seventy percent.")
    store, engineer, voice = await _race(stack, brain=brain)
    await store.mutate(_set_tyre(wear=[50.0, 52.0, 71.0, 60.0]))
    await engineer._detect(await store.snapshot_radio())
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == []
    assert [event["type"] for event in engineer.pending] == ["tyre_wear"]

    brain.during = None
    await store.update(ptt_pressed=False)
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == ["Rear left at seventy percent."]


@pytest.mark.asyncio
async def test_a_penalty_whose_life_ran_out_while_it_was_worded_is_still_said(stack, clock):
    """The clock running out during the wording is not the race moving on."""
    store, database, *_ = stack

    async def slow_wording(event):
        clock.offset += 60  # past the 45 s life of a critical call

    brain = _Brain(database, during=slow_wording, reply="Five-second penalty. Keep it clean.")
    store, engineer, voice = await _race(stack, brain=brain)
    await store.update(penalties_s=5)
    await _tick(store, engineer, passes=1)
    assert voice.spoken == ["Five-second penalty. Keep it clean."]


@pytest.mark.asyncio
async def test_a_call_held_for_the_driver_is_not_given_up_on(stack, clock):
    """Keying the radio during the wording three times does not lose the call."""
    store, database, *_ = stack

    async def driver_keys_radio(event):
        await store.update(ptt_pressed=True)

    brain = _Brain(database, during=driver_keys_radio, reply="Front wing at forty-five percent.")
    store, engineer, voice = await _race(stack, brain=brain)
    await store.mutate(lambda s: s.damage.update({"front_left_wing": 45}))
    await engineer._detect(await store.snapshot_radio())
    for _ in range(3):
        await engineer._deliver(await store.snapshot_radio())
        assert voice.spoken == []
        assert [event["type"] for event in engineer.pending] == ["damage"]
        await store.update(ptt_pressed=False)
    brain.during = None
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == ["Front wing at forty-five percent."]


@pytest.mark.asyncio
async def test_routine_news_waits_for_a_conversation_opened_while_it_was_worded(stack, clock):
    store, database, *_ = stack
    holder: dict = {}

    async def driver_opens_conversation(event):
        holder["voice"].realtime_active = True

    brain = _Brain(database, during=driver_opens_conversation, reply="Rear left at seventy percent.")
    store, engineer, voice = await _race(stack, brain=brain)
    holder["voice"] = voice
    await store.mutate(_set_tyre(wear=[50.0, 52.0, 71.0, 60.0]))
    await engineer._detect(await store.snapshot_radio())
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == []
    assert [event["type"] for event in engineer.pending] == ["tyre_wear"]

    brain.during = None
    voice.realtime_active = False
    await engineer._deliver(await store.snapshot_radio())
    assert voice.spoken == ["Rear left at seventy percent."]


# --- a barge-in, a silent conversation, a suspension ---------------------------


def _voice_controller(store, brain, spoken, *, playback_s=0.0):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pitwall.voice import NativeVoiceController

    async def stream_speech(text, target=None, on_first_audio=None):
        spoken.append(text)
        if playback_s:
            await asyncio.sleep(playback_s)
        return True

    audio = SimpleNamespace(
        stop_playback=lambda: None, voice_ready=True, play_ack=AsyncMock(),
        stream_speech=stream_speech, play_listening_cue=AsyncMock(),
    )
    return NativeVoiceController(store, brain, audio)


@pytest.mark.asyncio
async def test_keying_the_radio_over_a_call_does_not_silence_every_later_call(stack, monkeypatch, tmp_path):
    """The cut call's cancellation used to end the delivery loop for good."""
    import asyncio

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "voice_stream_tts", True)
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    monkeypatch.setattr(proactive_module, "DELIVER_INTERVAL_S", 0.01)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played, playback_s=0.3)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller
    deliver = asyncio.create_task(engineer._run_deliver())
    try:
        await store.update(penalties_s=5)
        await engineer._detect(await store.snapshot_radio())
        for _ in range(50):
            if played:
                break
            await asyncio.sleep(0.01)
        assert played and "penalty" in played[0]
        controller._speech_task.cancel()  # what a push-to-talk press does
        await asyncio.sleep(0.05)
        assert not deliver.done()
        await store.update(race_control_phase="safety_car", safety_car="full")
        await engineer._detect(await store.snapshot_radio())
        for _ in range(100):
            if len(played) > 1:
                break
            await asyncio.sleep(0.01)
        assert played[1:] == ["Race control: Safety car."]
    finally:
        deliver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await deliver


def _call_rows(database, kind):
    with database._connect() as db:
        rows = db.execute(
            "SELECT outcome, delivered FROM proactive_calls WHERE event_type=? ORDER BY rowid",
            (kind,),
        ).fetchall()
    return [(row["outcome"], row["delivered"]) for row in rows]


async def _wait_for(predicate, attempts: int = 300) -> None:
    import asyncio

    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)


async def _press_and_release(controller) -> None:
    import asyncio

    await controller._transition(True, real_time.monotonic(), "mask_press")
    await asyncio.sleep(0.05)
    await controller._transition(False, real_time.monotonic(), "explicit_release")


@pytest.mark.asyncio
async def test_a_penalty_the_driver_talked_over_is_said_again_after_their_exchange(stack, monkeypatch, tmp_path):
    """Keying the radio over a penalty call lost it: "voice delivery failed", never again."""
    import asyncio

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "voice_stream_tts", True)
    monkeypatch.setattr(settings, "ptt_release_tail_s", 0.0)
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    monkeypatch.setattr(proactive_module, "DELIVER_INTERVAL_S", 0.01)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played, playback_s=0.3)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller
    deliver = asyncio.create_task(engineer._run_deliver())
    try:
        await store.update(penalties_s=5)
        await engineer._detect(await store.snapshot_radio())
        await _wait_for(lambda: bool(played))
        assert played and "penalty" in played[0]
        await _press_and_release(controller)
        await _wait_for(lambda: len(played) > 1)
        assert played[1] == played[0]
        await _wait_for(lambda: len(_call_rows(database, "penalty")) > 1)
        # The cut attempt and the delivery, each recorded once.
        assert _call_rows(database, "penalty") == [
            ("cut short by the driver; requeued", 0),
            ("delivered", 1),
        ]
        assert not engineer.pending
    finally:
        deliver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await deliver


@pytest.mark.asyncio
async def test_a_cut_call_waits_for_the_answer_to_the_drivers_question(stack, monkeypatch, tmp_path):
    """Between letting go of the button and the clip reaching the engineer,
    the radio read as free: the cut penalty was said again before the answer,
    with the acknowledgement playing over it."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import numpy as np

    from pitwall.voice import NativeVoiceController

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "native_voice", True)
    monkeypatch.setattr(settings, "voice_stream_tts", True)
    monkeypatch.setattr(settings, "ptt_release_tail_s", 0.02)
    monkeypatch.setattr(settings, "ptt_min_hold_ms", 0)
    monkeypatch.setattr(settings, "audio_min_rms", 1.0)
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    monkeypatch.setattr(proactive_module, "DELIVER_INTERVAL_S", 0.01)
    store, database, *_ = stack
    heard: list[str] = []

    async def stream_speech(text, target=None, on_first_audio=None):
        heard.append(text)
        await asyncio.sleep(0.2)
        return True

    async def play_tone(*_args):
        await asyncio.sleep(0.07)

    async def transcribe(_path, _names):
        await asyncio.sleep(0.1)
        return "what's the gap behind"

    async def play_ack(_kind):
        heard.append("<ack>")

    class _Answering(_Brain):
        @staticmethod
        def classify_request(_command):
            return "fast"

        async def ask(self, _command):
            await asyncio.sleep(0.05)
            return "Gap behind is 1.2 seconds."

    audio = SimpleNamespace(
        stop_playback=lambda: None, voice_ready=True, play_ack=play_ack, play_tone=play_tone,
        stream_speech=stream_speech, play_listening_cue=AsyncMock(), transcribe=transcribe,
    )
    controller = NativeVoiceController(store, _Answering(database), audio)

    async def microphone(self):
        self.stream = object()

    controller._ensure_input_stream = types.MethodType(microphone, controller)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller
    deliver = asyncio.create_task(engineer._run_deliver())
    try:
        await store.update(penalties_s=5)
        await engineer._detect(await store.snapshot_radio())
        await _wait_for(lambda: bool(heard))
        penalty = heard[0]
        assert "penalty" in penalty
        controller._signal_pressed = True
        await controller._transition(True, real_time.monotonic(), "mask_press")
        controller.frames = [np.full((1600, 1), 3000, dtype=np.int16)]
        await asyncio.sleep(0.05)
        await controller._transition(False, real_time.monotonic(), "explicit_release")
        await _wait_for(lambda: heard.count(penalty) > 1)
        assert heard == [penalty, "<ack>", "Gap behind is 1.2 seconds.", penalty]
    finally:
        deliver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await deliver


@pytest.mark.asyncio
async def test_a_cut_call_no_longer_true_does_not_come_back(stack, monkeypatch, tmp_path):
    """A flashback took the penalty back while the driver was talking."""
    import asyncio

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "voice_stream_tts", True)
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played, playback_s=0.3)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller
    await store.update(penalties_s=5)
    await engineer._detect(await store.snapshot_radio())
    speaking = asyncio.create_task(engineer._deliver(await store.snapshot_radio()))
    await _wait_for(lambda: bool(played))
    await controller._transition(True, real_time.monotonic(), "mask_press")
    await speaking
    assert [event["type"] for event in engineer.pending] == ["penalty"]
    # Held while the driver has the radio, not spoken over them.
    await engineer._deliver(await store.snapshot_radio())
    assert len(played) == 1
    await store.update(penalties_s=0)
    controller._signal_pressed = False
    await store.update(ptt_pressed=False)
    for _ in range(3):
        await engineer._deliver(await store.snapshot_radio())
    assert len(played) == 1
    assert not engineer.pending
    assert _call_rows(database, "penalty") == [
        ("cut short by the driver; requeued", 0),
        ("superseded", 0),
    ]


@pytest.mark.asyncio
async def test_a_cut_restart_call_is_not_said_after_the_green(stack, monkeypatch, tmp_path):
    """"VSC ending. Stay positive on the delta until green" said once the race
    is green tells the driver to sit on the delta while the field goes."""
    import asyncio

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "voice_stream_tts", True)
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played, playback_s=0.3)
    store, engineer, _voice = await _race(stack, race_control_phase="vsc_ending")
    engineer.voice = controller
    engineer._enqueue("sc_restart", {"phase": "vsc_ending"}, critical=True, cooldown_s=0.0, expires_s=20.0)
    speaking = asyncio.create_task(engineer._deliver(await store.snapshot_radio()))
    await _wait_for(lambda: bool(played))
    assert played[0].startswith("VSC ending")
    await controller._transition(True, real_time.monotonic(), "mask_press")
    await speaking
    assert [event["type"] for event in engineer.pending] == ["sc_restart"]
    # Green comes while the driver is talking.
    await store.update(race_control_phase="green")
    controller._signal_pressed = False
    await store.update(ptt_pressed=False)
    for _ in range(3):
        await engineer._deliver(await store.snapshot_radio())
    assert played == [played[0]]
    assert not engineer.pending
    assert _call_rows(database, "sc_restart") == [
        ("cut short by the driver; requeued", 0),
        ("superseded", 0),
    ]


@pytest.mark.asyncio
async def test_routine_news_cut_short_and_a_second_cut_are_not_repeated(stack, monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "voice_stream_tts", True)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played, playback_s=0.3)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller

    async def cut(event, text):
        played.clear()
        speaking = asyncio.create_task(engineer._speak(event, text, await store.snapshot_radio()))
        await _wait_for(lambda: bool(played))
        await controller._transition(True, real_time.monotonic(), "mask_press")
        await speaking
        controller._signal_pressed = False

    routine = {
        "type": "progress_update", "priority": proactive_module.ROUTINE, "payload": {},
        "session_uid": 7, "queued_at": real_time.time(), "expires_at": real_time.time() + 60,
    }
    await cut(routine, "Lap ten, P5, gap ahead two seconds.")
    assert not engineer.pending
    penalty = {
        "type": "penalty", "critical": True, "priority": proactive_module.CRITICAL,
        "payload": {"penalties_s": 5}, "session_uid": 7,
        "queued_at": real_time.time(), "expires_at": real_time.time() + 60,
    }
    await cut(penalty, "You have picked up a 5 second penalty.")
    assert engineer.pending and engineer.pending[0] is penalty
    engineer.pending.clear()
    await cut(penalty, "You have picked up a 5 second penalty.")
    assert not engineer.pending
    assert _call_rows(database, "progress_update") == [("cut short by the driver", 0)]
    assert _call_rows(database, "penalty") == [
        ("cut short by the driver; requeued", 0),
        ("cut short by the driver", 0),
    ]


@pytest.mark.asyncio
async def test_a_flag_is_not_held_by_the_reply_window_after_the_engineers_question(stack, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    monkeypatch.setattr(settings, "wake_enabled", True)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller
    await controller.open_reply_window("engineer asked a question")
    assert controller.is_busy and not controller.audio_busy
    state = await store.snapshot_radio()
    assert engineer._engine_block_reason(state, urgent=True) is None
    assert engineer._engine_block_reason(state).startswith("engineer busy: wake window armed")
    await store.update(race_control_phase="safety_car", safety_car="full")
    await _tick(store, engineer)
    assert played == ["Race control: Safety car."]
    # The window the driver was answering in is given back afterwards.
    assert controller.reply_window_armed


@pytest.mark.asyncio
async def test_a_flag_is_not_held_while_the_engineer_works_out_an_answer(stack, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    store, database, *_ = stack
    played: list[str] = []
    controller = _voice_controller(store, _Brain(database), played)
    store, engineer, _voice = await _race(stack)
    engineer.voice = controller
    controller.busy = True  # transcribing and answering a driver question, nothing playing
    await store.update(race_control_phase="vsc", safety_car="virtual")
    await store.mutate(_set_tyre(wear=[50.0, 52.0, 71.0, 60.0]))
    await _tick(store, engineer)
    assert played == ["Race control: VSC."]
    assert controller.busy_reason == "answering the driver"
    # Routine news still waits for the conversation.
    assert [event["type"] for event in engineer.pending] == ["tyre_wear"]


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before delivery", "while it was worded"])
async def test_a_penalty_that_meets_a_red_flag_is_still_said(stack, clock, when):
    store, database, *_ = stack
    holder: dict = {}

    async def red_flag(event):
        if when == "while it was worded" and event["type"] == "penalty":
            await store.update(red_flag_active=True, race_control_phase="red_flag")
            await holder["engineer"]._detect(await store.snapshot_radio())

    brain = _Brain(database, during=red_flag, reply="Five-second penalty for causing a collision.")
    store, engineer, voice = await _race(stack, brain=brain)
    holder["engineer"] = engineer
    await store.update(penalties_s=5)
    await engineer._detect(await store.snapshot_radio())
    if when == "before delivery":
        await store.update(red_flag_active=True, race_control_phase="red_flag")
    await _tick(store, engineer)
    assert voice.spoken[0].startswith("Red flag, red flag.")
    assert voice.spoken[1] == "Five-second penalty for causing a collision."


@pytest.mark.asyncio
async def test_an_unserved_drive_through_survives_a_red_flag(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(unserved_drive_through_penalties=1)
    await engineer._detect(await store.snapshot_radio())
    await store.update(red_flag_active=True, race_control_phase="red_flag")
    await _tick(store, engineer)
    assert len(voice.spoken) == 2 and "Drive-through" in voice.spoken[1]


# --- damage, component by component ----------------------------------------------


async def _damage(store, engineer, clock, **values):
    await store.mutate(lambda s: s.damage.update(values))
    await _tick(store, engineer)
    clock.offset += 30


@pytest.mark.asyncio
async def test_a_wing_hit_after_floor_damage_is_called_as_the_wing(stack, clock):
    store, engineer, voice = await _race(stack)
    await _damage(store, engineer, clock, floor=30)
    await _damage(store, engineer, clock, front_left_wing=65)
    assert voice.spoken[0].startswith("Floor damage 30 percent.")
    assert voice.spoken[1].startswith("Front-wing damage 65 percent.")
    assert "Floor" not in voice.spoken[1]


@pytest.mark.asyncio
async def test_floor_damage_beside_a_reported_wing_and_a_new_wing_hit_are_both_called(stack, clock):
    store, engineer, voice = await _race(stack)
    await _damage(store, engineer, clock, front_left_wing=65)
    await _damage(store, engineer, clock, floor=30)
    assert len(voice.spoken) == 2 and voice.spoken[1].startswith("Floor damage 30 percent.")
    # The nose is changed at a stop, then hit again while the floor carries 30%.
    await _damage(store, engineer, clock, front_left_wing=0)
    await _damage(store, engineer, clock, front_left_wing=35)
    assert voice.spoken[-1].startswith("Front-wing damage 35 percent.")


@pytest.mark.asyncio
async def test_a_wing_and_floor_hit_together_leads_with_the_repairable_wing(stack, clock):
    store, engineer, voice = await _race(stack)
    await _damage(store, engineer, clock, floor=25, front_left_wing=45)
    assert voice.spoken == [
        "Front-wing damage 45 percent. That is the one thing we can change at a stop; say the word "
        "if the balance is costing you. Floor damage 25 percent. Expect reduced downforce; it cannot "
        "be repaired at a stop."
    ]


# --- what a standing instruction may silence --------------------------------------


def test_a_standing_instruction_does_not_silence_a_safety_critical_call():
    tyres = [{"rule": "Do not raise tyres unless the driver asks or it is safety-critical."}]
    assert ProactiveEngineer.is_suppressed("tyre_wear", tyres) is True
    assert ProactiveEngineer.is_suppressed("tyre_wear", tyres, {"wear_fl_fr_rl_rr": [60, 61, 70, 66]}) is True
    assert ProactiveEngineer.is_suppressed("tyre_wear", tyres, {"wear_fl_fr_rl_rr": [70, 71, 88, 80]}) is False

    gearbox = [{"rule": "Do not raise gearbox unless the driver asks or it is safety-critical."}]
    assert ProactiveEngineer.is_suppressed("damage", gearbox) is True
    assert ProactiveEngineer.is_suppressed("damage", gearbox, {"gearbox": 30, "changed": ["gearbox"]}) is True
    assert ProactiveEngineer.is_suppressed(
        "damage", gearbox, {"gearbox": 30, "front_left_wing": 30, "changed": ["front_left_wing"]}
    ) is False
    assert ProactiveEngineer.is_suppressed(
        "damage", gearbox, {"gearbox": 30, "ers_fault": True, "new_faults": ["ers_fault"], "changed": []}
    ) is False


@pytest.mark.asyncio
async def test_a_silenced_gearbox_does_not_silence_a_wing_hit_and_an_ers_fault(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(standing_instructions=[
        {"rule": "Do not raise gearbox unless the driver asks or it is safety-critical.", "added_at": 1.0}
    ])
    await _damage(store, engineer, clock, gearbox=30)
    assert voice.spoken == []
    await _damage(store, engineer, clock, front_left_wing=70, ers_fault=True)
    assert voice.spoken and voice.spoken[0].startswith("ERS fault reported.")
    assert "Front-wing damage 70 percent" in voice.spoken[0]


@pytest.mark.asyncio
async def test_silencing_the_gearbox_damage_leaves_a_wing_hit_audible(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(standing_instructions=[
        {"rule": "Do not raise gearbox damage unless the driver asks or it is safety-critical.", "added_at": 1.0}
    ])
    await _damage(store, engineer, clock, gearbox=30)
    assert voice.spoken == []
    await _damage(store, engineer, clock, front_left_wing=30)
    assert len(voice.spoken) == 1 and voice.spoken[0].startswith("Front-wing damage 30 percent")


@pytest.mark.asyncio
async def test_a_silenced_tyre_subject_still_warns_past_the_hard_limit(stack, clock):
    store, engineer, voice = await _race(stack)
    await store.update(standing_instructions=[
        {"rule": "Do not raise tyres unless the driver asks or it is safety-critical.", "added_at": 1.0}
    ])
    await store.mutate(_set_tyre(wear=[60.0, 61.0, 70.0, 66.0]))
    await _tick(store, engineer)
    assert voice.spoken == []
    clock.offset += 120
    await store.mutate(_set_tyre(wear=[70.0, 71.0, 88.0, 80.0]))
    await _tick(store, engineer)
    assert len(voice.spoken) == 1 and "88 percent" in voice.spoken[0]


# --- a declined stop, a question asked, a restart, a released hold ----------------


@pytest.mark.asyncio
async def test_a_narration_that_restates_a_declined_stop_is_replaced(stack, clock):
    store, database, *_ = stack
    brain = _Brain(database, reply="Rear left at 70 percent. Box lap 18 for hards, that's next lap.")
    store, engineer, voice = await _race(stack, brain=brain, current_lap=17)
    await store.update(
        strategy={"recommended": {"box_lap": 18, "fit_compound": "HARD", "instruction": "Box lap 18 for HARD."}},
        strategy_hold={"active": True, "until_lap": 22, "set_at_lap": 17, "baseline": {
            "race_control_phase": "green", "wet": False, "compound": "MEDIUM", "damage": {}, "max_wear_pct": 70.0,
        }},
    )
    await store.mutate(_set_tyre(wear=[55.0, 56.0, 70.0, 66.0], compound="MEDIUM"))
    await _tick(store, engineer)
    assert (await store.snapshot_radio())["strategy_hold"]["active"]
    assert len(voice.spoken) == 1
    assert "Box lap 18" not in voice.spoken[0]
    assert voice.spoken[0].startswith("Tyre warning.")


@pytest.mark.asyncio
async def test_a_narrated_crossover_call_keeps_the_question_it_exists_to_ask(stack, clock):
    store, database, *_ = stack
    brain = _Brain(database, reply="Track is right at the crossover. Stay out on slicks for now.")
    store, engineer, _voice = await _race(stack, brain=brain)
    event = {
        "type": "weather_crossover",
        "payload": {
            "reason": "Track is right at the slick-inter crossover.", "ask_driver": True,
            "driver_question": "How wet is it out there — is the line still taking a slick?",
        },
    }
    spoken = await engineer._narrate(event, await store.snapshot_radio())
    assert spoken.endswith("is the line still taking a slick?")
    assert event["narration_rejected"] == "narration lost its question"
    from pitwall.brain import PROACTIVE_PERSONA

    assert "facts carry driver_question" in " ".join(PROACTIVE_PERSONA.split())


def test_a_vsc_ending_is_not_told_to_watch_the_leader():
    vsc = ProactiveEngineer._fallback_text({"type": "sc_restart", "payload": {"phase": "vsc_ending"}}, {})
    assert "leader" not in vsc and vsc.startswith("VSC ending. Stay positive on the delta until green")
    sc = ProactiveEngineer._fallback_text({"type": "sc_restart", "payload": {"phase": "safety_car_ending"}}, {})
    assert "watch the leader for the go point" in sc


@pytest.mark.asyncio
async def test_a_released_hold_says_the_box_call_once_with_its_reason(stack, clock):
    store, engineer, voice = await _race(stack, current_lap=21)
    plan = {"box_lap": 22, "fit_compound": "HARD", "instruction": "Box lap 22 for HARD.", "stops_remaining": 1}
    await store.update(
        strategy={"recommended": plan, "plans": [], "confidence": "high"},
        strategy_hold={"active": True, "until_lap": 21, "set_at_lap": 16, "reason": "No, staying out.",
                       "baseline": {}, "raised_after_release": False},
    )
    await store.mutate(_set_tyre(wear=[55.0, 56.0, 70.0, 66.0]))
    for _ in range(3):
        await _tick(store, engineer)
        clock.offset += 10
    box_calls = [text for text in voice.spoken if "Box lap 22" in text]
    assert len(box_calls) == 1, voice.spoken
    assert "hold is released" in box_calls[0]
