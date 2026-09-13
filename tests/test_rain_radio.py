"""What the driver hears about the weather, and what the engineer hears back.

The model can be right and the race still lost if the call arrives as "stand by
for a crossover call" three laps after the crossover. These pin the two halves
of the conversation: the engineer saying something a driver can act on, and the
driver's answer getting back into the model.
"""

from __future__ import annotations

import pytest

from pitwall.proactive import ProactiveEngineer


def _radio(payload: dict) -> str:
    return ProactiveEngineer._fallback_text(
        {"type": "weather_crossover", "payload": payload}, {}
    )


def test_a_called_stop_names_the_lap_and_the_tyre():
    text = _radio(
        {
            "worth_stopping": True,
            "compound": "INTER",
            "box_lap": 23,
            "reason": "Track is wet and getting wetter; INTER is worth 40s.",
        }
    )
    assert "INTER" in text
    assert "23" in text
    assert "stand by" not in text.lower(), (
        "a decided call was delivered as a standby notice"
    )


def test_a_marginal_call_puts_the_question_to_the_driver():
    text = _radio(
        {
            "worth_stopping": False,
            "ask_driver": True,
            "driver_question": "Is there standing water out there, or is it just heavy spray?",
            "reason": "Track is fully wet and holding; WET is only 4s better.",
        }
    )
    assert text.rstrip().endswith("?"), f"the engineer never actually asked: {text!r}"
    assert "standing water" in text


def test_staying_out_is_explained_rather_than_left_silent():
    """A driver in the rain hearing nothing assumes nobody is looking."""
    reason = (
        "Track is fully wet and drying through the stint; INTER is still the "
        "right tyre for the laps that are left."
    )
    text = _radio({"worth_stopping": False, "ask_driver": False, "reason": reason})
    assert reason in text


def test_the_old_percentage_notice_still_covers_a_payload_with_nothing_in_it():
    text = _radio({"rain_15_pct": 70})
    assert "70" in text
    assert text.strip()


# --- the driver's half of it ------------------------------------------------


@pytest.mark.asyncio
async def test_a_driver_reporting_the_track_is_recorded_as_a_grip_report(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    from pitwall.brain import EngineerBrain

    brain = EngineerBrain(store, tools, database)

    def grid(state):
        state.current_lap = 14
        state.session_type = "Race"
        state.mode_profile = "race"

    await store.mutate(grid)
    await brain._capture_feedback("It's aquaplaning everywhere out here")

    feedback = (await store.snapshot_analysis()).get("driver_grip_feedback") or {}
    assert feedback.get("category") == "flooded"
    assert feedback.get("lap") == 14


@pytest.mark.asyncio
async def test_a_driver_reporting_a_dry_line_is_not_read_as_a_tyre_complaint(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    from pitwall.brain import EngineerBrain

    brain = EngineerBrain(store, tools, database)

    def grid(state):
        state.current_lap = 30
        state.session_type = "Race"
        state.mode_profile = "race"

    await store.mutate(grid)
    await brain._capture_feedback("There's a dry line coming through now")

    state = await store.snapshot_analysis()
    assert (state.get("driver_grip_feedback") or {}).get("category") == "dry_line"
    assert not (state.get("driver_tyre_feedback") or {}).get("category"), (
        "a report about the track was filed as a report about the tyres"
    )


@pytest.mark.asyncio
async def test_a_reported_mistake_is_pinned_to_the_lap_it_happened_on(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    from pitwall.brain import EngineerBrain

    brain = EngineerBrain(store, tools, database)

    def grid(state):
        state.current_lap = 18
        state.session_type = "Race"
        state.mode_profile = "race"

    await store.mutate(grid)
    await brain._capture_feedback("Sorry, I went off at turn twelve")

    incidents = (await store.snapshot_analysis()).get("driver_lap_incidents") or []
    laps = {int(item["lap"]) for item in incidents}
    assert 18 in laps
    # A driver reports the mistake as they cross the line as often as during the
    # lap itself, so the lap just completed has to be covered too.
    assert 17 in laps


@pytest.mark.asyncio
async def test_a_tyre_complaint_and_a_track_report_can_both_land(stack):
    """"They're gone and it's soaking" is two facts, not one."""
    store, database, _strategy, _setup, _analysis, tools = stack
    from pitwall.brain import EngineerBrain

    brain = EngineerBrain(store, tools, database)

    def grid(state):
        state.current_lap = 22
        state.session_type = "Race"
        state.mode_profile = "race"

    await store.mutate(grid)
    await brain._capture_feedback("They're gone, and it's soaking out here")

    state = await store.snapshot_analysis()
    assert (state.get("driver_tyre_feedback") or {}).get("category") == "tyres_gone"
    assert (state.get("driver_grip_feedback") or {}).get("category") == "soaked"
