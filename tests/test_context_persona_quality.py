"""What the model is given: the brief for each path, the situation header,
the radio history and the facts behind an unsolicited call.

Each test reproduces a context the engineer used to receive and checks the
version it receives now. The provider is scripted, so these pin the prompt
and instructions, not a model's wording.
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy

import pytest

from pitwall.brain import (
    PERSONA,
    QUESTION_PERSONA,
    SAVED_SESSION_PERSONA,
    SETUP_PERSONA,
    SETUP_TOOL_PERSONA,
    STRATEGY_PERSONA,
    EngineerBrain,
    compose_persona,
    persona_addenda,
)
from pitwall.proactive import ProactiveEngineer, narration_facts
from pitwall.providers import ProviderResult

CONDITION = (
    "The call changes for a safety car, red flag, wet crossover, new damage, "
    "or a hard tyre-wear limit breach."
)
TOOL_NAMES = (
    "get_pace_verdict", "get_race_picture", "get_pit_strategy", "record_lap_observation",
    "get_practice_run_review", "predict_rival_strategy", "generate_setup", "get_strategic_rivals",
)


def _plan(**extra):
    best = {
        "box_lap": 14, "fit_compound": "HARD", "box_laps": [14], "compounds": ["MEDIUM", "HARD"],
        "feasible": True, "legal": True, "instruction": "Box lap 14 for HARD.",
        "call_changes_if": CONDITION, "change_condition": CONDITION, "stops_remaining": 1,
        "risk_adjusted_time_s": 1800.0,
        # The bulk an engine plan carries, none of which a radio call speaks.
        "monte_carlo": {"samples": 320, "p10_s": 1790.123456, "p90_s": 1812.987654},
        "position_probabilities": {"P5": 0.41, "P6": 0.33, "P7": 0.26},
        "stint_models": [{"laps": 8, "expected_time_s": 197.30111515789474}],
        "points_expected": 9.4, "projected_rejoin_position": 16,
        "projected_finish_wear_pct": 48.5, "projected_max_wear_pct": 90.1,
        **extra,
    }
    return {
        "recommended": best,
        "plans": [
            best,
            # Stops before the declined lap: not an answer to "not this lap".
            {**best, "box_lap": 12, "box_laps": [12], "risk_adjusted_time_s": 1801.0},
            # Cannot reach the flag.
            {**best, "box_lap": 16, "box_laps": [16], "feasible": False, "risk_adjusted_time_s": 1801.5},
            {**best, "box_lap": 17, "box_laps": [17], "risk_adjusted_time_s": 1802.1},
        ],
        "confidence": "low",
        "tyre_inventory": {"status": "known"},
        "model_summary": {"selected_stint_wear_source": "track_default",
                          "selected_stint_deg_source": "track_default"},
        "compound_rule": {
            "applies": True, "wet_waiver": False, "used_compounds": ["MEDIUM"],
            "dry_compounds_used": ["HARD", "MEDIUM"], "compliant": True,
            "conditional_on_future_use": True, "change_outstanding": False,
            "eligible_next_compounds": ["SOFT"],
        },
    }


async def _scripted(stack, monkeypatch, reply="Copy.", *, strategy=None, lap=13, **state):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    values = {
        "connected": True, "session_type": "Race", "mode_profile": "race", "current_lap": lap,
        "total_laps": 50, "player_position": 5,
        "strategy": deepcopy(strategy) if strategy is not None else {},
    }
    await store.update(**{**values, **state})
    brain = EngineerBrain(store, tools, database)
    captured: list[dict] = []

    async def generate(**kwargs):
        captured.append(kwargs)
        return ProviderResult(reply, "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)
    return store, brain, captured


async def _age_groups(store, *groups: int, seconds: float = 120.0) -> None:
    def apply(state):
        for group in groups:
            state.packet_group_freshness[str(group)] = time.time() - seconds

    await store.mutate(apply)


# ---------------------------------------------------- the unsolicited call


@pytest.mark.asyncio
async def test_the_narrator_gets_the_call_facts_not_the_queue_record_or_the_whole_plan(stack, monkeypatch):
    store, brain, captured = await _scripted(stack, monkeypatch, "Box lap 14 for hards.", strategy=_plan())
    state = await store.snapshot_analysis()
    queued = {
        "type": "strategy_change", "critical": False, "flash": False, "priority": 1,
        # As the detector queues it: the whole plan, its instruction worded for the radio.
        "payload": ProactiveEngineer._spoken_advice(state["strategy"]["recommended"], state), "queued_at": 1794115563.3856175, "deliver_by": 1794115591.0,
        "expires_at": 1794115743.0, "session_uid": 1234, "session_context": {"track": "Silverstone"},
        "blocked_reasons": ["speed 287, throttle 0.30, lateral 2.10g"],
    }
    await brain.proactive(queued)
    prompt = captured[0]["prompt"]
    event = json.loads(prompt.split("PROACTIVE EVENT: ", 1)[1].splitlines()[0])
    for leaked in (
        "queued_at", "deliver_by", "expires_at", "session_context", "blocked_reasons", "lateral",
        "monte_carlo", "position_probabilities", "stint_models", "points_expected",
        "projected_rejoin_position", "197.301",
    ):
        assert leaked not in prompt, leaked
    assert event["type"] == "strategy_change"
    assert event["facts"]["instruction"] == "Box lap 14 for hards."
    assert event["facts"]["box_lap"] == 14 and event["facts"]["fit_compound"] == "HARD"
    assert event["facts"]["plan_confidence"] == "low"
    assert event["facts"]["call_changes_if"] == CONDITION


def _events() -> list[dict]:
    plan = _plan()["recommended"]
    return [
        {"type": "strategy_change", "payload": {**plan, "hold_released_reason": "rain arrived"}},
        {"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [55.2, 56.1, 71.3, 66.0], "strategy": plan}},
        {"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [55.2, 56.1, 71.3, 66.0], "strategy": {}}},
        {"type": "rival_pitted", "payload": {"driver": "HAMILTON", "position": 6, "tyre": "HARD",
                                             "gap_to_player_s": 1.8, "strategy": plan}},
        {"type": "undercut_threat", "payload": {"driver": "HAMILTON", "position": 6, "tyre": "HARD",
                                                "gap_to_player_s": 1.8, "strategy": plan,
                                                "instruction": "Car behind has stopped. Push."}},
        {"type": "penalty_service", "payload": {"drive_through": 1, "stop_go": 0, "serve_at_stop": False,
                                                "strategy": plan}},
        {"type": "compound_requirement", "payload": {"laps_remaining": 5, "used": ["MEDIUM"],
                                                     "eligible": ["HARD"], "strategy": plan}},
        {"type": "damage", "payload": {"front_left_wing": 34, "front_right_wing": 0, "engine": 0,
                                       "gearbox": 0, "ers_fault": True, "drs_fault": False,
                                       "max_damage_pct": 34, "new_faults": [], "escalated": True,
                                       "repairable": ["front wing"]}},
        {"type": "damage", "payload": {"gearbox": 25, "ers_fault": False, "max_damage_pct": 25,
                                       "new_faults": ["drs_fault"], "drs_fault": True}},
        {"type": "progress_update", "payload": {
            "lap": 12, "mode_profile": "race",
            "progress": {"lap_num": 12, "lap_time": "1:31.200", "delta_to_target_s": 0.215,
                         "top_opportunity": {"instruction": "Brake later into turn 3.", "start_m": 5331.0},
                         "summary": {"timing_fields": {"s1_ms": 30100}}},
            "target": {"target": "1:31.000", "target_ms": 91000, "theoretical": "1:30.700"},
            "racing_line": {"top_opportunity": {"instruction": "Use more kerb at turn 9.", "start_m": 12.0}},
            "strategy": plan, "qualifying": {}, "gaps": {"ahead": {"driver": "NORRIS", "gap_s": 1.4}},
        }},
        {"type": "progress_update", "payload": {
            "lap": 4, "mode_profile": "qualifying", "progress": {}, "strategy": {}, "target": {},
            "racing_line": {}, "gaps": {},
            "qualifying": {"session_best": "1:28.000", "player_best": "1:28.400", "target": "1:28.200",
                           "required_delta_s": 0.2, "top_best_laps": [{"name": "VER", "best_lap": "1:28.000"}]},
        }},
        {"type": "weather_crossover", "payload": {
            "rain_15_pct": 70, "wetness": 0.42, "trend": "rising", "compound": "INTER", "box_lap": 22,
            "worth_stopping": True, "ask_driver": False, "driver_question": None,
            "reason": "Rain is building.", "forecast": [{"time_offset_min": t, "rain_pct": 70, "weather": 3}
                                                       for t in (5, 10, 15, 30, 45, 60)],
        }},
        {"type": "corner_coaching", "payload": {"corner_no": 3, "name": "Turn 3", "average_loss_s": 0.31,
                                                "cause": "early_braking", "instruction": "Brake later into Turn 3."}},
        {"type": "component_wear", "payload": {"component": "gearbox", "wear_pct": 74.0,
                                               "components": [{"component": "gearbox", "wear_pct": 74.0, "band": 70},
                                                              {"component": "ice", "wear_pct": 71.0, "band": 70}],
                                               "all": {"gearbox": 74.0, "ice": 71.0, "mguk": 40.0}}},
        {"type": "pace_roast", "payload": {"lap": 7, "reason": "both", "position": 18, "active_cars": 20,
                                           "bottom_five": True, "delta_to_target_s": 0.912,
                                           "target": "1:31.000", "lap_time": "1:31.912", "tolerance_s": 0.5}},
        {"type": "penalty", "payload": {"penalties_s": 10, "added_s": 5, "total_s": 10}},
        {"type": "race_start", "payload": {"grid_position": 7, "clean_side": True, "ers_pct": 100.0,
                                           "front_temps_c": [80.0, 81.0]}},
        {"type": "driver_check", "payload": {"laps_to_window": 2, "box_lap": 14, "current_wear_pct": 61.0,
                                             "candidate_plans": [{"box_laps": [14], "compounds": ["MEDIUM", "HARD"]}]}},
        {"type": "rival_pace", "payload": {"driver": "NORRIS", "gap_to_player_s": 1.62, "closing": True}},
        {"type": "energy_low", "payload": {"ers_pct": 12.4, "regulations_2026": True}},
        {"type": "fuel_warning", "payload": {"burn_kg_per_lap": 1.612, "fuel_laps_delta": -0.42,
                                             "lift_and_coast_m_per_lap": 20, "recommendation": "save fuel"}},
        {"type": "warning", "payload": {"corner_cutting_warnings": 3}},
        {"type": "engine_failure", "payload": {"blown": False, "seized": True}},
        {"type": "lap_deleted", "payload": {"lap": 5, "sector": 2}},
        {"type": "quali_clear_air", "payload": {"lap": 3, "clear_air_estimate": True, "closest_gap_s": None,
                                                "target": {"target": "1:28.200", "session_best": "1:28.000"},
                                                "ers_pct": 100.0, "tyre_temps": [90, 91, 88, 89]}},
    ]


@pytest.mark.parametrize("event", _events(), ids=lambda event: event["type"])
def test_the_narration_facts_keep_everything_the_template_says(event):
    """The template spoken from the facts is the template spoken from the payload."""
    state = {"strategy": _plan(), "fuel_laps_delta": -0.4, "regulations_2026": True}
    projected = narration_facts(event, state)
    reduced = {"type": event["type"], "payload": projected["facts"]}
    assert ProactiveEngineer._fallback_text(reduced, state) == ProactiveEngineer._fallback_text(event, state)


def test_the_narration_facts_drop_the_bulk_and_undamaged_parts():
    state = {"strategy": _plan()}
    events = {event["type"]: event for event in _events()}
    rival = narration_facts(events["rival_pitted"], state)["facts"]
    assert set(rival["strategy"]) <= {
        "instruction", "box_lap", "fit_compound", "stops_remaining", "stop_required_reason",
        "tyre_reason", "call_changes_if", "plan_confidence",
    }
    damage = narration_facts(_events()[7], state)["facts"]
    assert "front_right_wing" not in damage and "drs_fault" not in damage
    assert damage["front_left_wing"] == 34 and damage["ers_fault"] is True
    weather = narration_facts(events["weather_crossover"], state)["facts"]
    assert len(weather["forecast"]) == 3 and "weather" not in weather["forecast"][0]
    progress = narration_facts(_events()[9], state)["facts"]
    assert progress["progress"]["top_opportunity"] == "Brake later into turn 3."
    assert "summary" not in progress["progress"]
    assert "all" not in narration_facts(events["component_wear"], state)["facts"]


@pytest.mark.asyncio
async def test_a_volunteered_rival_stop_call_carries_the_plan_confidence_and_qualifier(stack, monkeypatch):
    """The same low-confidence plan used to be qualified when asked and bare when volunteered."""
    store, brain, captured = await _scripted(
        stack, monkeypatch, "Hamilton has gone for hards. We stay on box lap 14 for hards.", strategy=_plan(),
    )
    plan = (await store.snapshot_analysis())["strategy"]["recommended"]
    spoken = await brain.proactive({"type": "rival_pitted", "payload": {
        "driver": "HAMILTON", "position": 6, "tyre": "HARD", "gap_to_player_s": 1.8, "strategy": plan,
    }})
    assert "plan confidence low" in captured[0]["prompt"]
    assert spoken.endswith("box lap 14 for hards; provisional, confidence low, tyre estimates include track defaults.")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,payload,reply,spoken", [
    # The penalty is a fact, not a provisional projection.
    ("penalty_service", {"drive_through": 0, "stop_go": 1, "serve_at_stop": False},
     "Stop-go penalty outstanding. Serve it within the game deadline.",
     "Stop-go penalty outstanding. Serve it within the game deadline."),
    ("penalty_service", {"drive_through": 1, "stop_go": 0, "serve_at_stop": False},
     "Drive-through penalty outstanding. Do not combine it with the tyre stop.",
     "Drive-through penalty outstanding. Do not combine it with the tyre stop."),
    # The rival's stop is his; the plan's own call is the one qualified.
    ("rival_pitted", {"driver": "HAMILTON", "position": 6, "tyre": "HARD", "gap_to_player_s": 1.8},
     "Hamilton has made his stop for hards. We box lap 14 for hards.",
     "Hamilton has made his stop for hards. We box lap 14 for hards; provisional, confidence low, "
     "tyre estimates include track defaults."),
    # Coaching that says "stop" is not a pit call.
    ("progress_update", {"lap": 12, "mode_profile": "race", "progress": {}, "target": {"target": "1:31.000"},
                         "racing_line": {}, "qualifying": {}, "gaps": {}},
     "Lap 12, on target. Stop braking so early into turn 3.",
     "Lap 12, on target. Stop braking so early into turn 3."),
    # A plan the stop guard turned into "stay out" is restated as staying out.
    ("rival_pitted", {"driver": "HAMILTON", "position": 6, "tyre": "HARD", "gap_to_player_s": 1.8,
                      "instruction_override": "Stay out — boxing costs you 6 places and gains nothing."},
     "Hamilton has made his stop. We stay out, a stop costs six places.",
     "Hamilton has made his stop. We stay out, a stop costs six places; provisional, confidence low, "
     "tyre estimates include track defaults."),
])
async def test_a_plan_that_rides_along_qualifies_only_the_plans_own_call(stack, monkeypatch, kind, payload, reply, spoken):
    store, brain, captured = await _scripted(stack, monkeypatch, reply, strategy=_plan(), lap=12)
    plan = dict((await store.snapshot_analysis())["strategy"]["recommended"])
    payload = dict(payload)
    if "instruction_override" in payload:
        # As _spoken_advice queues it: the plan with the radio's wording.
        plan["instruction"] = payload.pop("instruction_override")
    assert await brain.proactive({"type": kind, "payload": {**payload, "strategy": plan}}) == spoken
    # The confidence is still in front of the narrator.
    assert "plan confidence low" in captured[0]["prompt"]


@pytest.mark.asyncio
async def test_the_narration_brief_orders_no_tools_and_says_so(stack, monkeypatch):
    _, brain, captured = await _scripted(stack, monkeypatch, "Rears at seventy.", strategy=_plan())
    await brain.proactive({"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [55, 56, 70, 66]}})
    instructions = captured[0]["instructions"]
    assert captured[0]["tools"] == []
    for name in TOOL_NAMES:
        assert name not in instructions, name
    assert "No tools are available for this call" in instructions
    assert "the driver's latest request" not in instructions
    assert "For a strategy request, inspect the ranked alternatives" not in instructions
    # The pit-call and safety rules every call needs are still there.
    assert "Pit calls must specify BOTH the lap and the tyre compound" in instructions
    assert "only pit-stop repair this telemetry exposes is the front wing" in instructions


@pytest.mark.asyncio
async def test_a_briefing_brief_orders_no_tools(stack, monkeypatch):
    _, brain, captured = await _scripted(stack, monkeypatch, "Brief.")
    await brain.narrate_briefing("post_race", {"kind": "post_race"})
    instructions = captured[0]["instructions"]
    for name in TOOL_NAMES:
        assert name not in instructions, name
    assert "provenance label" not in instructions
    assert "Never invent a number" in instructions


# ---------------------------------------------------------- reply briefs


def test_each_reply_brief_is_attached_where_it_applies():
    race = {"mode_profile": "race"}
    plain = persona_addenda(race, "what's the gap to Norris", {"get_gap", "record_lap_observation"})
    assert plain == (QUESTION_PERSONA,)
    assert STRATEGY_PERSONA in persona_addenda(race, "can I make it to the end on these tyres", set())
    assert STRATEGY_PERSONA in persona_addenda(race, "copy", set(), strategy=True)
    assert STRATEGY_PERSONA in persona_addenda({"red_flag_active": True}, "what now", set())
    assert SETUP_PERSONA in persona_addenda(race, "should I add front wing", set())
    assert SAVED_SESSION_PERSONA not in persona_addenda(race, "review my saved session", {"get_gap"})
    assert SAVED_SESSION_PERSONA in persona_addenda(race, "anything", {"compare_laps"})
    # Fixed order: the same path always opens with the same text.
    deep = persona_addenda(race, "talk me through the strategy", {"compare_laps", "generate_setup"})
    assert deep == (QUESTION_PERSONA, STRATEGY_PERSONA, SETUP_PERSONA, SETUP_TOOL_PERSONA, SAVED_SESSION_PERSONA)
    composed = compose_persona(PERSONA, "standard", route="deep", addenda=deep)
    assert composed.startswith(PERSONA + "\n\n" + QUESTION_PERSONA)


_FIELD = {
    "mode_profile": "race", "player_car_index": 0,
    "drivers": [
        {"car_idx": 0, "name": "PLAYER", "active": True},
        {"car_idx": 1, "name": "NORRIS", "active": True},
        {"car_idx": 2, "name": "VERSTAPPEN", "active": True},
    ],
}


@pytest.mark.parametrize("utterance", [
    "Is Norris on hards?", "Has Norris made his stop yet?", "Is he on mediums?",
    "What tyres is the car ahead on?",
])
def test_a_question_about_a_rivals_stop_keeps_the_rival_rules(utterance):
    """'Is Norris on hards?' was given the brief for the driver's own plan."""
    assert STRATEGY_PERSONA not in persona_addenda(_FIELD, utterance, set())


@pytest.mark.parametrize("utterance", [
    "Norris is on hards, should we go to hards too?", "can I make it to the end on these tyres",
    "Has Norris stopped? When do we box?",
])
def test_a_question_about_the_drivers_own_stop_carries_the_strategy_brief(utterance):
    assert STRATEGY_PERSONA in persona_addenda(_FIELD, utterance, set())


@pytest.mark.asyncio
async def test_the_plan_qualifier_lands_on_the_drivers_call_not_a_rivals_stop(stack, monkeypatch):
    """'Norris made his stop two laps ago; provisional, confidence low' made a
    rival's stop sound provisional and left the actual call unqualified."""
    store, brain, captured = await _scripted(
        stack, monkeypatch, "Norris made his stop two laps ago. Box lap 14 for hards.", strategy=_plan(),
    )

    def field(state):
        state.player_car_index = 0
        for index, name in enumerate(("PLAYER", "NORRIS")):
            state.drivers[index].name = name
            state.drivers[index].active = True

    await store.mutate(field)
    heard = await brain.ask("Norris has stopped, talk me through our strategy and the alternatives")
    assert captured, "the model answers"
    assert heard.startswith("Norris made his stop two laps ago. Box lap 14 for hards; provisional")


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    "Undercut Norris with a stop on lap 14.",
    "Lap 14 for hards to cover Norris.",
    "Norris pitted, so lap 14 for hards.",
])
async def test_a_call_made_to_answer_a_rival_keeps_its_qualifier_and_survives_the_cut(stack, monkeypatch, call):
    """A call that names the rival it answers is still the driver's call: it
    lost the plan's low-confidence qualifier, and the radio cut dropped it."""
    reply = f"Norris made his stop two laps ago. He is on hards. He rejoined in P6. {call}"
    store, brain, captured = await _scripted(stack, monkeypatch, reply, strategy=_plan())

    def field(state):
        state.player_car_index = 0
        for index, name in enumerate(("PLAYER", "NORRIS")):
            state.drivers[index].name = name
            state.drivers[index].active = True

    await store.mutate(field)
    heard = await brain.ask("Norris has stopped, what's our strategy now?")
    assert captured, "the model answers"
    assert call.rstrip(".") + "; provisional, confidence low" in heard
    assert "two laps ago; provisional" not in heard

    async def compare(**_kwargs):
        return {"results": {"test": {"reply": reply}}}

    brain.router.compare = compare
    # A comparison shows each reply as the radio would speak it.
    compared = await brain.compare("Norris has stopped, what's our strategy now?")
    assert compared["results"]["test"]["as_spoken"] == heard


@pytest.mark.parametrize("utterance", [
    "they're gone", "the rears are going away", "no grip left", "holding", "plenty left in these",
    "I'm losing the rears",
])
def test_the_drivers_tyre_report_carries_the_rule_on_feedback_against_the_model(utterance):
    """The driver's read of the tyres rarely names a tyre, and it is exactly
    what the rule on feedback against the model is for."""
    addenda = persona_addenda({"mode_profile": "race"}, utterance, {"get_pit_strategy"})
    assert STRATEGY_PERSONA in addenda
    assert "Driver tyre feedback is evidence, not telemetry" in STRATEGY_PERSONA


def _ordered_tools(instructions: str, stack) -> set[str]:
    """Every tool of the telemetry set that the brief names."""
    names = {schema["name"] for schema in stack[5].schemas()}
    return {name for name in names if re.search(rf"\b{name}\b", instructions)}


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "What front wing should I run for the second stint?",
    "Should I lower the tyre pressures, the rears are overheating?",
    "Log that last lap, I got held up by traffic in turn 7.",
])
async def test_a_brief_names_only_tools_its_request_offers(stack, monkeypatch, utterance):
    """The setup brief told a normal-route reply to "Use generate_setup", which
    only the deep route offers; the practice-notes brief did the same with
    record_lap_observation on the comparison, which withholds it."""
    _store, brain, captured = await _scripted(stack, monkeypatch, mode_profile="practice", session_type="Practice 2")
    await brain.ask(utterance)
    offered = {tool["name"] for tool in captured[-1]["tools"]}
    assert _ordered_tools(captured[-1]["instructions"], stack) <= offered
    received: dict = {}

    async def compare(**kwargs):
        received.update(kwargs)
        return {"results": {}}

    brain.router.compare = compare
    await brain.compare(utterance)
    offered = {tool["name"] for tool in received["tools"]}
    assert "record_lap_observation" not in offered
    assert _ordered_tools(received["instructions"], stack) <= offered


def test_the_notes_brief_saves_a_note_only_where_it_can():
    practice = {"mode_profile": "practice"}
    notes = {"get_practice_run_review", "get_lap_observations", "compare_practice_groups"}
    without = " ".join(persona_addenda(practice, "log that lap, traffic", notes))
    assert "record_lap_observation" not in without
    assert "say the note was not saved" in without
    offered = " ".join(persona_addenda(practice, "log that lap, traffic", notes | {"record_lap_observation"}))
    assert "record it with record_lap_observation" in offered
    assert "say the note was not saved" not in offered


@pytest.mark.asyncio
async def test_a_live_question_does_not_carry_the_saved_session_contract(stack, monkeypatch):
    _, brain, captured = await _scripted(stack, monkeypatch)
    await brain.ask("how am I doing against Norris")
    assert captured[0]["route"] != "deep"
    instructions = captured[0]["instructions"]
    assert "get_pace_verdict" in instructions
    assert "provenance label" not in instructions
    assert "Use generate_setup with basis reference" not in instructions


@pytest.mark.asyncio
async def test_a_deep_question_carries_the_contracts_its_tools_need(stack, monkeypatch):
    _, brain, captured = await _scripted(stack, monkeypatch, strategy=_plan())
    await brain.ask("talk me through the strategy and the best alternative")
    assert captured[0]["route"] == "deep"
    instructions = captured[0]["instructions"]
    assert "Every value carries a provenance label" in instructions
    assert "For a strategy request, inspect the ranked alternatives" in instructions
    assert "Use generate_setup with basis reference" in instructions


# ---------------------------------------------------------- radio history


@pytest.mark.asyncio
async def test_a_driver_question_that_opens_with_hold_on_stays_in_the_history(stack, monkeypatch):
    _, brain, captured = await _scripted(stack, monkeypatch)
    await brain.ask("Hold on, are we still going to the end on these mediums or not?")
    await brain.ask("The question is, can Norris undercut us if we wait two more laps?")
    await brain.ask("well, answer my question")
    history = captured[-1]["prompt"].split("RECENT RADIO:\n", 1)[1]
    assert "DRIVER: Hold on, are we still going to the end on these mediums or not?" in history
    assert "DRIVER: The question is, can Norris undercut us" in history


@pytest.mark.asyncio
async def test_leaked_engineer_deliberation_is_still_left_out(stack, monkeypatch):
    store, brain, captured = await _scripted(stack, monkeypatch)
    await store.append_radio("engineer", "Let me check the gap first. Key facts: Norris 1.2 behind.")
    await brain.ask("how far back is Norris now")
    assert "Let me check" not in captured[-1]["prompt"]


@pytest.mark.asyncio
async def test_history_lines_carry_their_lap_and_mark_old_and_unsolicited_calls(stack, monkeypatch):
    store, brain, captured = await _scripted(stack, monkeypatch, lap=10)
    await brain.record_spoken_call("Box this lap for hards, box this lap.")
    await brain.ask("copy, what tyres is Norris on")
    await store.update(current_lap=23)
    await brain.ask("is he still closing?")
    history = captured[-1]["prompt"].split("RECENT RADIO:\n", 1)[1]
    assert "L10 ENGINEER (unsolicited) [old]: Box this lap for hards, box this lap." in history
    assert "L10 DRIVER [old]: copy, what tyres is Norris on" in history
    assert "[old] marks a line two or more laps old" in captured[-1]["prompt"]


@pytest.mark.asyncio
async def test_radio_lines_record_their_lap_and_source(stack, monkeypatch):
    store, brain, _ = await _scripted(stack, monkeypatch, lap=9)
    await brain.record_spoken_call("Fuel is plus 0.4 laps.")
    await brain.ask("copy, how far back is Norris now")
    log = (await store.snapshot_analysis())["radio_log"]
    assert log[0] == {"role": "engineer", "text": "Fuel is plus 0.4 laps.", "lap": 9, "source": "proactive"}
    assert log[1] == {"role": "driver", "text": "copy, how far back is Norris now", "lap": 9}
    assert log[2]["source"] == "reply" and log[2]["lap"] == 9


@pytest.mark.asyncio
async def test_the_narrator_sees_how_old_each_recent_call_is(stack, monkeypatch):
    store, brain, captured = await _scripted(stack, monkeypatch, "Rears at seventy.", lap=10)
    await brain.record_spoken_call("Box this lap for hards.")
    await store.update(current_lap=14)
    await brain.proactive({"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [55, 56, 70, 66]}})
    recent = captured[0]["prompt"].split("RECENT ENGINEER CALLS: ", 1)[1].splitlines()[0]
    assert json.loads(recent) == ["L10 ENGINEER (unsolicited) [old]: Box this lap for hards."]


@pytest.mark.asyncio
async def test_the_comparison_keeps_driver_turns_and_labels_them(stack, monkeypatch):
    store, brain, _ = await _scripted(stack, monkeypatch)
    captured: list[dict] = []

    async def compare(**kwargs):
        captured.append(kwargs)
        return {}

    monkeypatch.setattr(brain.router, "compare", compare)
    await store.append_radio("driver", "Hold on, is Norris closing?")
    await brain.compare("well?")
    assert "L13 DRIVER: Hold on, is Norris closing?" in captured[0]["prompt"]


# ---------------------------------------------------------- race header


@pytest.mark.asyncio
async def test_stale_timing_and_status_are_not_printed_as_current(stack, monkeypatch):
    store, brain, _ = await _scripted(stack, monkeypatch)
    await _age_groups(store, 2, 7)
    header = await brain.situation_header(include_strategy=False)
    assert "| P5 |" not in header
    assert "position unknown (last confirmed P5, stale)" in header
    assert "LAP unknown (last confirmed 13/50, stale)" in header
    assert "FIA flag unknown (last confirmed none, stale)" in header
    assert "penalties unknown (last confirmed 0s, drive-through 0, stop-go 0, stale)" in header
    # A group that never arrived is unknown, with nothing to confirm.
    await store.mutate(lambda state: state.packet_group_freshness.pop("2"))
    header = await brain.situation_header(include_strategy=False)
    assert "| position unknown |" in header and "| penalties unknown |" in header


@pytest.mark.asyncio
async def test_live_timing_is_printed_as_it_was(stack, monkeypatch):
    _, brain, _ = await _scripted(stack, monkeypatch)
    header = await brain.situation_header(include_strategy=False)
    assert "| LAP 13/50 | P5 |" in header
    assert "FIA flag none | penalties 0s, drive-through 0, stop-go 0 |" in header


@pytest.mark.asyncio
async def test_the_compound_rule_is_one_sentence_about_what_has_been_raced(stack, monkeypatch):
    _, brain, _ = await _scripted(stack, monkeypatch, strategy=_plan())
    header = await brain.situation_header(include_strategy=True)
    assert "compound rule: raced MEDIUM only; met only if the planned HARD stop is made" in header
    assert "{'applies'" not in header and "'compliant': True" not in header


@pytest.mark.parametrize("rule,sentence", [
    ({"applies": True, "used_compounds": ["MEDIUM", "HARD"], "dry_compounds_used": ["HARD", "MEDIUM"],
      "compliant": True}, "met, raced MEDIUM and HARD"),
    ({"applies": True, "used_compounds": ["MEDIUM"], "dry_compounds_used": ["MEDIUM"], "compliant": False,
      "eligible_next_compounds": ["HARD", "SOFT"]},
     "raced MEDIUM only; a different dry compound (HARD or SOFT) must still be fitted"),
    ({"applies": False, "wet_waiver": True, "used_compounds": ["MEDIUM", "INTER"]},
     "waived, inters or wets have been used"),
    ({"applies": False, "wet_waiver": True, "used_compounds": ["MEDIUM"]},
     "raced MEDIUM only; waived only if the planned inters or wets are fitted"),
    ({"applies": False, "wet_waiver": False, "used_compounds": ["SOFT"]}, "does not apply to this session"),
    ({}, "not computed yet"),
])
def test_every_compound_rule_state_reads_as_a_sentence(rule, sentence):
    assert EngineerBrain._compound_rule_sentence({"compound_rule": rule}) == sentence


@pytest.mark.asyncio
async def test_the_header_carries_the_guarded_call_not_the_engine_string(stack, monkeypatch):
    plan = _plan(positions_gained_vs_stay_out=0, positions_lost_by_stopping=6, stop_required_reason="pace")
    _, brain, _ = await _scripted(stack, monkeypatch, strategy=plan)
    header = await brain.situation_header(include_strategy=True)
    assert "| strategy Stay out — boxing costs you 6 places and gains nothing." in header
    assert "Box lap 14 for HARD" not in header


@pytest.mark.asyncio
async def test_a_declined_call_travels_in_the_header(stack, monkeypatch):
    store, brain, _ = await _scripted(stack, monkeypatch, strategy=_plan())
    await store.update(strategy_hold={"active": True, "until_lap": 18, "set_at_lap": 13, "reason": "not boxing"})
    header = await brain.situation_header(include_strategy=False)
    assert "STRATEGY HOLD until lap 18: the driver declined the pit call on lap 13" in header


@pytest.mark.asyncio
async def test_a_hold_past_its_last_lap_is_not_stated_as_running(stack, monkeypatch):
    """The hold is released by the unsolicited-call loop; with those calls
    switched off its flag stays set, and the replies must not keep honouring it."""
    store, brain, _ = await _scripted(stack, monkeypatch, strategy=_plan(), lap=31)
    await store.update(strategy_hold={"active": True, "until_lap": 18, "set_at_lap": 13, "reason": "not boxing"})
    assert "STRATEGY HOLD" not in await brain.situation_header(include_strategy=True)


@pytest.mark.asyncio
async def test_the_race_header_prints_no_none(stack, monkeypatch):
    _, brain, _ = await _scripted(stack, monkeypatch, weather_forecast=[])
    header = await brain.situation_header(include_strategy=True)
    assert "None" not in header
    assert "target lap not set" in header
    assert "unknown%" not in header


# ---------------------------------------------------------- qualifying header


async def _qualifying(stack, monkeypatch, *, session_id=5, cars=20, timed=True):
    store, brain, _ = await _scripted(
        stack, monkeypatch, mode_profile="qualifying", session_type="Qualifying 1",
        raw_session_type_id=session_id, active_cars=cars, session_time_left_s=140,
        player_position=17 if timed else 0, player_car_index=0,
    )

    def field(state):
        for index, driver in enumerate(state.drivers[:cars]):
            driver.car_idx = index
            driver.active = True
            driver.name = f"CAR{index}"
            # The player (car 0) is 17th of 20 on 1:29.920; P15 is 1:29.680.
            driver.best_lap_ms = 0 if not timed else (89_920 if index == 0 else 88_000 + 120 * (index - 1 if index < 17 else index))

    await store.mutate(field)
    return store, brain


@pytest.mark.asyncio
async def test_the_qualifying_header_says_position_clock_and_knockout_line(stack, monkeypatch):
    _, brain = await _qualifying(stack, monkeypatch)
    header = await brain.situation_header()
    assert "TIMING POSITION P17/20" in header
    assert "TIME LEFT 2:20" in header
    assert "KNOCKOUT LINE P15 1:29.680 (you are 0.240s slower)" in header


@pytest.mark.asyncio
async def test_the_qualifying_header_prints_no_none_before_a_lap_is_set(stack, monkeypatch):
    _, brain = await _qualifying(stack, monkeypatch, timed=False)
    header = await brain.situation_header()
    assert "None" not in header and "Nones" not in header
    assert "PLAYER BEST not set yet" in header and "REQUIRED DELTA not set yet" in header
    assert "KNOCKOUT LINE P15, no times set yet" in header


@pytest.mark.asyncio
@pytest.mark.parametrize("session_id,cars", [(7, 20), (5, 18), (8, 20)])
async def test_no_knockout_line_is_guessed(stack, monkeypatch, session_id, cars):
    """Q3, an unusual field size and short qualifying have no line to state."""
    _, brain = await _qualifying(stack, monkeypatch, session_id=session_id, cars=cars)
    assert "KNOCKOUT LINE" not in await brain.situation_header()


@pytest.mark.asyncio
async def test_a_22_car_first_knockout_cuts_after_sixteen(stack, monkeypatch):
    _, brain = await _qualifying(stack, monkeypatch, cars=22)
    assert "KNOCKOUT LINE P16 " in await brain.situation_header()


# ---------------------------------------------------------- a declined call


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", [
    "No, I'm not boxing this lap",
    "I'm staying out",
    "I don't want to pit, the tyres feel fine",
])
async def test_a_declined_call_reaches_the_model_with_the_plan_and_the_next_option(stack, monkeypatch, utterance):
    store, brain, captured = await _scripted(
        stack, monkeypatch, "Copy, staying out. Next option is box lap 17 for hards, about 2.1 seconds slower.",
        strategy=_plan(),
    )
    await store.append_radio("engineer", "Box lap 14 for hards.")
    spoken = await brain.ask(utterance)
    assert (await store.snapshot_analysis())["strategy_hold"]["active"]
    prompt, instructions = captured[0]["prompt"], captured[0]["instructions"]
    assert "| strategy Box lap 14 for hards." in prompt
    assert 'DECLINED PIT CALL: the driver declined "Box lap 14 for hards". NEXT FEASIBLE OPTION: box lap 17 for hards, about 2.1 seconds slower' in prompt
    # Lap 12 stops before the declined lap and lap 16 cannot reach the flag.
    assert "lap 12" not in prompt.split("DECLINED PIT CALL", 1)[1].split("REQUEST FOCUS", 1)[0]
    assert "accept it and confirm briefly" not in prompt
    assert "When the driver declines a pit call, never leave it at \"copy\"" in instructions
    assert spoken.startswith("Copy, staying out. Next option is box lap 17 for hards")


@pytest.mark.asyncio
async def test_a_declined_call_with_no_other_plan_is_not_given_an_invented_one(stack, monkeypatch):
    plan = _plan()
    plan["plans"] = plan["plans"][:3]
    _, brain, captured = await _scripted(stack, monkeypatch, strategy=plan)
    await brain.ask("No, I'm not boxing this lap")
    prompt = captured[0]["prompt"]
    assert "No other feasible, legal plan is computed" in prompt
    assert "do not invent an alternative" in prompt


@pytest.mark.asyncio
async def test_a_correction_that_is_not_a_declined_call_is_still_just_accepted(stack, monkeypatch):
    _, brain, captured = await _scripted(stack, monkeypatch, strategy=_plan())
    await brain.ask("No, I'm talking about Max's car, not mine")
    prompt = captured[0]["prompt"]
    assert "DECLINED PIT CALL" not in prompt
    assert "If the driver is correcting or refusing something, accept it and confirm briefly." in prompt
