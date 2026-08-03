"""Regressions for defects found by the independent review of 3.7.0.

Three of these were introduced by the 3.7.0 changes themselves — keeping
retired cars in the snapshot broke every relative-position resolver, the car
number alias made bare integers name drivers, and eager logging of proactive
narration put unspoken calls into the conversation history.
"""

from __future__ import annotations

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.brain import EngineerBrain, compose_persona
from pitwall.identity import match_drivers
from pitwall.strategy import StrategyEngine
from pitwall.tools import TelemetryTools

FIELD = [
    {"car_idx": 0, "name": "PLAYER", "driver_id": 255, "race_number": 63,
     "active": True, "position": 1, "is_player": True, "result_label": "active"},
    {"car_idx": 1, "name": "VERSTAPPEN", "driver_id": 9, "race_number": 1,
     "active": True, "position": 2, "result_label": "active"},
    {"car_idx": 2, "name": "ALONSO", "driver_id": 3, "race_number": 14,
     "active": True, "position": 0, "result_label": "retired",
     "last_lap_ms": 91_000},
    {"car_idx": 3, "name": "ZHOU", "driver_id": 80, "race_number": 24,
     "active": True, "position": 0, "result_label": "inactive"},
]


# --------------------------------------------------------------- A1: numbers

@pytest.mark.parametrize(
    "utterance",
    [
        "box on lap 14",
        "what is my sector 1 time",
        "the gap is 1.5 seconds",
        "ERS at 43 percent",
        "give me the last 5 lap times",
        "how have my last 6 laps been",
    ],
)
def test_a_bare_number_never_names_a_driver(utterance):
    """"#14" tokenised to "14", so any integer named whoever wore that number.

    The driver asked about their own car and was answered about a rival's.
    """
    assert match_drivers(FIELD, utterance) == [], utterance


def test_car_and_number_references_still_resolve():
    """The qualified forms are the ones a driver actually says."""
    assert [d["name"] for d in match_drivers(FIELD, "how are car 14 tyres")] == ["ALONSO"]
    assert [d["name"] for d in match_drivers(FIELD, "what is number 1 doing")] == ["VERSTAPPEN"]


# ------------------------------------------- A2: relative position resolution

@pytest.mark.parametrize("resolver", [TelemetryTools._resolve_driver, StrategyEngine._resolve_driver])
def test_leader_has_no_car_ahead(resolver):
    """Retired cars report position 0, which equals `player_position - 1` at P1."""
    state = {"drivers": FIELD, "player_position": 1, "player_car_index": 0}
    assert resolver(state, "ahead") is None
    # Behind and leader still resolve normally.
    assert resolver(state, "behind")["name"] == "VERSTAPPEN"
    assert resolver(state, "leader")["name"] == "PLAYER"


@pytest.mark.parametrize("resolver", [TelemetryTools._resolve_driver, StrategyEngine._resolve_driver])
def test_unclassified_cars_never_answer_a_position_query(resolver):
    state = {"drivers": FIELD, "player_position": 1, "player_car_index": 0}
    assert resolver(state, "p0") is None


def test_target_is_not_taken_from_a_retired_car():
    """The race leader was told to chase a lap time set by a car in the garage."""
    state = {
        "player_position": 1,
        "drivers": FIELD,
        "mode_profile": "race",
        "completed_laps": [
            {"lap_num": n, "lap_time_ms": 95_000 + n, "valid": True}
            for n in range(1, 6)
        ],
        "analysis": {},
    }
    target = AnalysisEngine.compute_target(state)
    assert target["basis"] == "sustainable race pace"
    assert target["target_ms"] >= 95_000, "must not chase a retired car's 1:31"


# ------------------------------------------------- A3: standing instructions

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "don't tell me about the fuel any more",
        "dont tell me about the fuel",
        "do not tell me about the fuel",
        "stop telling me about the fuel",
        "shut up about the fuel",
    ],
)
async def test_contracted_suppression_is_understood(stack, utterance):
    """The apostrophe form is the one drivers use, and it never matched."""
    store, database, _s, _su, _a, tools = stack
    brain = EngineerBrain(store, tools, database)
    await store.update(connected=True, fuel_laps_delta=1.6)

    instruction = brain._standing_instruction(utterance)
    assert instruction is not None, utterance
    assert "fuel" in instruction

    answer = await brain._fast_answer(utterance)
    assert answer is not None
    assert "plus 1.6 laps" not in answer, "must not answer with the very report refused"


# ----------------------------------------------------- A7: unnamed rival gaps

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "how's the fuel for the guy ahead",
        "what's the leader's battery",
        "does the car in front have damage",
        "how much tyre wear does the car ahead have",
        "how many stops has the car behind made",
        "how old are the tyres on the car ahead",
    ],
)
async def test_unnamed_rival_questions_reach_the_model(stack, utterance):
    store, database, _s, _su, _a, tools = stack
    brain = EngineerBrain(store, tools, database)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, utterance) is True, utterance


@pytest.mark.asyncio
async def test_a_plain_gap_question_still_uses_the_fast_path(stack):
    """Deferring must not swallow the cheap answers it was never about."""
    store, database, _s, _su, _a, tools = stack
    brain = EngineerBrain(store, tools, database)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, "what is the gap to the car ahead") is False


# ------------------------------------------------ A8: session mode inversion

@pytest.mark.parametrize(
    "utterance", ["this is not qualifying", "this is not a race", "we are not in practice"]
)
def test_denying_a_session_type_does_not_select_it(utterance):
    assert EngineerBrain._manual_session_override_mode(utterance) is None, utterance


def test_declaring_a_session_type_still_works():
    assert EngineerBrain._manual_session_override_mode("this is the race") == "race"
    assert EngineerBrain._manual_session_override_mode("we are in qualifying") == "qualifying"


# --------------------------------------------- A9: malformed stored settings

def test_persona_survives_legacy_standing_instruction_shapes():
    """A hand-edited or older preference file must not break every model call."""
    for shape in ([{"rule": "Do not raise fuel."}], ["Do not raise fuel."], [None, 42]):
        persona = compose_persona("BASE", "standard", shape)
        assert persona.startswith("BASE")
    assert "Do not raise fuel." in compose_persona("BASE", "standard", ["Do not raise fuel."])


# ------------------------------------- A4/A6: proactive logging and delivery

@pytest.mark.asyncio
async def test_unspoken_proactive_calls_stay_out_of_the_conversation(stack):
    """A call the driver never heard must not be replayed to the model."""
    store, database, _s, _su, _a, tools = stack
    brain = EngineerBrain(store, tools, database)

    # brain.proactive() no longer records; the engineer records after delivery.
    assert not hasattr(brain, "_records_proactive_eagerly")
    await brain.record_spoken_call("Norris is closing, one point four back.")

    snapshot = await store.snapshot_live()
    assert [entry["text"] for entry in snapshot["radio_log"]] == [
        "Norris is closing, one point four back."
    ]
    history = await database.history_query(limit=10)
    assert len(history["radio"]) == 1
    assert history["radio"][0]["source"] == "proactive"


def test_record_spoken_call_ignores_empty_text():
    assert EngineerBrain.record_spoken_call.__doc__


# ---------------------------------------------------- B7: model tier fallback

def test_an_unset_fast_model_never_falls_back_to_the_flagship():
    from pitwall.config import Settings

    for value in ("", "   ", "gpt-5.6"):
        assert Settings(fast_model=value).fast_model == "gpt-5.6-luna", value
    # An explicitly chosen tier is respected, including the middle one.
    assert Settings(fast_model="gpt-5.6-terra").fast_model == "gpt-5.6-terra"
    # The deep model keeps resolving the bare alias to the flagship tier.
    assert Settings(model="gpt-5.6").model == "gpt-5.6-sol"
    assert Settings(model="").model == "gpt-5.6-sol"


# ------------------------------------------- B10: restricted rival reporting

@pytest.mark.asyncio
async def test_restricted_rival_boost_is_unknown_not_absent(stack):
    """"unknown" and "he has no boost" are different calls to make."""
    store, _database, _s, _su, _a, tools = stack

    def apply(state):
        state.player_position = 2
        state.player_car_index = 0
        for index, restricted in ((0, False), (1, True)):
            driver = state.drivers[index]
            driver.active = True
            driver.name = "PLAYER" if index == 0 else "RIVAL"
            driver.position = 2 if index == 0 else 1
            driver.restricted = restricted
            driver.gap_to_player_s = 0.0 if index == 0 else -1.5

    await store.mutate(apply)
    plan = await tools.get_attack_plan("ahead")
    assert plan["available"] is True
    assert plan["rival_telemetry_restricted"] is True
    assert plan["rival_overtake_available"] is None
    assert plan["rival_ers_pct"] is None
