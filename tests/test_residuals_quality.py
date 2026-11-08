"""Small things the driver hears that must match what the engineer means.

The change condition read after every full plan, the ranking explanation the
strategy carries, a volunteered pit call when narration is unavailable, the
everyday "how are the tyres", and the trend reported for the car behind.
"""

import re
from copy import deepcopy

import pytest

from pitwall.brain import EngineerBrain
from pitwall.proactive import ProactiveEngineer
from pitwall.providers import ProviderResult

QUALIFIER = "provisional, confidence low, tyre estimates include track defaults"


def _race_state(state):
    state.connected = True
    state.session_uid = 3801
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = 21
    state.total_laps = 28
    state.track_id = 15
    state.track_name = "Texas"
    state.player_position = 10
    state.player_car_index = 9
    state.active_cars = 20
    state.tyre.compound = "MEDIUM"
    state.tyre.age_laps = 10
    state.tyre.wear = [40, 41, 42, 42]
    state.tyre_sets = [
        {"compound": "SOFT", "available": True, "wear_pct": 0, "usable_life_laps": 10},
        {"compound": "HARD", "available": True, "wear_pct": 0, "usable_life_laps": 20},
    ]
    for index, driver in enumerate(state.drivers[:20]):
        driver.active = True
        driver.position = index + 1
        driver.is_player = index == 9
        driver.name = f"Driver {index + 1}"
        driver.tyre_compound = "MEDIUM"
        driver.tyre_age = 12
        driver.gap_to_player_s = (index - 9) * 2.1
        driver.lap_history = [
            {"lap_num": lap, "lap_ms": 97_000 + index * 15, "valid_flags": 1,
             "s1_ms": 30_000, "s2_ms": 35_000, "s3_ms": 32_000 + index * 15}
            for lap in range(16, 21)
        ]
    state.drivers[9].tyre_stints = [
        {"compound": "HARD", "start_lap": 1, "end_lap": 10},
        {"compound": "MEDIUM", "start_lap": 11, "end_lap": 255},
    ]


# ------------------------------------------------------ strategy wording


@pytest.mark.asyncio
async def test_the_spoken_change_condition_does_not_sound_like_a_compound(stack):
    """'a hard tyre-wear limit breach' is heard as a limit on the hard tyre."""
    store, _database, strategy, _setup, _analysis, _tools = stack
    await store.mutate(_race_state)
    recommended = (await strategy.recompute())["recommended"]
    condition = recommended["call_changes_if"]
    assert recommended["change_condition"] == condition
    assert not re.search(r"\bhard\b", condition, re.IGNORECASE)
    assert "wear limit" in condition
    # It still reads after a call and is still recognised as naming wear, so a
    # reply that already gives a wear condition does not get a second one.
    spoken = EngineerBrain._with_strategy_change_condition("Stay out to the flag.", {"recommended": recommended})
    assert spoken == (
        "Stay out to the flag; the call changes for a safety car, red flag, wet crossover, "
        "new damage, or the tyres reaching their wear limit."
    )
    own = "Stay out to the flag; if the rear wear spikes we box."
    assert EngineerBrain._with_strategy_change_condition(own, {"recommended": recommended}) == own


@pytest.mark.asyncio
async def test_the_ranking_assumption_describes_the_ranking_actually_used(stack):
    """Equal finishes prefer the later first stop; time only settles what is left."""
    store, _database, strategy, _setup, _analysis, _tools = stack
    await store.mutate(_race_state)
    ranking = (await strategy.recompute())["assumptions"][0]
    assert ranking.startswith("Plans are ranked by projected finishing position first")
    assert "elapsed time only breaks" not in ranking
    assert ranking.index("later first stop") < ranking.index("risk-adjusted time")


# ------------------------------------------- volunteered strategy template


def _low_confidence_plan():
    best = {
        "box_lap": 14, "fit_compound": "HARD", "box_laps": [14], "compounds": ["MEDIUM", "HARD"],
        "feasible": True, "legal": True, "instruction": "Box lap 14 for HARD.",
        "positions_gained_vs_stay_out": 1, "positions_lost_by_stopping": 0,
    }
    return {
        "recommended": best, "plans": [best], "confidence": "low",
        "tyre_inventory": {"status": "known"},
        "model_summary": {"selected_stint_wear_source": "track_default",
                          "selected_stint_deg_source": "track_default"},
    }


def _template_state(plan):
    return {
        "current_lap": 6, "total_laps": 31, "mode_profile": "race",
        "tyre": {"compound": "MEDIUM", "age_laps": 6, "wear": [20, 21, 24, 25]},
        "strategy": plan,
    }


def test_the_strategy_change_template_is_provisional_when_the_plan_is():
    state = _template_state(_low_confidence_plan())
    payload = ProactiveEngineer._spoken_advice(state["strategy"]["recommended"], state)
    spoken = ProactiveEngineer._fallback_text({"type": "strategy_change", "payload": payload}, state)
    assert spoken == f"Box lap 14 for hards; {QUALIFIER}."


def test_a_released_hold_keeps_its_reason_first_and_qualifies_the_call():
    state = _template_state(_low_confidence_plan())
    payload = {
        **ProactiveEngineer._spoken_advice(state["strategy"]["recommended"], state),
        "hold_released_reason": "the rears passed the wear limit",
    }
    spoken = ProactiveEngineer._fallback_text({"type": "strategy_change", "payload": payload}, state)
    assert spoken == (
        "Your strategy hold is released because the rears passed the wear limit. "
        f"Box lap 14 for hards; {QUALIFIER}."
    )


@pytest.mark.parametrize("confidence", ["medium", "high"])
def test_a_supported_plan_is_still_called_firmly(confidence):
    plan = {**_low_confidence_plan(), "confidence": confidence}
    state = _template_state(plan)
    payload = ProactiveEngineer._spoken_advice(plan["recommended"], state)
    spoken = ProactiveEngineer._fallback_text({"type": "strategy_change", "payload": payload}, state)
    assert spoken == "Box lap 14 for hards."


@pytest.mark.asyncio
async def test_template_and_narrated_strategy_calls_say_the_same_thing(stack, monkeypatch):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    plan = _low_confidence_plan()
    await store.update(current_lap=6, total_laps=31, mode_profile="race", strategy=deepcopy(plan))
    brain = EngineerBrain(store, tools, database)

    async def generate(**kwargs):
        return ProviderResult("Box lap 14 for hards.", "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)
    state = await store.snapshot_analysis()
    event = {"type": "strategy_change", "payload": ProactiveEngineer._spoken_advice(plan["recommended"], state)}
    narrated = await brain.proactive(deepcopy(event))
    template = ProactiveEngineer._fallback_text(event, state)
    assert template == narrated == f"Box lap 14 for hards; {QUALIFIER}."
