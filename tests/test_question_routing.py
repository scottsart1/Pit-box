"""A keyword appearing in a sentence is not the question being asked.

From a recorded session, four different questions in a row were all answered
"Timing currently reports P14." — because the fast path matched the bare word
"position" anywhere in the sentence and returned a position readout. The same
shape of bug existed for "fuel", "damage", "weather" and "penalty".

The guarantee here is behavioural rather than per-branch: a question that asks
what to *do*, or that carries a goal or a qualifier, must never be answered with
a canned single fact.
"""

from __future__ import annotations

import pytest

from pitwall.brain import EngineerBrain


async def _make_brain(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    brain = EngineerBrain(store, tools, database)

    def apply(state):
        state.connected = True
        state.current_lap = 18
        state.total_laps = 44
        state.player_position = 13
        state.player_car_index = 0
        state.mode_profile = "race"
        state.fuel_laps_delta = 0.8
        state.weather = "Light cloud"
        state.rain_next_15_pct = 20
        state.corner_cutting_warnings = 2
        state.damage = {"front_left_wing": 6, "floor": 5, "gearbox": 12}
        state.tyre.compound = "HARD"
        state.tyre.age_laps = 14
        state.tyre.wear = [44.0, 42.0, 51.0, 49.0]
        rows = [("PLAYER", 255, 13, 0.0), ("OCON", 17, 12, -2.4), ("ZHOU", 80, 10, -9.8)]
        for index, (name, driver_id, position, gap) in enumerate(rows):
            driver = state.drivers[index]
            driver.name = name
            driver.driver_id = driver_id
            driver.active = True
            driver.position = position
            driver.gap_to_player_s = gap
            driver.tyre_compound = "MEDIUM" if index else "HARD"
            driver.tyre_age = 8 if index else 14
            driver.pit_stops = 1
            driver.lap_history = [
                {"lap_num": lap, "lap_ms": 97_200, "s1_ms": 31_000,
                 "s2_ms": 33_000, "s3_ms": 33_200, "valid_flags": 1}
                for lap in range(13, 19)
            ]

    await store.mutate(apply)
    return store, brain


# Canned single-fact replies the fast path can produce. None of these is an
# acceptable answer to a question asking what to do.
CANNED = (
    "timing currently reports",
    "fuel is plus",
    "fuel is minus",
    "front-wing damage",
    "track-limit warnings",
    "rain risk",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        # Verbatim from the recorded session.
        "what should be my target lap times if I want to complete a point-finishing position",
        "what should be my target lap time so that I can achieve a points-paying position",
        "is there anything that I could do in order to improve my position and speed",
        "is there anything that I could do to improve my speed and position",
        # The same shape against the other bare-keyword branches.
        "what should my fuel strategy be for the rest of the race",
        "how do I manage the fuel to the end",
        "should I worry about the damage",
        "what can I do about the floor damage",
        "how should I play the weather from here",
        "is there anything I can do to avoid another penalty",
        "what do I need to do to hold this position",
        "any advice on improving my lap time",
        "what would you do about the rain",
        "how can I make up a position",
    ],
)
async def test_advisory_questions_are_never_answered_with_a_canned_fact(stack, utterance):
    store, brain = await _make_brain(stack)
    state = await store.snapshot_analysis()

    assert brain._defers_to_model(state, utterance) is True, utterance
    answer = await brain._fast_answer(utterance)
    if answer is None:
        return
    lowered = answer.lower()
    for canned in CANNED:
        assert canned not in lowered, f"{utterance!r} -> {answer!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("what position am I in", "timing currently reports"),
        ("my position", "timing currently reports"),
        ("how much fuel is left", "fuel is"),
        ("what is the damage", "damage"),
        ("what is the weather", "rain risk"),
        ("how many warnings do I have", "track-limit warnings"),
    ],
)
async def test_direct_lookups_still_answer_immediately(stack, utterance, expected):
    """Tightening the branches must not push cheap facts onto the model."""
    _store, brain = await _make_brain(stack)
    answer = await brain._fast_answer(utterance)
    assert answer is not None, utterance
    assert expected in answer.lower(), f"{utterance!r} -> {answer!r}"


@pytest.mark.asyncio
async def test_position_target_answers_what_it_would_actually_take(stack):
    """"what lap time do I need for points" had no tool behind it at all."""
    _store, _brain = await _make_brain(stack)
    tools = stack[5]
    plan = await tools.get_position_target(10)

    assert plan["available"] is True
    assert plan["already_there"] is False
    assert plan["position"] == 13 and plan["target_position"] == 10
    assert plan["positions_needed"] == 3
    assert [c["driver"] for c in plan["cars_in_the_way"]][0] == "Guanyu Zhou"
    # The arithmetic the driver cannot do at speed.
    assert plan["time_to_find_s"] == 9.8
    assert plan["laps_remaining"] == 26
    assert plan["required_gain_s_per_lap"] == pytest.approx(9.8 / 26, abs=0.01)
    assert plan["required_lap_time"] and plan["your_median_lap"]
    assert plan["feasible_on_pace"] in {"yes", "marginal", "not on pace alone"}
    # And the honesty about what would change it.
    assert "safety car" in plan["note"]


@pytest.mark.asyncio
async def test_position_target_knows_when_the_job_is_holding_station(stack):
    store, _brain = await _make_brain(stack)
    tools = stack[5]
    await store.update(player_position=6)
    plan = await tools.get_position_target(10)
    assert plan["already_there"] is True
    assert "holding" in plan["message"]


@pytest.mark.asyncio
async def test_position_target_reports_an_unrealistic_target_honestly(stack):
    store, _brain = await _make_brain(stack)
    tools = stack[5]
    # One lap left, ten seconds to find.
    await store.update(current_lap=43, total_laps=44)
    plan = await tools.get_position_target(10)
    assert plan["feasible_on_pace"] == "not on pace alone"
