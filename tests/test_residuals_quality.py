"""Small things the driver hears that must match what the engineer means.

The change condition read after every full plan, the ranking explanation the
strategy carries, a volunteered pit call when narration is unavailable, the
everyday "how are the tyres", and the trend reported for the car behind.
"""

import re

import pytest

from pitwall.brain import EngineerBrain


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
