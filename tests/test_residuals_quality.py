"""Small things the driver hears that must match what the engineer means.

The change condition read after every full plan, the ranking explanation the
strategy carries, a volunteered pit call when narration is unavailable, the
everyday "how are the tyres", and the trend reported for the car behind.
"""

import re
import time
from copy import deepcopy

import pytest

from pitwall.brain import EngineerBrain
from pitwall.proactive import ProactiveEngineer
from pitwall.providers import ProviderResult
from pitwall.strategy import StrategyEngine

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


# ------------------------------------------------------ how are the tyres


async def _tyre_radio(stack, monkeypatch):
    store, database, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, database)

    async def no_provider(*args, **kwargs):
        pytest.fail("A tyre-condition question must be answered locally, without a provider call")

    monkeypatch.setattr(brain, "_run", no_provider)
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 54321, packet_id=group)
    await store.update(
        session_type="Race", mode_profile="race", current_lap=8, total_laps=20,
        player_position=3, player_car_index=0,
        tyre={"compound": "MEDIUM", "age_laps": 5, "wear": [21, 22, 24, 28],
              "inner_temps_c": [92, 94, 96, 98]},
    )
    return store, brain


HOW_ARE_THE_TYRES = [
    "how are the tyres", "How are the tyres looking?", "how're my tyres",
    "How're the tires?", "how are my tyres doing", "how are the tyres holding up",
    "Mark, how are the tyres?",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", HOW_ARE_THE_TYRES)
async def test_how_are_the_tyres_is_the_tyre_condition_readout(stack, monkeypatch, utterance):
    store, brain = await _tyre_radio(stack, monkeypatch)
    assert EngineerBrain.classify_request(utterance) == "fast"
    answer = await brain.ask(utterance)
    assert answer == "Mediums, 5 laps old, rear-right limiting at 28 percent; front cores 92/94 C."
    assert answer == await brain.ask("what's the tyre condition")
    assert (await store.snapshot_analysis())["llm_provider"] == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["how are the tyres", "how're my tyres"])
async def test_how_are_the_tyres_is_not_read_out_from_stale_telemetry(stack, monkeypatch, utterance):
    store, brain = await _tyre_radio(stack, monkeypatch)
    await store.mutate(lambda state: state.packet_group_freshness.update({"10": time.time() - 100}))
    # An unrelated packet arrives, so the receiver itself is still connected.
    await store.mark_packet(2026, 26, 54321, packet_id=0)
    answer = await brain.ask(utterance)
    assert answer == "Tyre condition telemetry is stale; I cannot confirm the current value."


@pytest.mark.asyncio
async def test_how_are_the_tyres_while_paused_is_labelled_as_last_confirmed(stack, monkeypatch):
    store, brain = await _tyre_radio(stack, monkeypatch)
    await store.update(game_paused=True, connected=False)
    answer = await brain.ask("how are the tyres looking")
    assert answer == "Paused, last confirmed: Mediums, 5 laps old, rear-right limiting at 28 percent; front cores 92/94 C."


@pytest.mark.parametrize("utterance", [
    "how are his tyres", "how are the tyres on the car ahead",
    "how are the tyres compared to Norris", "how are the tyres, should I box",
    "how are the tyre temps",
])
def test_tyre_questions_about_something_else_still_go_to_the_model(utterance):
    state = {"drivers": [{"name": "NORRIS", "driver_id": 3, "car_idx": 1, "position": 5}]}
    assert EngineerBrain._defers_to_model(state, utterance)


# ------------------------------------------------------- the car behind


@pytest.mark.asyncio
async def test_defence_and_pace_verdict_agree_on_the_car_behind(stack):
    """He caught 1.2 s in the first lap and lost 0.4 s in the last.

    Over the whole history he is catching; over the last lap he is not. The
    pace verdict reads the last lap, so the defence plan for the same car must
    too, or the engineer says "being caught" and "pulling away" about one car.
    """
    store, _database, _strategy, _setup, _analysis, tools = stack
    for group in (2, 7):
        await store.mark_packet(2026, 26, 42, packet_id=group)

    def seed(state):
        state.connected = True
        state.current_lap = 12
        state.total_laps = 30
        state.player_position = 6
        state.player_car_index = 0
        state.mode_profile = "race"
        player, rival = state.drivers[0], state.drivers[1]
        player.car_idx, player.position, player.active, player.is_player = 0, 6, True, True
        player.name = "PLAYER"
        rival.car_idx, rival.position, rival.active = 1, 7, True
        rival.name, rival.driver_id = "HULKENBERG", 10
        rival.gap_to_player_s = 2.2
        rival.last_lap_ms = 90_000
        rival.gap_history = [
            {"session_time_s": 1000.0, "lap": 10, "gap_s": 3.0},
            {"session_time_s": 1090.0, "lap": 11, "gap_s": 1.8},
            {"session_time_s": 1180.0, "lap": 12, "gap_s": 2.2},
        ]

    await store.mutate(seed)
    verdict = await tools.get_pace_verdict("Hulkenberg")
    defence = (await tools.get_defence_plan("Hulkenberg"))["assessment"]
    assert verdict["verdict"] == "pulling away"
    assert defence["gap_trend"] == "pulling_away"
    assert defence["gap_change_s_per_lap"] == verdict["gap_change_s_per_lap"] == 0.4
    # Not being caught, so the defence holds to the flag.
    assert defence["defence_laps_sustainable"] == 19


def test_defence_trend_is_the_shared_lap_window_measure():
    state = {
        "player_position": 6, "player_car_index": 0, "current_lap": 12, "total_laps": 30,
        "drivers": [
            {"car_idx": 0, "position": 6},
            {"car_idx": 1, "position": 7, "name": "Rival", "gap_to_player_s": 2.2, "last_lap_ms": 90_000,
             "gap_history": [{"session_time_s": 1000.0 + 2.0 * k, "gap_s": 2.2 - 0.002 * k} for k in range(46)]},
        ],
        "analysis": {"corner_metrics": []},
    }
    # 0.09 s gained over the last 90 s: under the trend threshold every other
    # gap answer uses, so this is holding station, not being caught.
    result = StrategyEngine.defence_assessment(state)
    assert result["gap_trend"] == "holding_station"
    assert result["defence_laps_sustainable"] == 19
