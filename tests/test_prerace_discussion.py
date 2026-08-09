"""Agreeing a race strategy on the grid.

The engineer proposes, the driver pushes back, and nothing constrains the race
until they agree. The failure that matters most here is not a clumsy reply - it
is acting on a sentence that was misread, because that commits a whole race to a
plan nobody chose.
"""

from __future__ import annotations

import pytest

from pitwall.prerace import PreRacePlanner


def _on_the_grid(state, *, current_lap=0, total_laps=57, compound="MEDIUM"):
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = current_lap
    state.total_laps = total_laps
    state.player_position = 8
    state.active_cars = 20
    state.track_id = 10
    state.player_car_index = 0
    state.tyre.compound = compound
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
    player.tyre_compound = compound


async def _grid(stack):
    """Put the car on the grid and open a planner against it."""
    store, _, strategy, _, _, _ = stack
    await store.mutate(_on_the_grid)
    return store, strategy, PreRacePlanner(store, strategy)


# ---------------------------------------------------------------------------
# When planning is offered at all
# ---------------------------------------------------------------------------


def test_planning_is_offered_on_the_grid_and_lap_one() -> None:
    base = {"mode_profile": "race", "total_laps": 57}
    assert PreRacePlanner.is_prerace({**base, "current_lap": 0})
    assert PreRacePlanner.is_prerace({**base, "current_lap": 1})
    assert not PreRacePlanner.is_prerace({**base, "current_lap": 12})


def test_planning_is_not_offered_outside_a_race() -> None:
    assert not PreRacePlanner.is_prerace(
        {"mode_profile": "qualifying", "total_laps": 0, "current_lap": 0}
    )
    assert not PreRacePlanner.is_prerace(
        {"mode_profile": "practice", "total_laps": 0, "current_lap": 0}
    )
    # A race whose distance has not arrived yet cannot be planned against.
    assert not PreRacePlanner.is_prerace(
        {"mode_profile": "race", "total_laps": 0, "current_lap": 0}
    )
    # Sprints are races too.
    assert PreRacePlanner.is_prerace(
        {"mode_profile": "sprint", "total_laps": 19, "current_lap": 0}
    )


# ---------------------------------------------------------------------------
# The opening proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_engineer_opens_with_a_plan_and_the_alternatives(stack) -> None:
    _, _, planner = await _grid(stack)
    briefing = await planner.propose()

    assert briefing["phase"] == "proposed"
    plan = briefing["proposal"]
    assert plan["compounds"], briefing
    assert len(plan["box_laps"]) == plan["stops"]
    # A choice needs something to choose between.
    assert len(briefing["alternatives"]) >= 2
    assert briefing["spoken"].endswith("Happy with that?")


@pytest.mark.asyncio
async def test_proposing_does_not_commit_anything(stack) -> None:
    """The whole point of a proposal is that it is not yet a decision."""
    store, _, planner = await _grid(stack)
    await planner.propose()

    override = (await store.snapshot_analysis())["strategy_override"]
    assert not override.get("plan")
    assert override.get("enabled") is False


# ---------------------------------------------------------------------------
# The driver's turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agreeing_puts_the_plan_in_charge(stack) -> None:
    store, _, planner = await _grid(stack)
    briefing = await planner.propose()
    proposed = briefing["proposal"]

    after = await planner.respond("yeah lock it in")

    assert after["phase"] == "agreed"
    assert after["committed"] is True
    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["plan"]["compounds"] == proposed["compounds"]
    assert override["plan_agreed"] is True
    assert override["enabled"] is True


@pytest.mark.asyncio
async def test_yes_but_is_a_change_not_an_agreement(stack) -> None:
    """The misread that would matter most.

    "Yes, but make it a one-stop" contains an agreement word and a change. Acting
    on the agreement would lock the race into the plan the driver was in the
    middle of rejecting.
    """
    store, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("yes but make it a one stop")

    assert after["phase"] == "negotiating", after["spoken"]
    assert after["committed"] is False
    assert after["proposal"]["stops"] == 1
    override = (await store.snapshot_analysis())["strategy_override"]
    assert not override.get("plan")


@pytest.mark.asyncio
async def test_asking_for_a_different_shape_changes_the_shape(stack) -> None:
    _, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("i want to do a one stop")

    assert after["proposal"]["stops"] == 1
    assert len(after["proposal"]["box_laps"]) == 1
    assert after["phase"] == "negotiating"


@pytest.mark.asyncio
async def test_an_impossible_shape_is_set_but_flagged(stack) -> None:
    """The driver gets what they asked for, and the reason it is a bad idea.

    Refusing outright would be the engine overruling the driver; staying silent
    would let them commit blind.
    """
    _, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("one stop please")

    assert after["proposal"]["stops"] == 1
    assert "tyre" in after["spoken"].lower() or "wear" in after["spoken"].lower()


@pytest.mark.asyncio
async def test_moving_a_stop_to_a_named_lap(stack) -> None:
    _, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("box on lap 22 instead")

    assert after["proposal"]["box_laps"][0] == 22


@pytest.mark.asyncio
async def test_nudging_the_stops_earlier_and_later(stack) -> None:
    _, _, planner = await _grid(stack)
    opening = await planner.propose()
    original = list(opening["proposal"]["box_laps"])

    later = await planner.respond("i want to go longer on this set")
    assert later["proposal"]["box_laps"] == [lap + 3 for lap in original]

    earlier = await planner.respond("actually bring it forward")
    assert earlier["proposal"]["box_laps"] == original


@pytest.mark.asyncio
async def test_choosing_the_starting_tyre(stack) -> None:
    _, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("i want to start on softs")

    assert after["proposal"]["compounds"][0] == "SOFT"


@pytest.mark.asyncio
async def test_choosing_the_final_tyre(stack) -> None:
    _, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("i want to finish on softs")

    assert after["proposal"]["compounds"][-1] == "SOFT"


@pytest.mark.asyncio
async def test_a_tyre_mentioned_without_a_place_changes_nothing(stack) -> None:
    """"The softs look quick here" is an observation, not an instruction.

    Rewriting the plan from it would have the driver fighting a strategy they
    never asked for.
    """
    _, _, planner = await _grid(stack)
    opening = await planner.propose()
    before = dict(opening["proposal"])

    after = await planner.respond("the softs looked quick in practice")

    assert after["proposal"]["compounds"] == before["compounds"]
    assert after["proposal"]["box_laps"] == before["box_laps"]


@pytest.mark.asyncio
async def test_an_unrecognised_sentence_asks_rather_than_guesses(stack) -> None:
    _, _, planner = await _grid(stack)
    await planner.propose()

    after = await planner.respond("what a beautiful day for it")

    assert after["committed"] is False
    assert "did not catch" in after["spoken"]
    # And it repeats where things stand, so the driver is not left guessing.
    assert "stop" in after["spoken"].lower()


@pytest.mark.asyncio
async def test_asking_why_explains_without_changing_the_plan(stack) -> None:
    _, _, planner = await _grid(stack)
    opening = await planner.propose()
    before = dict(opening["proposal"])

    after = await planner.respond("why that plan")

    assert after["proposal"] == before
    assert after["committed"] is False
    assert len(after["spoken"]) > 20


@pytest.mark.asyncio
async def test_a_disqualifying_request_is_refused_with_the_reason(stack) -> None:
    _, _, planner = await _grid(stack)
    await planner.propose()

    # Trying to run mediums the whole way is a DQ, not a strategy.
    after = await planner.respond("no stop at all")

    assert after["committed"] is False
    assert "disqualification" in after["spoken"].lower()


# ---------------------------------------------------------------------------
# Ending the discussion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discarding_hands_the_race_back_to_the_engine(stack) -> None:
    store, _, planner = await _grid(stack)
    await planner.propose()
    await planner.respond("lock it in")
    assert (await store.snapshot_analysis())["strategy_override"]["plan"]

    after = await planner.discard()

    assert after["phase"] == "idle"
    override = (await store.snapshot_analysis())["strategy_override"]
    assert not override["plan"]
    assert override["enabled"] is False


@pytest.mark.asyncio
async def test_discarding_leaves_a_hand_set_override_alone(stack) -> None:
    """Only the pre-race plan is the planner's to withdraw.

    A driver who locked "box lap 30 for hards" from the dashboard has not asked
    for that to be cleared by abandoning a separate conversation.
    """
    store, _, planner = await _grid(stack)
    await store.update(
        strategy_override={
            "enabled": True,
            "locked": True,
            "plan": {},
            "plan_agreed": False,
            "next_box_lap": 30,
            "next_compound": "HARD",
            "preferred_stops": 1,
            "start_compound": None,
            "priority": "balanced",
            "source": "dashboard",
            "note": "",
            "updated_at": 0.0,
        }
    )

    await planner.discard()

    override = (await store.snapshot_analysis())["strategy_override"]
    assert override["enabled"] is True
    assert override["next_box_lap"] == 30


@pytest.mark.asyncio
async def test_the_transcript_stays_bounded(stack) -> None:
    # It rides the websocket several times a second; it is live state, not a
    # permanent record.
    _, _, planner = await _grid(stack)
    await planner.propose()
    for _ in range(12):
        await planner.respond("go longer")

    assert len((await planner.snapshot())["transcript"]) <= 12


# ---------------------------------------------------------------------------
# Not planning before the telemetry can support it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_proposal_until_the_fitted_tyre_is_known(stack) -> None:
    """Found by running it, not by reading it.

    The first frames of a session carry the race distance but not the compound.
    A proposal built then opened with "start on unknowns" - and worse, UNKNOWN
    counts as no dry compound used, so the only *legal* one-stop came back as a
    switch to intermediates in a dry race. Proposing early advises wrongly.
    """
    store, _, planner = await _grid(stack)
    await store.mutate(lambda s: setattr(s.tyre, "compound", "UNKNOWN"))

    briefing = await planner.propose()

    assert briefing["phase"] == "idle"
    assert briefing["proposal"] == {}
    assert "tyre" in briefing["spoken"].lower()


@pytest.mark.asyncio
async def test_it_proposes_as_soon_as_the_tyre_arrives(stack) -> None:
    store, _, planner = await _grid(stack)
    await store.mutate(lambda s: setattr(s.tyre, "compound", "UNKNOWN"))
    assert (await planner.propose())["phase"] == "idle"

    await store.mutate(lambda s: setattr(s.tyre, "compound", "MEDIUM"))
    briefing = await planner.propose()

    assert briefing["phase"] == "proposed"
    assert briefing["proposal"]["compounds"][0] == "MEDIUM"


@pytest.mark.asyncio
async def test_a_dry_proposal_never_starts_on_a_wet_tyre(stack) -> None:
    _, _, planner = await _grid(stack)
    briefing = await planner.propose()

    for shape in briefing["alternatives"]:
        assert not ({"INTER", "WET"} & set(shape["compounds"])), shape
