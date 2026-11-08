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
    assert texts["weather_crossover"] == "Track is wet. Box this lap for inters."
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
