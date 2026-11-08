"""Routing decides what the driver hears: the computed plan, a stored value or
the model's narration, and how long they wait for it.

Each case here is a wording a driver actually uses for an ordinary request.
The same request must get the same treatment however it is phrased: "box" is
the radio word for "pit", filler in front changes nothing, and a second
question in the same breath must not be swallowed by the standing pit call.
"""

from __future__ import annotations

import time

import pytest

from pitwall.brain import EngineerBrain
from pitwall.providers import ProviderFailure, ProviderRequestError, ProviderResult

BEST = {
    "feasible": True, "legal": True, "box_laps": [12], "compounds": ["MEDIUM", "HARD"],
    "box_lap": 12, "fit_compound": "HARD", "instruction": "Box lap 12 for HARD.",
    "stops_remaining": 1,
}
ALTERNATE = {**BEST, "box_laps": [10, 16], "compounds": ["MEDIUM", "SOFT", "SOFT"]}


async def _race(stack, *, confidence: str = "low"):
    store, database, _strategy, _setup, _analysis, tools = stack
    for group in (1, 2, 5, 6, 7, 10, 16):
        await store.mark_packet(2026, 26, 54321, packet_id=group)

    def apply(state):
        state.connected = True
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 8
        state.total_laps = 20
        state.player_position = 5
        state.player_car_index = 0
        state.fuel_laps_delta = 0.8
        state.weather = "Light cloud"
        state.weather_forecast = [{"time_offset_min": 15, "rain_pct": 20}]
        state.rain_next_15_pct = 20
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 8
        state.tyre.wear = [24.0, 25.0, 27.0, 28.0]
        state.strategy = {
            "recommended": dict(BEST), "plans": [dict(BEST), dict(ALTERNATE)],
            "confidence": confidence, "tyre_inventory": {"status": "known"},
        }
        rows = [("PLAYER", 255, 5, 0.0), ("VERSTAPPEN", 9, 4, -1.8), ("NORRIS", 7, 6, 1.2)]
        for index, (name, driver_id, position, gap) in enumerate(rows):
            driver = state.drivers[index]
            driver.name = name
            driver.driver_id = driver_id
            driver.active = True
            driver.position = position
            driver.gap_to_player_s = gap
            driver.last_lap_ms = 97_550

    await store.mutate(apply)
    return store, EngineerBrain(store, tools, database)


def _forbid_provider(brain, monkeypatch):
    async def forbidden(**kwargs):
        pytest.fail("This request must be answered without a provider call")

    monkeypatch.setattr(brain.router, "generate", forbidden)


def _model_reads_the_plan(brain, monkeypatch, calls: list[dict]):
    async def generate(**kwargs):
        calls.append(kwargs)
        await kwargs["execute_tool"]("get_pit_strategy", {})
        return ProviderResult("Box lap 12 for hards.", "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)


def _provider_fails(brain, monkeypatch, *failures: ProviderFailure):
    async def generate(**kwargs):
        raise ProviderRequestError(list(failures))

    monkeypatch.setattr(brain.router, "generate", generate)


# ---------------------------------------------------------------------------
# Asking for the pit call: every wording gets the computed, qualified plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "should I pit",
    "should I box",
    "should we box",
    "should we pit",
    "when do I box",
    "when do we box",
    "box now?",
    "is it time to pit",
    "what's the plan",
    "when are we pitting",
    "are we boxing this lap",
    "do I box this lap",
    "pit this lap?",
    "Okay, should we stay out?",
    "Should we pit now under the safety car?",
    "which lap do I box",
    "Is it time to box yet?",
    "should I pit now or later",
    "should I box now or next lap",
    "should we stop this lap",
])
async def test_every_way_of_asking_for_the_pit_call_gets_the_qualified_plan(stack, monkeypatch, utterance):
    store, brain = await _race(stack)
    _forbid_provider(brain, monkeypatch)
    answer = await brain.ask(utterance)
    assert "box lap 12" in answer.lower(), answer
    assert "confidence low" in answer.lower(), answer
    assert (await store.snapshot_analysis())["llm_provider"] == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "What's my fuel and should I pit?",
    "should we box if the safety car comes out",
    "can I make it to the end on these tyres",
    "when do we box, and how is the rear left looking",
    "I have a puncture, should I come in?",
    "the safety car is out, what's the plan",
])
async def test_a_pit_question_left_to_the_model_carries_the_plan_and_its_qualifier(stack, monkeypatch, utterance):
    _store, brain = await _race(stack)
    calls: list[dict] = []
    _model_reads_the_plan(brain, monkeypatch, calls)
    answer = await brain.ask(utterance)
    assert calls, "a compound or conditional pit question needs the model"
    assert "| strategy Box lap 12 for HARD." in calls[0]["prompt"]
    assert "confidence low" in answer.lower(), answer


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "What's my fuel and should I pit?",
    "when do we box, and how is the rear left looking",
    "can I make it to the end on these tyres",
    "the safety car is out, what's the plan",
])
async def test_a_pit_question_still_gets_the_computed_plan_when_the_model_times_out(stack, monkeypatch, utterance):
    store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", "deadline", "test deadline"))
    answer = await brain.ask(utterance)
    assert "Engineer analysis timed out; current computed plan: box lap 12 for hards" in answer
    assert (await store.snapshot_analysis())["llm_model"] == "strategy-deadline-fallback"


@pytest.mark.parametrize("utterance", [
    "how much fuel do I need to stay out of trouble with the car behind",
    "What is my gap ahead?",
    "stay out of the way of the leader",
    "can I make it to the end on this fuel",
    "when should I stop pushing",
    "what's the plan for the setup",
])
def test_words_that_only_look_like_a_pit_call_are_not_strategy_requests(utterance):
    assert not EngineerBrain._is_strategy_request(utterance)


@pytest.mark.parametrize("utterance,asks", [
    ("We're staying out, right?", True),
    ("can we go long on this set", True),
    ("Okay, I'm staying out", False),
    ("let's go long", False),
])
def test_a_stint_decision_said_flat_is_not_a_request_for_the_call(utterance, asks):
    assert EngineerBrain._is_strategy_request(utterance) is asks


# ---------------------------------------------------------------------------
# The standing pit call answers the pit call, not a question that contains it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "What's my fuel and should I pit?",
    "Should I stay out, what's the gap behind?",
    "Should I pit now or stay out and what's the weather doing?",
    "What happens if I stay out?",
    "Would it be quicker to stay out on these mediums than box for hards?",
    "should I pit if the safety car comes out",
    "what if I stay out",
    "how long can I stay out on these",
    "can I stay out until the end",
])
async def test_compound_and_hypothetical_pit_questions_are_not_answered_with_the_standing_call(stack, utterance):
    store, brain = await _race(stack)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, utterance) is True, utterance
    assert await brain._fast_answer(utterance) is None, utterance


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "should I pit", "pit now", "Pit now or stay out?", "stay out?",
    "so should I box this lap or stay out", "should we stay out or box",
])
async def test_the_bare_pit_call_keeps_its_deterministic_answer(stack, utterance):
    store, brain = await _race(stack)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, utterance) is False, utterance
    answer = await brain._fast_answer(utterance)
    assert answer and "box lap 12" in answer.lower(), (utterance, answer)


@pytest.mark.asyncio
async def test_a_correction_in_front_of_the_pit_call_goes_to_the_model(stack):
    store, brain = await _race(stack)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, "No, box now") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["should I stop?", "should we stop"])
async def test_should_i_stop_on_its_own_is_not_taken_for_the_pit_call(stack, utterance):
    # It may mean stopping the car, which the radio history has to settle.
    store, brain = await _race(stack)
    state = await store.snapshot_analysis()
    assert brain._defers_to_model(state, utterance) is True
    assert await brain._fast_answer(utterance) is None


async def _red_flag(store):
    await store.update(red_flag_active=True, race_control_phase="red_flag")


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "should we box if the safety car comes out",
    "when do we box, and how is the rear left looking",
    "can I make it to the end on these tyres",
])
async def test_a_compound_pit_question_under_a_red_flag_is_not_answered_with_the_restart_summary(
    stack, monkeypatch, utterance,
):
    store, brain = await _race(stack)
    await _red_flag(store)
    calls: list[dict] = []
    _model_reads_the_plan(brain, monkeypatch, calls)
    await brain.ask(utterance)
    assert calls, utterance
    assert "| strategy" in calls[0]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["should we box", "what's the plan", "what's the plan for the restart"])
async def test_the_bare_pit_call_under_a_red_flag_gets_the_restart_summary(stack, monkeypatch, utterance):
    store, brain = await _race(stack)
    await _red_flag(store)
    _forbid_provider(brain, monkeypatch)
    answer = await brain.ask(utterance)
    assert "Tyres can be changed while the race is suspended" in answer, answer


# ---------------------------------------------------------------------------
# Everyday wordings of a stored value are answered from telemetry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("utterance,expected", [
    ("what's the gap ahead", "VERSTAPPEN ahead, 1.8 seconds"),
    ("whats the gap ahead", "VERSTAPPEN ahead, 1.8 seconds"),
    ("what is the gap ahead", "VERSTAPPEN ahead, 1.8 seconds"),
    ("what's the gap behind", "NORRIS behind, 1.2 seconds"),
    ("whats the gap to the car behind", "NORRIS behind, 1.2 seconds"),
    ("Okay, what's the gap behind?", "NORRIS behind, 1.2 seconds"),
    ("fuel ok", "Fuel is plus 0.8 laps"),
    ("are we ok on fuel", "Fuel is plus 0.8 laps"),
    ("what's the fuel like", "Fuel is plus 0.8 laps"),
    ("copy, fuel status", "Fuel is plus 0.8 laps"),
    ("how's the weather looking", "rain risk 20 percent"),
    ("what position are we in", "P5"),
])
async def test_everyday_lookups_are_answered_without_a_provider(stack, monkeypatch, utterance, expected):
    store, brain = await _race(stack)
    _forbid_provider(brain, monkeypatch)
    answer = await brain.ask(utterance)
    assert expected in answer, answer
    assert (await store.snapshot_analysis())["llm_provider"] == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance,group", [
    ("what's the gap ahead", 2),
    ("are we ok on fuel", 7),
    ("what place are we in", 2),
])
async def test_widened_lookups_keep_the_packet_freshness_guard(stack, monkeypatch, utterance, group):
    store, brain = await _race(stack)
    _forbid_provider(brain, monkeypatch)
    await store.mutate(lambda state: state.packet_group_freshness.update({str(group): time.time() - 100}))
    await store.mark_packet(2026, 26, 54321, packet_id=0)
    answer = await brain.ask(utterance)
    assert "stale" in answer and "cannot confirm the current value" in answer
    assert "1.8" not in answer and "0.8" not in answer and "P5" not in answer


RAMBLE = (
    "okay so I was just thinking about the last few laps and the car felt a bit "
    "loose in the high speed stuff but anyway mate I just want to know what's the gap behind"
)


@pytest.mark.asyncio
async def test_a_quick_question_after_chat_still_gets_its_value_when_the_model_fails(stack, monkeypatch):
    store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", "deadline", "test deadline"))
    answer = await brain.ask(RAMBLE)
    assert answer.startswith("Engineer analysis timed out. NORRIS behind, 1.2 seconds"), answer
    state = await store.snapshot_analysis()
    assert state["llm_provider"] == "local"
    assert state["llm_model"] == "deterministic-radio-fallback"
    assert state["radio_log"][-1]["text"] == answer


@pytest.mark.asyncio
async def test_the_failure_readout_is_as_short_as_the_value_asked_on_its_own(stack, monkeypatch):
    store, brain = await _race(stack)
    await store.update(radio_verbosity="terse")
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", "deadline", "test deadline"))
    answer = await brain.ask(RAMBLE)
    assert answer == "Engineer analysis timed out. NORRIS behind, 1.2 seconds.", answer


@pytest.mark.asyncio
async def test_the_failure_readout_goes_through_the_freshness_guard(stack, monkeypatch):
    store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", "connection", "test failure"))
    await store.mutate(lambda state: state.packet_group_freshness.update({"2": time.time() - 100}))
    await store.mark_packet(2026, 26, 54321, packet_id=0)
    answer = await brain.ask(RAMBLE)
    assert "stale" in answer and "1.2" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance,kind", [
    ("why am I so slow in sector two and what's the gap behind", "deadline"),
    ("I want to move the brake bias forward, what's the gap behind", "deadline"),
    (RAMBLE, "configuration"),
    # The question is about the other car; the player's own damage is not the answer.
    ("I think Verstappen has damage, what's the damage like", "connection"),
    ("the car ahead has damage, what's the damage like", "connection"),
    ("I think he's got damage, any damage", "connection"),
])
async def test_no_readout_stands_in_for_reasoning_or_for_a_failure_to_fix(stack, monkeypatch, utterance, kind):
    _store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", kind, "test failure"))
    with pytest.raises(ProviderRequestError):
        await brain.ask(utterance)


@pytest.mark.parametrize("utterance", [
    "no, damage report",
    "what's the gap ahead and should I pit",
    "what's the gap ahead of Norris",
])
def test_a_lookup_with_anything_else_attached_is_not_simple(utterance):
    assert not EngineerBrain._is_simple_lookup(utterance)


# ---------------------------------------------------------------------------
# A transient provider failure on a strategy question still gives the plan
# ---------------------------------------------------------------------------

REQUEST = "Give me the full pit strategy and its best alternative."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,prefix", [
    ("timeout", "Engineer service timed out; "),
    ("connection", "Engineer service unreachable; "),
    ("service", "Engineer service unavailable; "),
    ("rate_limit", "Engineer service busy; "),
    ("truncation", "Engineer answer was cut off; "),
    ("response", "Engineer gave no usable answer; "),
    ("unknown", "Engineer service failed; "),
])
async def test_every_transient_failure_kind_falls_back_to_the_computed_plan(stack, monkeypatch, kind, prefix):
    store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", kind, "test failure"))
    answer = await brain.ask(REQUEST)
    assert prefix + "current computed plan: box lap 12 for hards" in answer, answer
    assert "alternative: box lap 10 for softs, then box lap 16 for softs" in answer
    state = await store.snapshot_analysis()
    assert state["llm_provider"] == "local"
    assert state["llm_model"] == "strategy-provider-fallback"
    assert "ProviderRequestError" in state["llm_last_error"]
    assert state["radio_log"][-1]["text"] == answer


@pytest.mark.asyncio
async def test_a_cooling_down_provider_does_not_withhold_the_plan(stack, monkeypatch):
    _store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure(
        "openai", "cooldown", "temporarily cooling down", attempted=False, retry_after_s=20.0,
    ))
    answer = await brain.ask(REQUEST)
    assert "Engineer service paused after repeated failures; current computed plan: box lap 12" in answer


@pytest.mark.asyncio
async def test_a_primary_deadline_is_not_hidden_by_the_fallbacks_later_failure(stack, monkeypatch):
    store, brain = await _race(stack)
    _provider_fails(
        brain, monkeypatch,
        ProviderFailure("openai", "deadline", "exceeded the 25-second deep deadline"),
        ProviderFailure("deepseek", "response", "still requesting tools after the final round"),
    )
    answer = await brain.ask(REQUEST)
    assert "Engineer analysis timed out; current computed plan: box lap 12 for hards" in answer
    assert (await store.snapshot_analysis())["llm_model"] == "strategy-deadline-fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["configuration", "authentication", "quota", "request"])
async def test_a_failure_the_driver_must_fix_keeps_its_actionable_message(stack, monkeypatch, kind):
    _store, brain = await _race(stack)
    _provider_fails(brain, monkeypatch, ProviderFailure("openai", kind, "test failure"))
    with pytest.raises(ProviderRequestError) as raised:
        await brain.ask(REQUEST)
    assert raised.value.kind == kind


# ---------------------------------------------------------------------------
# The reasoning tier follows what is asked, not incidental words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "How slow would a one-stop to hards on lap 26 be?",
    "If we box this lap where do we come out?",
    "Is it worth going to the softs at the end?",
    "Can the mediums last to the end?",
    "Can we go long on this set?",
    "What happens if I stay out?",
])
def test_a_hypothetical_about_the_stop_gets_the_planning_tier(utterance):
    assert EngineerBrain.classify_request(utterance) == "deep"


@pytest.mark.parametrize("utterance", [
    "how much fuel do I need to stay out of trouble with the car behind",
    RAMBLE,
    "I've been happy with the setup so far, what's the gap behind?",
    "what's the gap ahead",
    "are we ok on fuel",
])
def test_a_quick_question_is_not_escalated_by_incidental_words_or_length(utterance):
    assert EngineerBrain.classify_request(utterance) != "deep"


@pytest.mark.parametrize("utterance", [
    "Generate a setup for the rain and what's the gap behind",
    "I want the setup changed, what's the gap behind",
    "what setup should I run in the rain",
    "the strategy and the gap behind",
    "I think we should stay out, what's the gap behind",
    # A long request for a diagnosis keeps the planning tier although it
    # ends in a quick question.
    (
        "why are the rears overheating so badly in the second sector, I have tried a later "
        "apex and a gentler throttle and lifting earlier into the fast corners but nothing "
        "seems to work, and what's the gap behind"
    ),
    "are they undercutting us",
    "compare stints",
])
def test_a_real_planning_request_before_a_lookup_still_gets_the_planning_tier(utterance):
    assert EngineerBrain.classify_request(utterance) == "deep"


@pytest.mark.asyncio
async def test_the_model_receives_the_planning_route_for_a_stop_simulation(stack, monkeypatch):
    _store, brain = await _race(stack)
    calls: list[dict] = []
    _model_reads_the_plan(brain, monkeypatch, calls)
    await brain.ask("How slow would a one-stop to hards on lap 14 be?")
    assert calls and calls[0]["route"] == "deep"
    assert "what_if" in {schema["name"] for schema in calls[0]["tools"]}
