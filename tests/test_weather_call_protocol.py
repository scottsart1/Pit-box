"""The weather call reaches the driver once per change, promptly, uncontradicted.

The old trigger was a one-shot latch on "the track is wet": a verdict that
moved from "wet, staying out" to "box now" raised no weather call at all, the
strategy call that remained could be turned round by the stay-out guard, a
withdrawn stop was never stood down, and "box now" waited on a model round
trip behind a rules footnote.
"""

from __future__ import annotations

import types

import pytest

from pitwall import proactive as radio
from pitwall.brain import EngineerBrain
from pitwall.proactive import (
    WEATHER_ASK,
    WEATHER_BOX,
    WEATHER_NONE,
    WEATHER_STANDBY,
    WEATHER_WATCH,
    ProactiveEngineer,
)
from pitwall.session_guard import session_key


class _Brain:
    def __init__(self, database):
        self.database = database
        self.narrated: list[dict] = []

    async def proactive(self, event):
        self.narrated.append(event)
        return "narrated"

    async def record_spoken_call(self, text):
        return None


class _Voice:
    is_busy = False

    def __init__(self):
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


WATCHING = {
    "wetness": 0.41, "worth_stopping": False, "ask_driver": False, "compound": "MEDIUM",
    "box_lap": None, "trend": "wetting", "reason": "Track is wet and getting wetter; staying out for now.",
}


def _box(lap: int, compound: str = "INTER", **extra) -> dict:
    return {
        "wetness": 0.47, "worth_stopping": True, "ask_driver": False, "compound": compound,
        "box_lap": lap, "time_offset_min": 0, "payback_s": 80.0, "trend": "wetting",
        "reason": f"Track is wet and getting wetter; {compound} is worth 80s over the rest of the race.",
        **extra,
    }


async def _engineer(stack):
    store, database, *_ = stack
    brain, voice = _Brain(database), _Voice()
    engineer = ProactiveEngineer(store, brain, voice, _Setup(), _Strategy())  # type: ignore[arg-type]

    async def published_plan_stands(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(published_plan_stands, engineer)
    queued: list[tuple[str, dict]] = []
    enqueue = engineer._enqueue

    def recording(event_type, payload, **options):
        accepted = enqueue(event_type, payload, **options)
        if accepted and event_type.startswith("weather"):
            queued.append((event_type, dict(payload)))
        return accepted

    engineer._enqueue = recording
    engineer.queued = queued
    engineer.brain_stub = brain
    engineer.voice_stub = voice
    return store, engineer


async def _detect(store, engineer, crossover, *, lap=13, compound="MEDIUM", weather="Light rain", **extra):
    await store.update(
        connected=True, session_uid=42, game_paused=False, mode_profile="race",
        current_lap=lap, total_laps=40, speed_kph=200, weather=weather,
        strategy={"weather_crossover": crossover, "recommended": {}, "stability": {"held": False},
                  "neutralisation": {"pit_entry_fraction": 0.94}},
        **extra,
    )
    await store.mutate(lambda state: setattr(state.tyre, "compound", compound))
    state = await store.snapshot_radio()
    engineer._session_uid = 42
    engineer._session_context = session_key(state)
    await engineer._detect(state)
    return state


def _levels(engineer, kind="weather_crossover"):
    return [payload["level"] for event_type, payload in engineer.queued if event_type == kind]


# --- the levels ------------------------------------------------------------------


@pytest.mark.parametrize(
    "crossover,compound,expected",
    [
        ({}, "MEDIUM", WEATHER_NONE),
        (WATCHING, "MEDIUM", WEATHER_WATCH),
        ({**WATCHING, "ask_driver": True}, "MEDIUM", WEATHER_ASK),
        (_box(15), "MEDIUM", WEATHER_STANDBY),
        (_box(13), "MEDIUM", WEATHER_BOX),
        (_box(13), "INTER", WEATHER_NONE),
    ],
)
def test_the_call_level_reads_the_published_crossover(crossover, compound, expected):
    state = {
        "strategy": {"weather_crossover": crossover}, "tyre": {"compound": compound},
        "current_lap": 13, "weather": "Light rain",
    }
    assert ProactiveEngineer._weather_call(state)["level"] == expected


def test_rain_on_the_way_is_a_heads_up_before_the_track_is_wet():
    crossover = {
        "wetness": 0.0, "worth_stopping": False, "compound": "MEDIUM",
        "scenarios": [
            {"probability": 0.7, "trajectory": [0.1, 0.3, 0.4, 0.5]},
            {"probability": 0.3, "trajectory": [0.0, 0.0, 0.0, 0.0]},
        ],
    }
    state = {"strategy": {"weather_crossover": crossover}, "tyre": {"compound": "MEDIUM"},
             "current_lap": 5, "weather": "Overcast"}
    call = ProactiveEngineer._weather_call(state)
    assert call["level"] == WEATHER_WATCH and call["direction"] == "to_wet"


def test_on_inters_the_news_is_the_dry_line_coming():
    soaked = {"wetness": 0.6, "compound": "INTER", "trajectory": [0.6, 0.6, 0.6, 0.6, 0.6]}
    drying = {"wetness": 0.45, "compound": "INTER", "trajectory": [0.44, 0.40, 0.36, 0.33]}
    for crossover, expected in ((soaked, WEATHER_NONE), (drying, WEATHER_WATCH)):
        state = {"strategy": {"weather_crossover": crossover}, "tyre": {"compound": "INTER"},
                 "current_lap": 30, "weather": "Light rain"}
        call = ProactiveEngineer._weather_call(state)
        assert call["level"] == expected
    assert call["direction"] == "to_dry"


# --- WX-03: once per change of what it says ----------------------------------------


@pytest.mark.asyncio
async def test_a_watch_that_becomes_a_stop_is_called_again(stack):
    store, engineer = await _engineer(stack)
    await _detect(store, engineer, WATCHING)
    await _detect(store, engineer, _box(13))
    assert _levels(engineer) == [WEATHER_WATCH, WEATHER_BOX]


@pytest.mark.asyncio
async def test_a_stand_by_then_the_box_lap_are_both_said(stack):
    store, engineer = await _engineer(stack)
    await _detect(store, engineer, _box(15), lap=13)
    await _detect(store, engineer, _box(15), lap=14)
    await _detect(store, engineer, _box(15), lap=15)
    assert _levels(engineer) == [WEATHER_STANDBY, WEATHER_BOX]


@pytest.mark.asyncio
async def test_the_crossover_back_to_slicks_is_called(stack):
    store, engineer = await _engineer(stack)
    drying = {"wetness": 0.40, "compound": "INTER", "trajectory": [0.38, 0.34, 0.30]}
    for _ in range(3):
        await _detect(store, engineer, drying, lap=30, compound="INTER")
    await _detect(store, engineer, _box(31, "MEDIUM"), lap=31, compound="INTER")
    calls = [payload for event_type, payload in engineer.queued if event_type == "weather_crossover"]
    assert [call["level"] for call in calls] == [WEATHER_WATCH, WEATHER_BOX]
    assert calls[-1]["direction"] == "to_dry"


@pytest.mark.asyncio
async def test_after_the_stop_nothing_is_said_until_the_track_dries(stack):
    store, engineer = await _engineer(stack)
    await _detect(store, engineer, _box(13), lap=13)
    soaked = {"wetness": 0.6, "compound": "INTER", "trajectory": [0.6] * 6}
    for lap in (14, 15, 16):
        await _detect(store, engineer, soaked, lap=lap, compound="INTER")
    assert _levels(engineer) == [WEATHER_BOX]
    assert not _levels(engineer, "weather_stand_down")


@pytest.mark.asyncio
async def test_a_withdrawn_stop_is_stood_down_once(stack):
    store, engineer = await _engineer(stack)
    await _detect(store, engineer, _box(13))
    await _detect(store, engineer, WATCHING)
    await _detect(store, engineer, WATCHING)
    stand_downs = [payload for event_type, payload in engineer.queued if event_type == "weather_stand_down"]
    assert len(stand_downs) == 1
    assert stand_downs[0]["current_compound"] == "MEDIUM"


@pytest.mark.asyncio
async def test_an_unchanged_stop_is_not_repeated(stack):
    store, engineer = await _engineer(stack)
    for _ in range(10):
        await _detect(store, engineer, _box(13))
    assert _levels(engineer) == [WEATHER_BOX]


@pytest.mark.asyncio
async def test_a_stop_refused_by_the_queue_is_tried_again(stack):
    store, engineer = await _engineer(stack)
    recording = engineer._enqueue
    refused = []

    def refuse_once(event_type, payload, **options):
        if event_type == "weather_crossover" and not refused:
            refused.append(event_type)
            return False
        return recording(event_type, payload, **options)

    engineer._enqueue = refuse_once
    await _detect(store, engineer, _box(13))
    await _detect(store, engineer, _box(13))
    assert refused and _levels(engineer) == [WEATHER_BOX]


@pytest.mark.asyncio
async def test_silenced_weather_still_boxes(stack):
    store, engineer = await _engineer(stack)
    rule = [{"rule": "Do not raise the weather unless it is safety-critical or the driver asks."}]
    await store.update(standing_instructions=rule)
    engineer._standing_instructions = rule
    await _detect(store, engineer, WATCHING, standing_instructions=rule)
    assert _levels(engineer) == []
    await _detect(store, engineer, _box(13), standing_instructions=rule)
    assert _levels(engineer) == [WEATHER_BOX]


@pytest.mark.asyncio
async def test_a_queued_box_is_dropped_when_the_engine_withdraws_it(stack):
    store, engineer = await _engineer(stack)
    await _detect(store, engineer, _box(13))
    assert any(event["type"] == "weather_crossover" for event in engineer.pending)
    state = await _detect(store, engineer, WATCHING)
    engineer._prune(state)
    assert not any(event["type"] == "weather_crossover" for event in engineer.pending)


# --- a box call heard and not acted on ----------------------------------------------


async def _heard(store, engineer, crossover, lap):
    state = await _detect(store, engineer, crossover, lap=lap)
    for event in [event for event in engineer.pending if event["type"] == "weather_crossover"]:
        engineer.pending.remove(event)
        await engineer._speak(event, ProactiveEngineer._fallback_text(event, state), state)


@pytest.mark.asyncio
async def test_a_box_call_heard_and_not_acted_on_is_not_repeated_every_lap(stack):
    store, engineer = await _engineer(stack)
    for lap in range(13, 19):
        # Past the pit entry the stop moves to the next lap, then back.
        await _heard(store, engineer, _box(lap + 1), lap)
        await _heard(store, engineer, _box(lap), lap)
    assert _levels(engineer).count(WEATHER_BOX) == 1


@pytest.mark.asyncio
async def test_a_box_call_returns_when_the_track_gets_worse(stack):
    store, engineer = await _engineer(stack)
    await _heard(store, engineer, _box(13), 13)
    await _heard(store, engineer, _box(14), 13)
    await _heard(store, engineer, _box(14, wetness=0.60), 14)
    assert _levels(engineer).count(WEATHER_BOX) == 2


@pytest.mark.asyncio
async def test_a_box_call_returns_when_the_case_grows_and_never_more_than_three_times(stack):
    store, engineer = await _engineer(stack)
    margin = 80.0
    for lap in range(13, 30, 2):
        await _heard(store, engineer, _box(lap + 1, payback_s=margin), lap)
        await _heard(store, engineer, _box(lap, payback_s=margin), lap)
        margin *= 2
    assert _levels(engineer).count(WEATHER_BOX) == 3


# --- WX-09: box now is said at once, instruction first --------------------------------


def test_a_weather_box_is_a_flash_call_and_a_stand_by_is_not():
    assert ProactiveEngineer._weather_flash("weather_crossover", {"level": WEATHER_BOX})
    assert not ProactiveEngineer._weather_flash("weather_crossover", {"level": WEATHER_STANDBY})
    assert ProactiveEngineer._weather_flash("weather_stand_down", {})
    assert ProactiveEngineer._weather_flash("weather_box_reminder", {})


@pytest.mark.asyncio
async def test_a_weather_box_is_spoken_from_its_template(stack):
    store, engineer = await _engineer(stack)
    state = await _detect(store, engineer, _box(13))
    event = next(event for event in engineer.pending if event["type"] == "weather_crossover")
    assert event["flash"] is True
    spoken = await engineer._narrate(event, state)
    assert spoken.startswith("Box this lap for inters.")
    assert engineer.brain_stub.narrated == []


def test_the_box_call_leads_with_the_instruction():
    text = ProactiveEngineer._fallback_text(
        {"type": "weather_crossover", "payload": {**_box(13), "level": WEATHER_BOX}},
        {"current_lap": 13},
    )
    assert text == "Box this lap for inters. Track is wet and getting wetter; INTER is worth 80s over the rest of the race."


def test_a_named_later_lap_is_a_decided_call():
    text = ProactiveEngineer._fallback_text(
        {"type": "weather_crossover", "payload": {**_box(15), "level": WEATHER_STANDBY}},
        {"current_lap": 13},
    )
    assert text.startswith("Box lap 15 for inters.")


def test_a_stop_awaiting_confirmation_asks_for_the_drivers_read():
    payload = {**_box(14), "level": WEATHER_STANDBY, "awaiting_confirmation": True,
               "reason": "Track is damp and getting wetter; INTER is worth 82s on the forecast, standing by for the lap times to confirm it."}
    text = ProactiveEngineer._fallback_text({"type": "weather_crossover", "payload": payload}, {"current_lap": 13})
    assert text.endswith("How is the grip out there?")


def test_the_stand_down_says_to_stay_out_on_the_fitted_tyre():
    text = ProactiveEngineer._fallback_text(
        {"type": "weather_stand_down", "payload": {"current_compound": "MEDIUM", "reason": "Track is damp and holding. More."}},
        {},
    )
    assert text == "Cancel the stop. Stay out on the mediums. Track is damp and holding."


@pytest.mark.asyncio
async def test_the_reminder_is_said_once_a_lap_before_pit_entry(stack):
    store, engineer = await _engineer(stack)
    await _heard(store, engineer, _box(13), 13)
    for distance in (4000.0, 4100.0):
        await _detect(store, engineer, _box(13), lap=13, track_length_m=5000, lap_distance_m=distance)
    assert len([kind for kind, _ in engineer.queued if kind == "weather_box_reminder"]) == 1
    text = ProactiveEngineer._fallback_text(
        {"type": "weather_box_reminder", "payload": {"compound": "INTER"}}, {}
    )
    assert text == "Box, box. Inters on."


@pytest.mark.asyncio
async def test_no_reminder_once_the_car_is_in_the_pit_lane(stack):
    store, engineer = await _engineer(stack)
    await _heard(store, engineer, _box(13), 13)
    await _detect(store, engineer, _box(13), lap=13, track_length_m=5000, lap_distance_m=4100, pit_status=1)
    assert not [kind for kind, _ in engineer.queued if kind == "weather_box_reminder"]


@pytest.mark.asyncio
async def test_the_strategy_call_does_not_repeat_the_weather_box(stack):
    store, engineer = await _engineer(stack)
    plan = {"box_lap": 13, "fit_compound": "INTER", "stops_remaining": 1,
            "instruction": "Box lap 13 for INTER."}
    engineer._last_strategy_signature = "25:HARD:1"
    await store.update(strategy_hold={"active": False})
    state = await _detect(store, engineer, _box(13))
    await store.update(strategy={**state["strategy"], "recommended": plan})
    state = await store.snapshot_radio()
    await engineer._detect(state)
    assert not [event for event in engineer.pending if event["type"] == "strategy_change"]


# --- WX-04: the radio never talks a driver out of a weather stop --------------------------


def _weather_plan(**extra) -> dict:
    return {
        "box_lap": 30, "fit_compound": "INTER", "stops_remaining": 1,
        "positions_gained_vs_stay_out": -2, "positions_lost_by_stopping": 7,
        "stop_required_reason": "best projected finishing position and expected-points outcome",
        **extra,
    }


def test_the_guard_never_talks_a_driver_out_of_a_weather_stop():
    plan = _weather_plan(weather_crossover={"worth_stopping": True})
    assert EngineerBrain._strategy_guard_override(plan, 30, {"compound": "HARD"}) is None
    named = _weather_plan(stop_required_reason="track conditions")
    assert EngineerBrain._strategy_guard_override(named, 30, {"compound": "HARD"}) is None


def test_a_pace_stop_that_gains_nothing_is_still_refused():
    text = EngineerBrain._strategy_guard_override(_weather_plan(), 30, {"compound": "HARD"})
    assert text == "Stay out — boxing costs you 7 places and gains nothing."


@pytest.mark.asyncio
async def test_a_weather_stop_names_the_conditions_as_its_reason(stack):
    from tools.strategy_probe import build_field, wet_setup

    store, _, strategy, *_ = stack

    def setup(state):
        wet_setup(state, total_laps=40, current_lap=30, position=1)
        state.tyre.compound, state.tyre.age_laps, state.tyre.wear = "HARD", 9, [14.0] * 4
        state.completed_laps = [
            {"lap_num": n, "lap_time_ms": 90_000 if n < 26 else 104_000, "valid": True,
             "pit_status": 0, "weather": "Clear" if n < 26 else "Heavy rain",
             "compound": "SOFT" if n < 21 else "HARD", "track_temp_c": 24}
            for n in range(1, 30)
        ]
        state.drivers = build_field(
            current_lap=30, player_position=1, compound="HARD", tyre_age=9, pit_stops=1,
            spacing_s=3.0, lap_ms=lambda n: 90_000 if n < 26 else 104_000,
        )

    await store.mutate(setup)
    plan = await strategy.recompute()
    recommended = plan["recommended"]
    assert recommended["fit_compound"] == "INTER"
    assert recommended["stop_required_reason"] == "track conditions"
    spoken = ProactiveEngineer._spoken_advice(recommended, await store.snapshot_radio())
    assert spoken["instruction"] == "Box this lap for inters."


@pytest.mark.asyncio
async def test_a_stop_on_a_wet_track_carries_no_rules_footnote(stack):
    from tools.strategy_probe import build_field, wet_setup

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
    plan = await strategy.recompute()
    assert plan["recommended"]["instruction"] == "Box lap 15 for INTER."
    # The fact is still published for the Strategy page.
    assert plan["compound_rule"]["conditional_on_future_wet_use"] is True


def test_the_flash_set_is_not_widened_for_every_weather_call():
    assert "weather_crossover" not in radio.FLASH_EVENTS
