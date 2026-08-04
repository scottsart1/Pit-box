"""The engineer must analyse the data, not read it out.

A driver at speed cannot subtract two lap times, difference a gap history or
work out when a rival arrives. Answering "how am I doing against Perez" with
"PEREZ: 1:37.695." is a data read, and it puts the work back on the person
least able to do it. These cover the analysis tools that return a conclusion
plus the evidence for it.
"""

from __future__ import annotations

import pytest

from pitwall.brain import PERSONA, EngineerBrain


async def _race(stack, *, player_pace=96_500, rival_pace=95_900, rival_closing=True):
    """Mid-race: player P4, Perez P3 ahead, Hulkenberg P5 behind and catching."""
    store = stack[0]

    def apply(state):
        state.connected = True
        state.current_lap = 24
        state.total_laps = 44
        state.player_position = 4
        state.player_car_index = 0
        state.mode_profile = "race"
        state.tyre.compound = "HARD"
        state.tyre.age_laps = 18
        state.tyre.wear = [52.0, 50.0, 58.0, 56.0]
        rows = [
            ("PLAYER", 255, 4, 0.0, "HARD", 18, player_pace),
            ("PEREZ", 14, 3, -3.2, "MEDIUM", 6, rival_pace),
            ("HULKENBERG", 10, 5, 2.1, "HARD", 20, 96_900),
        ]
        for index, (name, driver_id, position, gap, compound, age, pace) in enumerate(rows):
            driver = state.drivers[index]
            driver.name = name
            driver.driver_id = driver_id
            driver.active = True
            driver.position = position
            driver.gap_to_player_s = gap
            driver.tyre_compound = compound
            driver.tyre_age = age
            driver.pit_stops = 1
            driver.lap_history = [
                {
                    "lap_num": lap,
                    "lap_ms": pace,
                    "s1_ms": 31_000,
                    # The player loses time in S2 and S3, not S1.
                    "s2_ms": 33_000 + (200 if index == 0 else 0),
                    "s3_ms": pace - 64_000 + (400 if index == 0 else 0),
                    "valid_flags": 1,
                }
                for lap in range(19, 25)
            ]
        # Perez ahead: gap magnitude shrinking when closing, growing otherwise.
        step = -0.36 if rival_closing else 0.30
        state.drivers[1].gap_history = [
            {"session_time_s": 1000 + k * 90, "lap": 19 + k, "gap_s": -(5.0 + step * k)}
            for k in range(6)
        ]
        # Hulkenberg behind and catching.
        state.drivers[2].gap_history = [
            {"session_time_s": 1000 + k * 90, "lap": 19 + k, "gap_s": 5.0 - 0.58 * k}
            for k in range(6)
        ]

    await store.mutate(apply)
    return stack[5]


@pytest.mark.asyncio
async def test_pace_verdict_answers_with_a_conclusion_not_two_lap_times(stack):
    tools = await _race(stack, player_pace=96_500, rival_pace=96_100)
    verdict = await tools.get_pace_verdict("Perez")

    assert verdict["available"] is True
    assert verdict["driver"] == "Sergio Perez"
    # The conclusion, not the raw material.
    assert verdict["verdict"] == "closing"
    assert verdict["laps_to_contact"] is not None
    assert verdict["gap_change_s_per_lap"] < 0
    # The evidence that supports it.
    assert verdict["pace_interpretation"].startswith("you are ")
    assert verdict["your_median_lap"] and verdict["his_median_lap"]
    # Where the time is actually going.
    assert verdict["losing_most_in"] == "S3"
    # And why, in terms the driver can act on.
    assert "different compound" in verdict["tyre_context"]


@pytest.mark.asyncio
async def test_pace_verdict_reports_dropping_back_when_the_gap_grows(stack):
    tools = await _race(stack, rival_closing=False)
    verdict = await tools.get_pace_verdict("Perez")
    assert verdict["verdict"] == "dropping back"
    assert verdict["laps_to_contact"] is None, "no contact when the gap is growing"


@pytest.mark.asyncio
async def test_pace_verdict_flags_contradictory_signals(stack):
    """A shrinking gap while his lap times are quicker is not a pace story.

    A stop, traffic or an out-lap breaks the link between the two signals, and
    asserting "you're catching him" from that would be a confidently wrong call.
    """
    tools = await _race(stack, player_pace=96_500, rival_pace=95_700)
    verdict = await tools.get_pace_verdict("Perez")
    assert verdict["verdict"] == "closing"
    assert verdict["signal_conflict"] is not None
    assert "traffic" in verdict["signal_conflict"]


@pytest.mark.asyncio
async def test_pace_verdict_refuses_the_players_own_car(stack):
    tools = await _race(stack)
    assert (await tools.get_pace_verdict("me"))["available"] is False
    assert (await tools.get_pace_verdict("Schumacher"))["available"] is False


@pytest.mark.asyncio
async def test_race_picture_names_one_threat_and_one_opportunity(stack):
    tools = await _race(stack)
    picture = await tools.get_race_picture()

    assert picture["available"] is True
    assert picture["position"] == 4
    # The nearest arriving car is the threat, not merely the closest.
    assert picture["threat"]["driver"] == "Nico Hulkenberg"
    assert picture["threat"]["laps_to_contact"] < picture["opportunity"]["laps_to_contact"]
    assert picture["opportunity"]["driver"] == "Sergio Perez"
    # A single sentence a driver can act on, led by the more urgent of the two.
    assert "Hulkenberg" in picture["headline"]
    assert "laps to contact" in picture["headline"]
    # Own state is a judgement, not just a value.
    assert picture["your_pace"]["trend_meaning"] in {"degrading", "improving", "steady"}
    assert picture["your_tyre"]["max_wear_pct"] == 58.0


@pytest.mark.asyncio
async def test_race_picture_lists_only_cars_that_are_actually_arriving(stack):
    """A car sitting at a constant gap is neither a threat nor an opportunity."""
    store = stack[0]
    tools = await _race(stack)

    def steady(state):
        for index in (1, 2):
            state.drivers[index].gap_history = [
                {"session_time_s": 1000 + k * 90, "lap": 19 + k, "gap_s": 4.0}
                for k in range(6)
            ]

    await store.mutate(steady)
    picture = await tools.get_race_picture()
    assert picture["threat"] is None
    assert picture["opportunity"] is None
    assert "stable" in picture["headline"] or "pace" in picture["headline"]


@pytest.mark.asyncio
async def test_race_picture_ignores_a_car_in_the_pit_lane(stack):
    store = stack[0]
    tools = await _race(stack)
    await store.mutate(lambda s: setattr(s.drivers[2], "pit_lane_timer_active", True))
    picture = await tools.get_race_picture()
    assert picture["threat"] is None, "a car serving a stop is not closing on you"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "how am I doing against Perez",
        "how is my pace compared to the leader",
        "am I faster than Hulkenberg",
        "how is the race going",
        "where do I stand",
        "how are we looking",
        "can I catch him",
    ],
)
async def test_comparative_and_open_questions_reach_the_analysis_layer(stack, utterance):
    """These were previously answered with a lap time or a list of gaps."""
    store, database = stack[0], stack[1]
    tools = await _race(stack)
    brain = EngineerBrain(store, tools, database)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, utterance) is True, utterance


@pytest.mark.asyncio
async def test_a_plain_closing_question_keeps_its_measured_answer(stack):
    """The cars-ahead report already gives a verdict with a measured trend.

    Routing it to the model would lose that guarantee, so it stays deterministic.
    """
    store, database = stack[0], stack[1]
    tools = await _race(stack)
    brain = EngineerBrain(store, tools, database)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, "am I closing in on the cars in front") is False


def test_persona_requires_analysis_over_readout():
    flat = " ".join(PERSONA.lower().split())
    assert "you are analysing, not reading out" in flat
    assert "a number on its own is not an answer" in flat
    assert "trends beat snapshots" in flat
    assert "get_pace_verdict" in flat and "get_race_picture" in flat


def test_realtime_radio_offers_the_analysis_tools():
    from pitwall.realtime import RADIO_TOOLS, REALTIME_PERSONA

    assert "get_pace_verdict" in RADIO_TOOLS
    assert "get_race_picture" in RADIO_TOOLS
    assert "analyse, do not read out" in " ".join(REALTIME_PERSONA.lower().split())
