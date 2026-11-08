"""Radio commands that change binding state must only fire on a decision.

A strategy lock, a five-lap pit-call hold, an agreed tactic, a saved
preference, short-answer mode and a standing "don't raise this" rule all
outlive the sentence that set them. Each used to recognise a question only
when its very first word was a question word, so a filler in front ("Okay,
should we stay out?"), a trailing "?" ("Pit now or stay out?") or a bare
phrase match ("is there enough grip left with these tyres") turned the
driver's question into a decision, and the question itself was answered with
a canned acknowledgement.
"""

from __future__ import annotations

import pytest

from pitwall.audio import RACING_VOCAB, AudioService
from pitwall.brain import EngineerBrain
from pitwall.config import settings
from pitwall.intent import (
    extract_lap,
    is_question,
    spoken_numbers_to_digits,
    utterance_mood,
)
from pitwall.proactive import ProactiveEngineer


async def _brain_mid_race(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    brain = EngineerBrain(store, tools, database)

    def apply(state):
        state.connected = True
        state.current_lap = 18
        state.player_position = 4
        state.player_car_index = 0
        state.total_laps = 40
        state.mode_profile = "race"
        state.strategy = {
            "confidence": "low",
            "recommended": {
                "box_lap": 24,
                "fit_compound": "HARD",
                "stops_remaining": 1,
                "instruction": "Box lap 24 for hards.",
            },
        }
        for index, (name, driver_id, position) in enumerate(
            [("PLAYER", 255, 4), ("VERSTAPPEN", 9, 3), ("NORRIS", 3, 5)]
        ):
            driver = state.drivers[index]
            driver.name = name
            driver.driver_id = driver_id
            driver.active = True
            driver.position = position
            driver.last_lap_ms = 97_550

    await store.mutate(apply)
    return store, database, brain


async def _binding_state(store):
    snapshot = await store.snapshot_live()
    return {
        "hold": bool((snapshot.get("strategy_hold") or {}).get("active")),
        "intent": (snapshot.get("strategy_intent") or {}).get("intent"),
        "locked": bool((snapshot.get("strategy_override") or {}).get("locked")),
        "preferences": snapshot.get("driver_preferences"),
        "risk": snapshot.get("strategy_risk_appetite"),
        "verbosity": snapshot.get("radio_verbosity"),
        "standing": list(snapshot.get("standing_instructions") or []),
    }


@pytest.fixture(autouse=True)
def _default_wake_name(monkeypatch):
    monkeypatch.setattr(settings, "wake_phrase", "mark")
    monkeypatch.setattr(settings, "wake_aliases", "marc,hey mark")


# ---------------------------------------------------------------------------
# Strategy: a question is never a hold, an agreed tactic or a lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Okay, should we stay out?",
        "okay should we stay out",
        "so are we staying out",
        "right should we stay out or box",
        "Pit now or stay out?",
        "pit now or stay out",
        "tell me if I should stay out",
        "so should I box this lap or stay out",
        "We're staying out, right?",
    ],
)
async def test_a_question_about_staying_out_never_silences_the_pit_call(stack, question):
    store, _database, brain = await _brain_mid_race(stack)
    before = await _binding_state(store)

    await brain._fast_answer(question)

    assert EngineerBrain._strategy_refusal(question) is False, question
    assert await _binding_state(store) == before, question


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "okay what's the undercut worth",
        "So is the overcut on?",
        "so is the overcut on",
        "mark can we go long on these",
    ],
)
async def test_a_question_about_a_tactic_is_not_an_agreement_however_it_opens(stack, question):
    store, _database, brain = await _brain_mid_race(stack)

    await brain._fast_answer(question)

    state = await _binding_state(store)
    assert state["intent"] is None, question
    assert state["hold"] is False, question
    header = await brain.situation_header()
    assert "AGREED WITH THE DRIVER" not in header


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "okay what if we switch to the hards",
        "tell me what happens if we move to hards on lap 22",
        "box for hards or mediums",
    ],
)
async def test_a_hypothetical_never_locks_the_strategy(stack, question):
    store, _database, brain = await _brain_mid_race(stack)

    answer = await brain._fast_answer(question)

    assert "locked" not in str(answer or "").lower(), (question, answer)
    assert (await _binding_state(store))["locked"] is False, question


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remark",
    [
        "I'm not happy with that pit stop",
        "that pit stop was not great",
        "without a safety car can we make it on one stop",
        "I can't stay out on these, they're gone",
        "I don't think we should stay out",
        "I can't stop the rear stepping out",
        "don't forget the pit stop",
    ],
)
async def test_a_negation_near_the_word_pit_is_not_a_refusal(stack, remark):
    store, _database, brain = await _brain_mid_race(stack)

    await brain._fast_answer(remark)

    assert EngineerBrain._strategy_refusal(remark) is False, remark
    assert (await _binding_state(store))["hold"] is False, remark


@pytest.mark.parametrize(
    "refusal",
    [
        "Okay, I'm staying out",
        "I'm staying out, okay?",
        "Copy, we're not stopping again",
        "Do not box me this lap",
        "I'm not going to take another pit stop",
        "forget the pit stop, we stay out",
        "No, I am not boxing, it does not make sense.",
    ],
)
def test_a_stated_refusal_still_holds_the_pit_call(refusal):
    assert EngineerBrain._strategy_refusal(refusal) is True, refusal


def test_decisions_still_lock_after_a_filler():
    action = EngineerBrain._strategy_override_action("Okay, box lap 22 for hards", 18)
    assert action is not None
    assert action["next_box_lap"] == 22
    assert action["next_compound"] == "HARD"
    intent = EngineerBrain._strategy_intent("Right, let's do the overcut", 18)
    assert intent is not None and intent["intent"] == "overcut"


@pytest.mark.parametrize(
    "utterance,mood",
    [
        ("Okay, should we stay out?", "question"),
        ("Pit now or stay out?", "question"),
        ("Copy?", "question"),
        ("could you tell me whether we should stay out", "question"),
        ("could you please keep your answers short", "request"),
        ("can you please confirm that I do not want reminders regarding my gearbox", "request"),
        ("Do not box me this lap", "statement"),
        ("I'm staying out, okay?", "statement"),
        ("I'm not boxing or stopping", "statement"),
        ("will do, staying out", "statement"),
    ],
)
def test_one_classifier_tells_questions_requests_and_statements_apart(utterance, mood):
    assert utterance_mood(utterance) == mood, utterance
    assert is_question(utterance) is (mood == "question")


# ---------------------------------------------------------------------------
# Preferences: questions and refusals are not saved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "how do I get more traction out of the slow corners",
        "how can I get more rotation in turn four",
        "should I protect the tyres now or push",
        "do I need to protect the tires",
        "how much straight line speed am I losing",
        "don't protect the tyres I want to push",
        "what does a conservative strategy look like",
        "would an aggressive strategy work here",
        "I'm losing straight line speed on the back straight",
    ],
)
async def test_a_question_or_remark_never_saves_a_preference(stack, utterance):
    store, database, brain = await _brain_mid_race(stack)
    before = await _binding_state(store)

    answer = await brain._fast_answer(utterance)

    assert "preference saved" not in str(answer or "").lower(), (utterance, answer)
    assert EngineerBrain._preference_updates(utterance) == {}, utterance
    after = await _binding_state(store)
    assert after["preferences"] == before["preferences"], utterance
    assert after["risk"] == before["risk"], utterance
    assert await database.load_preference("driver_preferences", None) is None


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("I want more traction", {"traction": 2, "setup_bias": "traction"}),
        ("give me more rotation please", {"rotation": 2, "setup_bias": "rotation"}),
        ("more rear stability please", {"rear_stability": 2, "setup_bias": "rear_stability"}),
        ("push, but protect the tyres", {"tyre_life": 2, "strategy_priority": "safety"}),
        ("let's play it safe on strategy", {"strategy_risk_appetite": "conservative"}),
        (
            "I want more rear stability rather than more rotation",
            {"rear_stability": 2, "setup_bias": "rear_stability"},
        ),
    ],
)
def test_a_stated_preference_is_still_saved(utterance, expected):
    assert EngineerBrain._preference_updates(utterance) == expected


# ---------------------------------------------------------------------------
# Short answers: only a complaint about the engineer's own length
# ---------------------------------------------------------------------------


def _stub_model(monkeypatch, brain):
    async def answer(*_args, **_kwargs):
        return "Model answer."

    monkeypatch.setattr(brain, "_run", answer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "is this stint too long",
        "how long is too long on these mediums",
        "this traffic is very annoying",
        "it's too long since the last stop",
    ],
)
async def test_too_long_about_the_race_does_not_cut_every_answer(stack, monkeypatch, utterance):
    store, _database, brain = await _brain_mid_race(stack)
    _stub_model(monkeypatch, brain)

    answer = await brain.ask(utterance)

    assert answer != "Short answers confirmed.", utterance
    assert (await _binding_state(store))["verbosity"] == "standard", utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "keep your answers short",
        "could you please keep your answers short",
        "your answers are way too long",
        "you are talking too much",
        "stop going on",
    ],
)
def test_asking_for_short_answers_still_works(utterance):
    assert EngineerBrain._requests_short_answers(utterance) is True, utterance


@pytest.mark.asyncio
async def test_the_driver_can_ask_for_normal_answers_again(stack, monkeypatch):
    store, _database, brain = await _brain_mid_race(stack)
    _stub_model(monkeypatch, brain)
    await store.update(radio_verbosity="terse")

    answer = await brain.ask("you can give me normal answers again")

    assert "normal-length answers" in answer
    assert (await _binding_state(store))["verbosity"] == "standard"
    assert EngineerBrain._requests_short_answers("no more short answers") is False


# ---------------------------------------------------------------------------
# Standing instructions: only a real "stop raising this", and a way back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "is there enough grip left with these tyres",
        "do we have enough fuel with the safety car",
        "have we got enough margin with the car behind",
        "do I have enough fuel with the extra laps",
        "I don't want to lose time with the traffic",
    ],
)
async def test_enough_and_with_in_a_question_set_no_lasting_rule(stack, utterance):
    store, database, brain = await _brain_mid_race(stack)

    answer = await brain._fast_answer(utterance)

    assert answer != "Copy, understood. I won't bring that up again.", utterance
    assert EngineerBrain._standing_instruction(utterance) is None, utterance
    assert (await _binding_state(store))["standing"] == []
    assert await database.load_preference("standing_instructions", None) is None


@pytest.mark.parametrize(
    "utterance,subject",
    [
        ("enough with the tyres", "tyres"),
        ("stop it with the fuel", "fuel"),
        ("shut up with the gearbox", "gearbox"),
        ("no more updates about the fuel", "fuel"),
        ("that's enough about corner coaching", "corner coaching"),
    ],
)
def test_a_real_suppression_still_names_its_subject(utterance, subject):
    rule = EngineerBrain._standing_instruction(utterance)
    assert rule is not None and subject in rule, (utterance, rule)


@pytest.mark.asyncio
async def test_stop_going_on_about_a_subject_drops_the_subject(stack, monkeypatch):
    """It is a standing rule, not a request to shorten every answer."""
    store, _database, brain = await _brain_mid_race(stack)
    _stub_model(monkeypatch, brain)

    answer = await brain.ask("stop going on about the tyres")

    assert answer == "Copy, understood. I won't bring that up again."
    state = await _binding_state(store)
    assert state["verbosity"] == "standard"
    assert ProactiveEngineer.is_suppressed("tyre_wear", state["standing"]) is True


@pytest.mark.asyncio
async def test_a_standing_rule_can_be_lifted_over_the_radio(stack):
    store, database, brain = await _brain_mid_race(stack)
    await brain._fast_answer("stop telling me about the tyres")
    await brain._fast_answer("shut up about engine damage")
    assert ProactiveEngineer.is_suppressed(
        "tyre_wear", (await _binding_state(store))["standing"]
    )

    answer = await brain._fast_answer("you can talk about the tyres again")

    assert answer == "Copy, I'll report tyres again."
    standing = (await _binding_state(store))["standing"]
    assert ProactiveEngineer.is_suppressed("tyre_wear", standing) is False
    assert EngineerBrain.suppressed_subjects(standing) == ["engine damage"]
    saved = await database.load_preference("standing_instructions", None)
    assert EngineerBrain.suppressed_subjects(saved) == ["engine damage"]

    assert await brain._fast_answer("clear my standing instructions") == (
        "Copy, standing instructions cleared."
    )
    assert (await _binding_state(store))["standing"] == []


@pytest.mark.asyncio
async def test_lifting_a_rule_that_was_never_set_is_an_ordinary_turn(stack):
    store, _database, brain = await _brain_mid_race(stack)
    await brain._fast_answer("stop telling me about the gearbox")

    answer = await brain._fast_answer("you can tell me about the fuel again")

    assert "report" not in str(answer or "").lower()
    assert len((await _binding_state(store))["standing"]) == 1


# ---------------------------------------------------------------------------
# Session profile: a question does not lock it
# ---------------------------------------------------------------------------


def test_a_question_about_the_session_does_not_lock_it():
    for question in ("so this is the race?", "this is qualifying, right?"):
        assert EngineerBrain._manual_session_override_mode(question) is None, question
    assert EngineerBrain._manual_session_override_mode("this is qualifying") == "qualifying"


# ---------------------------------------------------------------------------
# Spoken lap and position numbers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,lap",
    [
        ("box lap twenty two for hards", 22),
        ("box lap twenty for hards", 20),
        ("box lap twenty-five for mediums", 25),
    ],
)
async def test_a_spoken_lap_number_is_locked_as_the_lap(stack, command, lap):
    store, _database, brain = await _brain_mid_race(stack)

    answer = await brain._fast_answer(command)

    assert f"box lap {lap}" in str(answer).lower(), answer
    override = (await store.snapshot_live())["strategy_override"]
    assert override["next_box_lap"] == lap
    assert override["locked"] is True


@pytest.mark.asyncio
async def test_a_lap_that_could_not_be_read_is_asked_for_not_guessed(stack):
    store, _database, brain = await _brain_mid_race(stack)

    answer = await brain._fast_answer("box lap for hards")

    assert answer.startswith("Which lap for the hards?"), answer
    assert (await _binding_state(store))["locked"] is False


def test_number_words_are_read_only_where_they_name_a_lap_or_position():
    assert extract_lap("box on lap number twenty one", 18) == 21
    assert extract_lap("last lap two tenths quicker", 18) is None
    assert spoken_numbers_to_digits("let's do the one stop") == "let's do the one stop"
    assert spoken_numbers_to_digits("box in two laps") == "box in 2 laps"
    assert spoken_numbers_to_digits("in one stop") == "in one stop"
    assert EngineerBrain._driver_position_claim("I am in P five") == 5
    assert EngineerBrain._driver_position_claim("we are in position eleven") == 11


def test_the_transcriber_is_steered_towards_the_words_commands_use():
    assert {"softs", "stay out", "lift and coast", "front wing"} <= set(RACING_VOCAB)


def test_the_transcriber_hears_every_name_on_a_full_grid():
    # Empty car slots at the front of the list pushed the last cars out.
    names = ["PLAYER", "", "", "Unknown"] + [f"DRIVER{index}" for index in range(21)]
    prompt = AudioService.transcription_prompt(names, ["mark"])
    assert "DRIVER19" in prompt and "DRIVER20" in prompt
    assert AudioService._looks_like_prompt_echo("I am not boxing this lap, stay out", prompt) is False


# ---------------------------------------------------------------------------
# Orders that look like questions, and questions that look like orders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance,mood",
    [
        ("do it", "statement"),
        ("do me a favour and stop telling me about the fuel", "statement"),
        ("have the hards ready, box lap 22 for hards", "statement"),
        ("do we box this lap", "question"),
        ("have we got enough fuel", "question"),
        ("hypothetically I want to try switching to softs", "question"),
        ("suppose we box lap 22 for hards", "question"),
        ("we're staying out, aren't we", "question"),
    ],
)
def test_imperatives_and_hypotheticals_are_told_apart(utterance, mood):
    assert utterance_mood(utterance) == mood, utterance


@pytest.mark.asyncio
async def test_an_order_to_have_the_tyres_ready_still_locks_the_stop(stack):
    store, _database, brain = await _brain_mid_race(stack)

    answer = await brain._fast_answer("Have the hards ready, box lap 22 for hards")

    assert answer == "Driver strategy locked: box lap 22 for hards."
    assert (await store.snapshot_live())["strategy_override"]["next_box_lap"] == 22


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "hypothetically I want to try switching to softs",
        "suppose we box lap 22 for hards",
    ],
)
async def test_a_hypothetical_stop_is_not_locked(stack, question):
    store, _database, brain = await _brain_mid_race(stack)

    answer = await brain._fast_answer(question)

    assert "locked" not in str(answer or "").lower(), (question, answer)
    assert (await _binding_state(store))["locked"] is False, question


@pytest.mark.parametrize(
    "remark",
    ["we're staying out, aren't we", "I won't forget the pit stop", "we can't skip the stop"],
)
def test_a_tag_question_or_a_promise_is_not_a_refusal(remark):
    assert EngineerBrain._strategy_refusal(remark) is False, remark


# ---------------------------------------------------------------------------
# Standing rules: the way back must not run backwards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance",
    [
        "you can stop updating me about the tyres",
        "you can quit telling me about the tyres",
        "you can stop warning me about the tyres",
    ],
)
async def test_asking_again_to_drop_a_subject_never_lifts_its_rule(stack, utterance):
    store, _database, brain = await _brain_mid_race(stack)
    await brain._fast_answer("stop telling me about the tyres")

    answer = await brain._fast_answer(utterance)

    assert "report" not in str(answer or "").lower(), (utterance, answer)
    standing = (await _binding_state(store))["standing"]
    assert ProactiveEngineer.is_suppressed("tyre_wear", standing) is True, utterance


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance", ["forget my standing instructions", "cancel my standing instructions"]
)
async def test_every_clearing_word_clears_the_standing_rules(stack, utterance):
    store, database, brain = await _brain_mid_race(stack)
    await brain._fast_answer("stop telling me about the tyres")

    assert await brain._fast_answer("don't clear my standing instructions") != (
        "Copy, standing instructions cleared."
    )
    assert len((await _binding_state(store))["standing"]) == 1

    assert await brain._fast_answer(utterance) == "Copy, standing instructions cleared."
    assert (await _binding_state(store))["standing"] == []
    assert await database.load_preference("standing_instructions", None) == []
