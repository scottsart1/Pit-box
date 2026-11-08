"""Talking the plan through over the radio instead of typing it.

The panel and the radio need different thresholds for what counts as a plan
turn. Typing into the plan box is unambiguous; the same sentence spoken on the
grid competes with everything else a driver says. So the radio path takes only
what it plainly recognises and hands the rest back to the engineer.
"""

from __future__ import annotations

import pytest

from pitwall.prerace import PreRacePlanner


def _on_the_grid(state):
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = 1
    state.total_laps = 57
    state.player_position = 8
    state.active_cars = 20
    state.track_id = 10
    state.player_car_index = 0
    state.tyre.compound = "MEDIUM"
    state.tyre.age_laps = 0
    state.tyre.wear = [0.0, 0.0, 0.0, 0.0]
    state.tyre_sets = [
        {"compound": "SOFT", "available": True},
        {"compound": "MEDIUM", "available": True},
        {"compound": "HARD", "available": True},
    ]
    player = state.drivers[0]
    player.car_idx = 0
    player.is_player = True
    player.pit_stops = 0
    player.tyre_compound = "MEDIUM"


async def _open_discussion(stack):
    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    await planner.propose()
    return store, strategy, planner


# ---------------------------------------------------------------------------
# What the radio takes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "said",
    [
        "make it a one stop",
        "i want to start on softs",
        "box on lap 22",
        "let's go longer on the first stint",
        "yeah lock it in",
        "no i don't like that",
        "why that strategy",
    ],
)
async def test_plan_talk_is_taken(stack, said) -> None:
    _, _, planner = await _open_discussion(stack)
    assert await planner.try_respond(said) is not None, said


# ---------------------------------------------------------------------------
# What it hands back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "said",
    [
        "what's the gap to norris",
        "how's the weather looking",
        "my brakes feel soft",
        "what a beautiful day",
        "who is on pole",
    ],
)
async def test_everything_else_falls_through_to_the_engineer(stack, said) -> None:
    """The radio must not become a plan-only channel while a plan is open.

    Answering "what's the gap to Norris" with "tell me a number of stops" is
    worse than not having the feature.
    """
    _, _, planner = await _open_discussion(stack)
    assert await planner.try_respond(said) is None, said


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "said",
    [
        "radio check, do you copy?",
        "copy, what's the weather looking like",
        "yes, how much fuel have we got",
        "confirm the tyre temps",
        "yeah, what's the gap to norris",
        "what are the tyre temps, are they perfect for the start",
        "no rain expected today?",
        "no grip at all on the formation lap",
        "is there an alternative line into turn one",
        "Copy?",
    ],
)
async def test_common_radio_words_do_not_make_a_sentence_plan_talk(stack, said) -> None:
    """"Copy", "yes", "confirm", "perfect" and "no" are everywhere on the radio.

    Any one of them used to take the turn, and an accept word committed the
    whole race: "Radio check, do you copy?" answered "Locked in. 2-stop..."
    with nothing asked of the engineer answered at all.
    """
    store, _, planner = await _open_discussion(stack)
    assert await planner.try_respond(said) is None, said
    override = (await store.snapshot_analysis())["strategy_override"]
    assert not override.get("plan_agreed"), said


@pytest.mark.asyncio
async def test_a_plan_question_with_an_acknowledgement_is_answered_not_committed(stack) -> None:
    store, _, planner = await _open_discussion(stack)
    briefing = await planner.try_respond("why that strategy, copy?")
    assert briefing is not None
    assert not briefing.get("committed")
    override = (await store.snapshot_analysis())["strategy_override"]
    assert not override.get("plan_agreed")


@pytest.mark.asyncio
async def test_a_spoken_lap_number_moves_the_stop(stack) -> None:
    _, _, planner = await _open_discussion(stack)
    briefing = await planner.try_respond("box on lap twenty two")
    assert briefing is not None
    assert briefing["proposal"]["box_laps"][0] == 22


@pytest.mark.asyncio
async def test_nothing_is_taken_when_no_discussion_is_open(stack) -> None:
    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    # Never proposed, so there is no plan to be talking about.
    assert await planner.try_respond("make it a one stop") is None


@pytest.mark.asyncio
async def test_nothing_is_taken_once_the_plan_is_agreed(stack) -> None:
    # After agreement the in-race override owns strategy talk again.
    _, _, planner = await _open_discussion(stack)
    await planner.respond("lock it in")
    assert await planner.try_respond("make it a one stop") is None


# ---------------------------------------------------------------------------
# Through the engineer, end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_engineer_routes_a_spoken_change_into_the_plan(stack) -> None:
    from pitwall.brain import EngineerBrain

    store, database, strategy, _, _, tools = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    await planner.propose()

    brain = EngineerBrain(store, tools, database)
    spoken = await brain._fast_answer("make it a one stop")

    assert spoken, "the engineer should have answered from the plan discussion"
    briefing = await planner.snapshot()
    assert briefing["proposal"]["stops"] == 1
    # And it must not have leaked into the narrow single-stop override.
    override = (await store.snapshot_analysis())["strategy_override"]
    assert override.get("next_box_lap") is None


@pytest.mark.asyncio
async def test_agreeing_over_the_radio_commits_the_plan(stack) -> None:
    from pitwall.brain import EngineerBrain

    store, database, strategy, _, _, tools = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    await planner.propose()

    brain = EngineerBrain(store, tools, database)
    spoken = await brain._fast_answer("yes lock it in")

    assert "locked in" in spoken.lower(), spoken
    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["plan_agreed"] is True
    assert override["plan"]["compounds"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "said",
    ["do it", "yes, do it", "Do it.", "yeah thats fine", "lock it in, jenny"],
)
async def test_short_radio_agreements_still_commit_the_plan(stack, monkeypatch, said) -> None:
    """"Do it" is an order, not the start of a question like "do we box".

    Read as a question, "yes, do it" was handed to the engineer and the plan
    the driver had just agreed to was never locked. The engineer's name can
    end the sentence whatever it is set to.
    """
    from pitwall.config import settings

    monkeypatch.setattr(settings, "wake_phrase", "jenny")
    monkeypatch.setattr(settings, "wake_aliases", "")
    store, _, planner = await _open_discussion(stack)

    briefing = await planner.try_respond(said)

    assert briefing is not None and briefing.get("committed"), said
    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["plan_agreed"] is True, said


@pytest.mark.asyncio
async def test_a_radio_check_reaches_the_engineer_and_locks_nothing(stack) -> None:
    from pitwall.brain import EngineerBrain

    store, database, strategy, _, _, tools = stack
    await store.mutate(_on_the_grid)
    planner = PreRacePlanner(store, strategy)
    await planner.propose()

    brain = EngineerBrain(store, tools, database)
    assert await brain._fast_answer("Radio check, do you copy?") is None
    override = (await store.snapshot_analysis())["strategy_override"]
    assert not override.get("locked")
    assert not override.get("plan_agreed")


@pytest.mark.asyncio
async def test_the_in_race_override_still_works_with_no_discussion(stack) -> None:
    """Adding the plan path must not have captured the ordinary radio call."""
    from pitwall.brain import EngineerBrain

    store, database, strategy, _, _, tools = stack

    def mid_race(state):
        _on_the_grid(state)
        state.current_lap = 24

    await store.mutate(mid_race)
    brain = EngineerBrain(store, tools, database)
    await brain._fast_answer("box lap 30 for hards")

    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["next_box_lap"] == 30
    assert override["next_compound"] == "HARD"
