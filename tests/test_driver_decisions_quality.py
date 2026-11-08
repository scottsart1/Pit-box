"""The radio keeps to what the driver decided, end to end.

Each test drives the real EngineerBrain.ask() with a scripted provider and
reproduces a turn the driver used to get wrong: a pit question one breath
after declining the stop, a refusal answered with the call it refused when
the provider failed, a qualifying pit question with no race plan behind it,
a unit question that changed the unit, and the speech session's brief and
pit call drifting from what push-to-talk says.
"""

from __future__ import annotations

import time
from copy import deepcopy

import pytest

from pitwall.brain import (
    SETUP_PERSONA,
    STRATEGY_PERSONA,
    EngineerBrain,
    _closed_tyre_answer,
    _limit_radio_sentences,
    _pit_call_index,
    persona_addenda,
)
from pitwall.proactive import narration_facts
from pitwall.providers import ProviderFailure, ProviderRequestError, ProviderResult
from pitwall.realtime import REALTIME_PERSONA

CONDITION = (
    "The call changes for a safety car, red flag, wet crossover, new damage, "
    "or the tyres reaching their wear limit."
)


def _plan(**extra):
    best = {
        "box_lap": 14, "fit_compound": "HARD", "box_laps": [14], "compounds": ["MEDIUM", "HARD"],
        "feasible": True, "legal": True, "instruction": "Box lap 14 for HARD.",
        "call_changes_if": CONDITION, "change_condition": CONDITION, "stops_remaining": 1,
        "risk_adjusted_time_s": 1800.0,
        **extra,
    }
    return {
        "recommended": best,
        "plans": [
            best,
            {**best, "box_lap": 12, "box_laps": [12], "risk_adjusted_time_s": 1801.0},
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


def _timeout() -> ProviderRequestError:
    return ProviderRequestError([ProviderFailure("openai", "timeout", "timeout failure")])


async def _race(stack, monkeypatch, replies=("Copy.",), *, strategy=None, lap=13, **state):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    values = {
        "connected": True, "session_type": "Race", "mode_profile": "race", "current_lap": lap,
        "total_laps": 50, "player_position": 5, "fuel_laps_delta": 0.8,
        "strategy": deepcopy(strategy) if strategy is not None else _plan(),
    }
    await store.update(**{**values, **state})
    brain = EngineerBrain(store, tools, database)
    captured: list[dict] = []
    queue = list(replies)

    async def generate(**kwargs):
        captured.append(kwargs)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, BaseException):
            raise reply
        return ProviderResult(reply, "test", "test", 1)

    monkeypatch.setattr(brain.router, "generate", generate)
    return store, brain, captured


# ------------------------------------------------- a decision stays decided


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Okay, so when do we box then?", "should we box", "what's the plan", "is it time to pit",
])
async def test_a_pit_question_after_a_declined_stop_never_gets_the_declined_call(stack, monkeypatch, question):
    _store, brain, captured = await _race(
        stack, monkeypatch, ["Copy, staying out. Next option is box lap 17 for hards.", "Holding; next option is box lap 17."],
    )
    await brain.ask("I'm not boxing")
    heard = await brain.ask(question)
    assert len(captured) == 2, "the pit question goes to the model, which has the hold"
    assert "lap 14" not in heard
    prompt = captured[1]["prompt"]
    assert "STRATEGY HOLD until lap 18" in prompt
    assert 'PLAN DURING THE HOLD (until lap 18): the driver declined "Box lap 14 for hards"' in prompt
    assert "give the next option, box lap 17 for hards, about 2.1 seconds slower" in prompt


@pytest.mark.asyncio
async def test_an_agreed_tactic_keeps_the_pit_question_off_the_standing_plan(stack, monkeypatch):
    _store, brain, captured = await _race(stack, monkeypatch, ["Copy, we go long.", "We go long; lap 17 earliest."])
    await brain.ask("We'll go long, I'm staying out")
    heard = await brain.ask("when do we box")
    assert len(captured) == 2
    assert not heard.startswith("Box lap 14")
    assert "AGREED WITH THE DRIVER" in captured[1]["prompt"]


@pytest.mark.asyncio
async def test_an_agreed_tactic_is_spent_once_the_stop_it_was_about_is_made(stack, monkeypatch):
    store, brain, captured = await _race(stack, monkeypatch, ["Copy, we go long."])
    await store.mutate(lambda state: setattr(state.tyre, "age_laps", 13))
    await brain.ask("We'll go long, I'm staying out")
    # The stop is made on lap 22; on lap 35 the two-stop plan calls lap 38.
    second = _plan(box_lap=38, box_laps=[38], compounds=["HARD", "MEDIUM"], fit_compound="MEDIUM",
                   instruction="Box lap 38 for MEDIUM.")

    def after_the_stop(state):
        state.current_lap = 35
        state.tyre.compound = "HARD"
        state.tyre.age_laps = 13
        state.strategy = second
        state.strategy_hold = {**state.strategy_hold, "active": False}

    await store.mutate(after_the_stop)
    heard = await brain.ask("should I box")
    assert len(captured) == 1, "the plan answers; the go-long agreement was about the stop already made"
    assert heard.startswith("Box lap 38 for mediums")
    assert "AGREED WITH THE DRIVER" not in await brain.situation_header()


@pytest.mark.asyncio
async def test_the_dashboard_is_told_when_an_agreed_tactic_is_spent(stack, monkeypatch):
    """The strategy page kept headlining "Running the go long" after the stop."""
    store, brain, _captured = await _race(stack, monkeypatch, ["Copy, we go long."])
    await store.mutate(lambda state: setattr(state.tyre, "age_laps", 13))
    await brain.ask("We'll go long, I'm staying out")
    assert (await store.snapshot_live())["strategy_intent_stands"] is True

    def after_the_stop(state):
        state.current_lap = 35
        state.tyre.age_laps = 13

    await store.mutate(after_the_stop)
    live = await store.snapshot_live()
    assert live["strategy_intent"]["active"]
    assert live["strategy_intent_stands"] is False
    assert EngineerBrain._agreed_tactic_stands(live) is False


@pytest.mark.asyncio
async def test_a_hold_ends_at_the_stop_with_unsolicited_calls_switched_off(stack, monkeypatch):
    """Only the unsolicited loop released a hold; switched off, it bound the
    radio after the stop it was about had been made."""
    store, brain, captured = await _race(stack, monkeypatch, ["Copy, staying out."])
    await store.mutate(lambda state: state.proactive.update({"enabled": False}))
    await brain.ask("I'm not boxing")
    assert "STRATEGY HOLD" in await brain.situation_header()
    # The driver boxes for hards a lap later after all.
    after = _plan(box_lap=None, box_laps=[], compounds=["HARD"], fit_compound="HARD",
                  stops_remaining=0, instruction="Stay out to the flag on HARD.")
    after["plans"] = [after["recommended"]]

    def stopped(state):
        state.current_lap = 14
        state.tyre.compound = "HARD"
        state.tyre.age_laps = 0
        state.strategy = after

    await store.mutate(stopped)
    heard = await brain.ask("what's the plan")
    assert len(captured) == 1, "the plan answers; the hold was about the stop now made"
    assert heard.startswith("Stay out to the flag on HARD")
    assert "STRATEGY HOLD" not in await brain.situation_header()


@pytest.mark.asyncio
async def test_a_red_flag_ends_the_hold_for_good_with_unsolicited_calls_switched_off(stack, monkeypatch):
    from pitwall.proactive import ProactiveEngineer

    class _Quiet:
        is_busy = False
        busy_reason = ""
        realtime_active = False

    store, brain, captured = await _race(stack, monkeypatch, ["Copy, staying out.", "Restart on hards."])
    await store.mutate(lambda state: state.proactive.update({"enabled": False}))
    await brain.ask("I'm not boxing")
    engineer = ProactiveEngineer(store, brain, _Quiet(), None, None)  # type: ignore[arg-type]
    await store.update(race_control_phase="red_flag", red_flag_active=True)
    await brain.ask("what tyres for the restart?")
    assert "STRATEGY HOLD" not in captured[-1]["prompt"]
    assert "PLAN DURING THE HOLD" not in captured[-1]["prompt"]
    await engineer._detect(await store.snapshot_radio())
    # Back to green inside the hold's laps: the suspension still ended it.
    await store.update(race_control_phase="green", red_flag_active=False)
    await engineer._detect(await store.snapshot_radio())
    hold = (await store.snapshot_analysis())["strategy_hold"]
    assert not hold["active"]
    assert hold["change_reason"] == "race control changed to red flag"
    assert "STRATEGY HOLD" not in await brain.situation_header()
    assert not engineer.pending


@pytest.mark.asyncio
async def test_an_agreed_overcut_is_not_released_on_the_next_tick(stack, monkeypatch):
    """The agreement's hold had no baseline, which read as "the fitted tyre
    changed", so the pit call came straight back."""
    from pitwall.proactive import ProactiveEngineer

    store, brain, _captured = await _race(stack, monkeypatch, ["Copy, we'll run the overcut."])
    await store.mutate(lambda state: setattr(state.tyre, "compound", "MEDIUM"))
    await brain.ask("Let's do the overcut")
    engineer = ProactiveEngineer(store, brain, None, None, None)  # type: ignore[arg-type]
    _state, released = await engineer._evaluate_strategy_hold(await store.snapshot_radio())
    assert released is None
    assert EngineerBrain._hold_running(await store.snapshot_analysis())


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [
    "I'm not boxing", "I'm not boxing, stay out", "No, stay out", "Negative, we stay out this lap",
    "Not pitting, I'll stay out on these",
])
async def test_a_refusal_gets_the_same_route_whatever_words_it_uses(stack, monkeypatch, refusal):
    """"Stay out" sent a refusal to the planning tier (main model, high effort,
    25 s deadline) while "I'm not boxing" went on the normal one."""
    from pitwall.brain import _driver_reaction

    _store, brain, captured = await _race(
        stack, monkeypatch, ["Copy, staying out. Next option is box lap 17 for hards."]
    )
    await brain.ask(refusal)
    assert captured[0]["route"] == "normal"
    assert "DECLINED PIT CALL" in captured[0]["prompt"]
    # A plain decision is not a dispute, and the labeller has no label for it.
    assert _driver_reaction(refusal) == ""


@pytest.mark.parametrize("proposal", ["I think we should stay out, what's the gap behind", "should we stay out?"])
def test_a_proposal_to_stay_out_still_gets_the_planning_tier(proposal):
    assert EngineerBrain.classify_request(proposal) == "deep"


@pytest.mark.asyncio
async def test_a_pit_question_during_the_hold_with_no_provider_gets_the_next_option(stack, monkeypatch):
    _store, brain, _captured = await _race(stack, monkeypatch, ["Copy, staying out.", _timeout()])
    await brain.ask("I'm not boxing")
    heard = await brain.ask("when do we box")
    assert heard.startswith("Engineer service timed out; holding as agreed; next option is box lap 17 for hards")
    assert "lap 14" not in heard


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [
    "I'm not boxing, stay out", "No, stay out", "Negative, we stay out this lap",
])
async def test_a_refusal_answered_by_the_fallback_never_speaks_the_refused_call(stack, monkeypatch, refusal):
    store, brain, _captured = await _race(stack, monkeypatch, [_timeout()])
    heard = await brain.ask(refusal)
    assert (await store.snapshot_analysis())["strategy_hold"]["active"]
    assert heard == (
        "Engineer service timed out; copy, staying out; next option is box lap 17 for hards, "
        "about 2.1 seconds slower."
    )


@pytest.mark.asyncio
async def test_the_qualifier_and_the_cut_follow_the_next_option_not_the_refusal(stack, monkeypatch):
    reply = "Copy, we won't box. Next option is box lap 17 for hards, about 2.1 seconds slower."
    store, brain, _captured = await _race(stack, monkeypatch, [reply])
    heard = await brain.ask("I'm not boxing, stay out")
    assert heard.startswith("Copy, we won't box. Next option is box lap 17 for hards, about 2.1 seconds slower; provisional")

    _store, brain, _captured = await _race(
        stack, monkeypatch,
        ["Understood, no stop this lap. Next option is box lap 17 for hards, about 2.1 seconds slower."],
        radio_verbosity="terse",
    )
    heard = await brain.ask("I'm not boxing, stay out")
    assert heard.startswith("Next option is box lap 17 for hards")


@pytest.mark.asyncio
async def test_a_no_stop_option_takes_the_qualifier_not_the_acknowledgement(stack, monkeypatch):
    plan = _plan(box_lap=34, box_laps=[34], compounds=["HARD", "MEDIUM"], fit_compound="MEDIUM",
                 instruction="Box lap 34 for MEDIUM.")
    plan["plans"] = [plan["recommended"], {
        **plan["recommended"], "box_lap": None, "box_laps": [], "compounds": ["HARD"],
        "fit_compound": None, "instruction": "Stay out to the finish.", "risk_adjusted_time_s": 1803.4,
    }]
    reply = "Copy, staying out. Next option is to stay out to the finish on these, about 3.4 seconds slower."
    _store, brain, captured = await _race(stack, monkeypatch, [reply], strategy=plan, lap=33)
    heard = await brain.ask("I'm not boxing")
    assert "NEXT FEASIBLE OPTION: stay out to the finish" in captured[0]["prompt"]
    assert heard.startswith("Copy, staying out. Next option is to stay out to the finish")
    assert heard.endswith("3.4 seconds slower; provisional, confidence low, tyre estimates include track defaults.")


def test_a_negated_call_verb_is_not_the_pit_call():
    assert _pit_call_index(["Copy, we won't box."]) is None
    assert _pit_call_index(["Understood, no stop this lap.", "Box lap 17 for hards."]) == 1
    assert _pit_call_index(["Don't box this lap; box next lap for hards."]) == 0


@pytest.mark.asyncio
async def test_a_refusal_answered_on_the_standard_radio_after_the_speech_reply_failed_keeps_its_brief(stack, monkeypatch):
    store, brain, captured = await _race(stack, monkeypatch, ["Copy, staying out."])
    commands = await brain.apply_driver_commands("I'm not boxing, stay out")
    assert commands.declined
    # The speech session is told the same computed option the text path is.
    assert "The next feasible option is box lap 17 for hards, about 2.1 seconds slower." in commands.notes[0]
    await store.append_radio("driver", "I'm not boxing, stay out")
    await brain.ask("I'm not boxing, stay out", recorded=commands)
    prompt = captured[-1]["prompt"]
    assert 'DECLINED PIT CALL: the driver declined "Box lap 14 for hards"' in prompt
    assert "accept it and confirm briefly" not in prompt


@pytest.mark.asyncio
async def test_a_declined_mandatory_stop_with_no_alternative_says_the_change_is_still_owed(stack, monkeypatch):
    plan = _plan()
    plan["plans"] = plan["plans"][:2]
    _store, brain, captured = await _race(stack, monkeypatch, strategy=plan)
    await brain.ask("No, I'm not boxing this lap")
    prompt = captured[0]["prompt"]
    assert "do not invent an alternative" in prompt
    assert "A second dry compound is still owed before the flag" in prompt
    flat = " ".join(STRATEGY_PERSONA.split())
    assert "such as going long" not in flat
    assert "the next option the DECLINED PIT CALL line names" in flat
    persona = " ".join(REALTIME_PERSONA.split())
    assert "from the pit wall note when it names one" in persona


# -------------------------------------------------------- qualifying


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "should we box this lap?", "should I pit", "what's the plan", "when do we box", "what's the strategy",
])
async def test_a_qualifying_pit_question_reaches_the_model_with_the_session_header(stack, monkeypatch, question):
    _store, brain, captured = await _race(
        stack, monkeypatch, ["One more lap; you are 0.2 inside the cut."],
        strategy={}, mode_profile="qualifying", session_type="Qualifying 1", total_laps=0,
    )
    heard = await brain.ask(question)
    assert heard != "Strategy is still building."
    assert len(captured) == 1


# ---------------------------------------------------- a disputed call


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge", ["why box", "Are you sure about boxing?"])
async def test_a_terse_challenge_keeps_the_reason_with_the_call(stack, monkeypatch, challenge):
    reason = "Your current wear model projects 100% by the finish; the hard stint projects 33%."
    _store, brain, captured = await _race(
        stack, monkeypatch, strategy=_plan(tyre_reason=reason), radio_verbosity="terse",
    )
    heard = await brain.ask(challenge)
    assert captured == []
    assert heard.startswith("Box lap 14 for hards; provisional, confidence low")
    assert "your current wear model projects 100% by the finish" in heard


# -------------------------------------------------------------- units


@pytest.mark.parametrize("utterance,unit", [
    ("Is that in Fahrenheit?", None),
    ("Are those temperatures in celsius or fahrenheit?", None),
    ("What's 100 degrees F in celsius?", None),
    ("Stop giving me temperatures in Fahrenheit", None),
    ("Not fahrenheit, celsius please", "c"),
    ("Don't use fahrenheit, give me celsius", "c"),
    ("Give me tyre temperatures in Fahrenheit please.", "f"),
    ("Switch to Fahrenheit, what are the fronts?", "f"),
    ("tyre temps in fahrenheit", "f"),
])
def test_the_temperature_unit_is_the_one_asked_for(utterance, unit):
    assert EngineerBrain._requested_temperature_unit(utterance) == unit


@pytest.mark.asyncio
@pytest.mark.parametrize("start,utterance,unit", [
    ("c", "Can I have the temperatures in Fahrenheit", "f"),
    ("c", "Could we switch to Fahrenheit?", "f"),
    ("f", "Can we go back to celsius please", "c"),
    ("c", "Stop the tyre updates and give me temps in Fahrenheit", "f"),
    ("f", "Stop using Fahrenheit and switch back to Celsius", "c"),
    ("c", "Can you give me Fahrenheit, or is that Celsius already?", "f"),
    ("c", "Can you tell me if that's in fahrenheit?", "c"),
    ("c", "Did you give me those in Fahrenheit?", "c"),
])
async def test_a_unit_request_worded_as_a_question_still_sets_the_unit(stack, monkeypatch, start, utterance, unit):
    store, brain, _captured = await _race(stack, monkeypatch, ["Copy."], temperature_unit=start)
    await brain.apply_driver_commands(utterance)
    assert (await store.snapshot_analysis())["temperature_unit"] == unit


@pytest.mark.asyncio
async def test_a_question_about_the_unit_does_not_change_it(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, ["Those are Celsius."], temperature_unit="c")
    await brain.ask("Is that in Fahrenheit?")
    assert (await store.snapshot_analysis())["temperature_unit"] == "c"


# ------------------------------------------------- reports and settings


@pytest.mark.asyncio
async def test_a_position_report_with_a_contraction_is_checked_against_timing(stack, monkeypatch):
    _store, brain, captured = await _race(stack, monkeypatch, ["Copy, P4."])
    heard = await brain.ask("I'm in P4")
    assert captured == []
    assert heard.startswith("Your report is P4, while timing shows P5.")


@pytest.mark.asyncio
async def test_a_setting_with_a_quick_question_survives_a_provider_failure(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, [_timeout()])
    heard = await brain.ask("Keep your answers short. What's my fuel?")
    assert (await store.snapshot_analysis())["radio_verbosity"] == "terse"
    assert heard.startswith("Engineer service timed out. Short answers confirmed. ")
    assert "0.8" in heard


def test_the_setup_brief_does_not_order_a_tool_the_route_does_not_offer():
    race = {"mode_profile": "race"}
    without = persona_addenda(race, "should I add front wing", set())
    assert SETUP_PERSONA in without
    flat = " ".join(" ".join(without).split())
    assert "generate_setup" not in flat
    assert "Do not describe a reference setup you have not retrieved" in flat
    offered = " ".join(" ".join(persona_addenda(race, "should I add front wing", {"generate_setup"})).split())
    assert "Use generate_setup with basis reference" in offered
    assert "reference setup you have not retrieved" not in offered


def test_a_saved_session_question_reaches_the_saved_session_tools():
    for utterance in ("review my saved session from yesterday", "list my saved sessions",
                      "compare my lap to my reference lap", "what are my driver strengths"):
        assert EngineerBrain.classify_request(utterance) == "deep", utterance


def test_a_process_opener_is_not_the_whole_terse_reply():
    assert _limit_radio_sentences(
        "Okay, looking at the data. Gap is 1.5 seconds and closing 0.2 a lap.", "normal", "terse"
    ) == "Gap is 1.5 seconds and closing 0.2 a lap."
    assert _limit_radio_sentences(
        "Looking at the gap, he is closing. Defend into turn one.", "normal", "terse"
    ) == "Looking at the gap, he is closing."


# --------------------------------------------------- the tyre check answer


@pytest.mark.parametrize("answer,closed", [
    ("Holding.", "holding"), ("Still holding.", "holding"), ("They're holding.", "holding"),
    ("Going away.", "going away"), ("Yeah, going away.", "going away"), ("holding on to P4", None),
])
def test_the_tyre_check_answer_is_read_as_a_transcript_writes_it(answer, closed):
    assert _closed_tyre_answer(answer) == closed


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,category", [("Holding.", "tyres_fine"), ("Yeah, going away.", "tyres_going")])
async def test_the_tyre_check_answer_reaches_the_strategy_model(stack, monkeypatch, answer, category):
    store, brain, _captured = await _race(stack, monkeypatch, ["Copy."])
    await brain.apply_driver_commands(answer)
    feedback = (await store.snapshot_analysis())["driver_tyre_feedback"]
    assert feedback["category"] == category


# ------------------------------------------------ what the radio replays


@pytest.mark.asyncio
async def test_a_spoken_line_with_hold_on_is_replayed_and_kept_in_the_history(stack, monkeypatch):
    line = "Stay out. Hold on, Safety Car is being deployed, box box box."
    _store, brain, captured = await _race(stack, monkeypatch, [line, "Why next lap is the safety car."])
    heard = await brain.ask("what's the plan with Leclerc")
    assert "Hold on, Safety Car is being deployed, box box box." in heard
    assert await brain.ask("say again") == heard
    assert len(captured) == 1, "replayed word for word, with no model call"
    await brain.ask("why that")
    assert f"ENGINEER: {heard}" in captured[-1]["prompt"]


# ------------------------------------------------- the speech session


_NO_GAIN = {
    "recommended": {
        "box_lap": 33, "fit_compound": "SOFT", "box_laps": [33], "compounds": ["MEDIUM", "SOFT"],
        "feasible": True, "legal": True, "instruction": "Box lap 33 for SOFT.", "stops_remaining": 1,
        "positions_gained_vs_stay_out": 0, "positions_lost_by_stopping": 6,
        "stop_required_reason": "best projected finishing position and expected-points outcome",
    },
    "plans": [], "confidence": "high",
}


@pytest.mark.asyncio
async def test_the_speech_session_says_the_pit_call_push_to_talk_says(stack, monkeypatch):
    store, brain, _captured = await _race(
        stack, monkeypatch, strategy=_NO_GAIN, lap=33, total_laps=37,
    )
    asked = await brain.ask("should I box")
    state = await store.snapshot_analysis()
    spoken = EngineerBrain.spoken_pit_call(state["strategy"], state)
    assert asked.startswith("Stay out — boxing costs you 6 places and gains nothing")
    assert spoken.startswith("Stay out — boxing costs you 6 places and gains nothing")
    brief = await brain.realtime_instructions(REALTIME_PERSONA)
    assert "PIT CALL AS SPOKEN: Stay out — boxing costs you 6 places" in brief


@pytest.mark.asyncio
async def test_the_speech_session_calls_a_legal_stop_no_plan_can_avoid(stack, monkeypatch):
    plan = _plan(feasible=False, box_lap=35, box_laps=[35], stop_required_reason="mandatory compound change",
                 instruction="No feasible finish is supported by the current tyre data. A second compound is "
                             "mandatory — box lap 35 for HARD.")
    store, _brain, _captured = await _race(stack, monkeypatch, strategy=plan, lap=34, total_laps=52)
    state = await store.snapshot_analysis()
    spoken = EngineerBrain.spoken_pit_call(state["strategy"], state)
    assert spoken.startswith("Box lap 35 for hards; no plan reaches the flag inside the wear limit")
    illegal = deepcopy(plan)
    illegal["recommended"]["legal"] = False
    assert EngineerBrain.spoken_pit_call(illegal, state).startswith("No current, legal and feasible pit plan")


@pytest.mark.asyncio
async def test_the_speech_brief_keeps_the_drivers_question_and_tags_old_calls(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, lap=34)
    for lap, role, text, source in (
        (21, "engineer", "Box this lap for hards, box this lap.", "proactive"),
        (33, "driver", "Hold on, are we still going to the end on these mediums or not?", None),
        (33, "engineer", "Stay out. Hold on, Safety Car is being deployed, box box box.", "reply"),
    ):
        await store.update(current_lap=lap)
        await store.append_radio(role, text, **({"source": source} if source else {}))
    await store.update(current_lap=34)
    brief = await brain.realtime_instructions(REALTIME_PERSONA)
    assert "L21 ENGINEER (unsolicited) [old]: Box this lap for hards, box this lap." in brief
    assert "L33 DRIVER: Hold on, are we still going to the end on these mediums or not?" in brief
    assert "Stay out. Hold on, Safety Car is being deployed, box box box." in brief


@pytest.mark.asyncio
async def test_an_expired_hold_is_not_given_to_the_speech_session(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, lap=20)
    await store.update(strategy_hold={"active": True, "until_lap": 17, "set_at_lap": 12})
    brief = await brain.realtime_instructions(REALTIME_PERSONA)
    assert "DRIVER DECLINED THE PIT CALL" not in brief
    await store.update(current_lap=15)
    assert "DRIVER DECLINED THE PIT CALL" in await brain.realtime_instructions(REALTIME_PERSONA)


# ------------------------------------------------------------ the header


@pytest.mark.asyncio
async def test_a_stale_race_control_phase_is_not_printed_as_current(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, race_control_phase="safety_car")
    assert "race control safety_car |" in await brain.situation_header()

    def age(state):
        for group in list(state.packet_group_freshness):
            state.packet_group_freshness[group] = time.time() - 90.0

    await store.mutate(age)
    header = await brain.situation_header()
    assert "race control safety_car |" not in header
    assert "race control unknown (last confirmed safety_car, stale)" in header


@pytest.mark.asyncio
async def test_temperatures_reach_the_narrator_and_the_header_in_the_drivers_unit(stack, monkeypatch):
    store, brain, _captured = await _race(
        stack, monkeypatch, strategy={}, mode_profile="qualifying", session_type="Qualifying 1",
        temperature_unit="f",
    )
    state = await store.snapshot_analysis()
    facts = narration_facts(
        {"type": "quali_clear_air", "payload": {"lap": 3, "tyre_temps": [100.0, 100.0, 90.0, 90.0]}}, state
    )["facts"]
    assert facts["tyre_temps_f"] == [212.0, 212.0, 194.0, 194.0]
    assert "tyre_temps" not in facts
    assert "TEMPS IN F" in await brain.situation_header()


@pytest.mark.asyncio
async def test_the_speech_session_does_not_restate_a_declined_stop(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, ["Copy, staying out."])
    await brain.ask("I'm not boxing")
    state = await store.snapshot_analysis()
    spoken = EngineerBrain.spoken_pit_call(state["strategy"], state)
    assert spoken == "Holding as agreed; next option is box lap 17 for hards, about 2.1 seconds slower."
    brief = await brain.realtime_instructions(REALTIME_PERSONA)
    assert "PIT CALL AS SPOKEN: Holding as agreed; next option is box lap 17" in brief


@pytest.mark.parametrize("utterance", [
    "stay out of trouble", "Stay out of his dirty air on the exit", "staying out of trouble, nice",
])
def test_driving_talk_about_staying_out_of_something_is_not_a_declined_stop(utterance):
    assert EngineerBrain._strategy_refusal(utterance) is False


@pytest.mark.parametrize("utterance", [
    "I'm staying out", "stay out", "I'm staying out of the pits", "stay out of the pit lane this lap",
])
def test_staying_out_of_the_pits_is_still_a_declined_stop(utterance):
    assert EngineerBrain._strategy_refusal(utterance) is True


@pytest.mark.asyncio
async def test_a_hold_ends_at_a_stop_for_the_same_compound(stack, monkeypatch):
    """A stop for the same compound changes no compound name, only the age."""
    store, brain, _captured = await _race(stack, monkeypatch, ["Copy, staying out."])
    await store.mutate(lambda state: state.proactive.update({"enabled": False}))

    def on_worn_mediums(state):
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 12

    await store.mutate(on_worn_mediums)
    await brain.ask("I'm not boxing")
    assert "STRATEGY HOLD" in await brain.situation_header()

    def fresh_mediums(state):
        state.current_lap = 14
        state.tyre.age_laps = 0

    await store.mutate(fresh_mediums)
    assert "STRATEGY HOLD" not in await brain.situation_header()


@pytest.mark.asyncio
async def test_a_flashback_that_rewinds_the_tyre_age_keeps_the_hold(stack, monkeypatch):
    store, brain, _captured = await _race(stack, monkeypatch, ["Copy, staying out."])
    await store.mutate(lambda state: state.proactive.update({"enabled": False}))

    def on_worn_mediums(state):
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 12

    await store.mutate(on_worn_mediums)
    await brain.ask("I'm not boxing")

    def rewound(state):
        state.tyre.age_laps = 11

    await store.mutate(rewound)
    assert "STRATEGY HOLD" in await brain.situation_header()
