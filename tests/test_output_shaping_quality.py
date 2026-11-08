"""What the driver finally hears once the radio has shaped a reply.

Every reply passes through the same shaping before it is spoken: the sentence
cut, the strategy qualifier and change condition, and the deterministic
templates. These tests drive the real paths with a scripted provider and
check the words that come out.
"""

import re
from copy import deepcopy

import pytest

from pitwall.brain import (
    PERSONA,
    EngineerBrain,
    _limit_radio_sentences,
    compose_persona,
)
from pitwall.briefing import BriefingEngine
from pitwall.proactive import ProactiveEngineer
from pitwall.providers import ProviderResult

CONDITION = (
    "The call changes for a safety car, red flag, wet crossover, new damage, "
    "or a hard tyre-wear limit breach."
)


def _low_confidence_plan():
    best = {
        "box_lap": 14, "fit_compound": "HARD", "box_laps": [14], "compounds": ["MEDIUM", "HARD"],
        "feasible": True, "legal": True, "instruction": "Box lap 14 for HARD.",
        "call_changes_if": CONDITION, "change_condition": CONDITION,
    }
    alternative = {**best, "box_lap": 16, "box_laps": [16]}
    return {
        "recommended": best, "plans": [best, alternative], "confidence": "low",
        "tyre_inventory": {"status": "known"},
        "model_summary": {"selected_stint_wear_source": "track_default",
                          "selected_stint_deg_source": "track_default"},
    }


async def _scripted(stack, monkeypatch, reply, *, verbosity="standard", strategy=None, captured=None):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    await store.update(
        current_lap=6, total_laps=31, mode_profile="race", radio_verbosity=verbosity,
        strategy=deepcopy(strategy) if strategy is not None else {},
    )
    brain = EngineerBrain(store, tools, database)

    async def generate(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        return ProviderResult(reply, "test", "test", 1)

    async def call(name, arguments):
        return deepcopy(strategy or {})

    monkeypatch.setattr(brain.router, "generate", generate)
    monkeypatch.setattr(tools, "call", call)
    return store, brain


# --------------------------------------------------------------- sentence cut


@pytest.mark.asyncio
@pytest.mark.parametrize("verbosity,reply,heard", [
    # Terse kept the acknowledgement and dropped the instruction.
    ("terse", "Copy. Closing on Alonso, save battery for the Hangar Straight.",
     "Closing on Alonso, save battery for the Hangar Straight."),
    ("standard", "Copy that. Understood. Closing on Alonso, save battery for the Hangar Straight.",
     "Closing on Alonso, save battery for the Hangar Straight."),
])
async def test_the_cut_keeps_the_answer_not_the_acknowledgement(stack, monkeypatch, verbosity, reply, heard):
    _, brain = await _scripted(stack, monkeypatch, reply, verbosity=verbosity)
    assert await brain.ask("how am I doing against Alonso") == heard


@pytest.mark.asyncio
async def test_a_declined_stop_keeps_its_consequence_and_alternative_in_terse(stack, monkeypatch):
    reply = "Copy, staying out. That costs about two seconds; the alternative is box lap 16 for hards."
    _, brain = await _scripted(stack, monkeypatch, reply, verbosity="terse")
    answer = await brain.ask("No, I'm staying out")
    assert "the alternative is box lap 16 for hards" in answer


def test_an_abbreviation_is_not_a_sentence_end():
    text = "You're closing on Leclerc vs. Norris behind. Gap 1.2 seconds. Push now."
    assert _limit_radio_sentences(text, "normal", "standard") == (
        "You're closing on Leclerc vs. Norris behind. Gap 1.2 seconds."
    )


def test_a_lone_acknowledgement_is_still_spoken_when_it_is_the_whole_reply():
    assert _limit_radio_sentences("Copy.", "normal", "terse") == "Copy."


@pytest.mark.asyncio
@pytest.mark.parametrize("verbosity,reply,heard", [
    ("terse", "Two laps to the window. How are the rears, holding or going away?",
     "Two laps to the window. How are the rears, holding or going away?"),
    ("standard",
     "Two laps to the box window on lap 21. Rear left is at 66 percent. Are the rears holding or going away?",
     "Two laps to the box window on lap 21. Are the rears holding or going away?"),
])
async def test_a_driver_check_keeps_its_question_through_the_cut(stack, monkeypatch, verbosity, reply, heard):
    _, brain = await _scripted(stack, monkeypatch, reply, verbosity=verbosity)
    spoken = await brain.proactive({"type": "driver_check", "payload": {"laps_to_window": 2, "box_lap": 8}})
    assert spoken == heard
    assert spoken.endswith("?")  # What opens the reply window.


@pytest.mark.asyncio
async def test_a_driver_check_narration_without_its_question_falls_back_to_the_template(stack):
    store, database, _, _, _, _ = stack

    class _Brain:
        def __init__(self):
            self.database = database

        async def proactive(self, event):
            return "Window opens in two laps."

    engineer = ProactiveEngineer(store, _Brain(), object(), object(), object())
    event = {"type": "driver_check", "payload": {"laps_to_window": 2, "box_lap": 8}}
    spoken = await engineer._narrate(event, await store.snapshot_analysis())
    assert spoken == "2 laps to the window. How are the rears — holding, or going away?"


# --------------------------------------------------------- the real cap, said


@pytest.mark.parametrize("route,verbosity", [
    ("fast", "standard"), ("normal", "standard"), ("deep", "standard"),
    ("normal", "terse"), ("deep", "terse"), ("normal", "chatty"),
])
def test_the_brief_states_the_sentence_limit_the_radio_applies(route, verbosity):
    composed = compose_persona(PERSONA, verbosity, route=route)
    stated = int(re.search(r"cuts this reply after (\d+) sentence", composed).group(1))
    applied = _limit_radio_sentences("One one. Two two. Three three. Four four. Five five.", route, verbosity)
    assert stated == applied.count(".")


def test_the_brief_no_longer_promises_three_sentences_or_puts_the_action_last():
    flat = " ".join(PERSONA.split())
    assert "may use three" not in flat
    assert "then what to do about it" not in flat
    assert "and what to do about it, in the same sentence" in flat


@pytest.mark.asyncio
async def test_an_asked_question_carries_the_limit_for_its_route(stack, monkeypatch):
    captured = []
    _, brain = await _scripted(stack, monkeypatch, "Closing on Alonso.", verbosity="terse", captured=captured)
    await brain.ask("how am I doing against Alonso")
    instructions = captured[0]["instructions"]
    assert "cuts this reply after 1 sentence." in instructions
    assert "Verbosity: terse" in instructions


# --------------------------------------------------- deterministic templates


async def _local_only(stack, monkeypatch, verbosity="terse"):
    store, database, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, database)

    async def no_provider(*args, **kwargs):
        pytest.fail("This answer must come from the deterministic radio")

    monkeypatch.setattr(brain, "_run", no_provider)
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 4321, packet_id=group)
    await store.update(
        connected=True, session_type="Race", mode_profile="race", current_lap=8, total_laps=20,
        player_position=4, radio_verbosity=verbosity, weather="Light Rain",
        weather_forecast=[{"time_offset_min": 15, "rain_pct": 20}], rain_next_15_pct=20,
        tyre={"compound": "MEDIUM", "age_laps": 5, "wear": [21, 22, 24, 28],
              "inner_temps_c": [92, 94, 96, 98]},
    )
    return store, brain


@pytest.mark.asyncio
async def test_terse_tyre_condition_keeps_the_limiting_tyre(stack, monkeypatch):
    _, brain = await _local_only(stack, monkeypatch)
    answer = await brain.ask("how is my tyre condition")
    assert "rear-right" in answer and "28 percent" in answer


@pytest.mark.asyncio
async def test_terse_start_tyre_answer_keeps_the_tyre_that_was_asked_for(stack, monkeypatch):
    _, brain = await _local_only(stack, monkeypatch)
    answer = await brain.ask("this is a race, what is the first tyre")
    assert answer.startswith("Start on ")
    assert "session locked to Race" in answer


@pytest.mark.asyncio
async def test_terse_weather_answer_keeps_the_crossover_reason(stack, monkeypatch):
    store, brain = await _local_only(stack, monkeypatch)
    reason = "Track is drying through the stint; SOFT is worth 14s over the rest of the race against staying on INTER."
    await store.update(strategy={"weather_crossover": {"reason": reason}})
    answer = await brain.ask("weather update")
    assert "rain risk 20 percent" in answer
    assert "track is drying through the stint" in answer and "SOFT is worth 14s" in answer


@pytest.mark.asyncio
async def test_terse_computed_plan_keeps_its_box_call_after_a_warning(stack, monkeypatch):
    store, brain = await _local_only(stack, monkeypatch)
    instruction = (
        "Warning: the mandatory dry-compound change is still outstanding. "
        "Box for SOFT before the flag or the result is a disqualification."
    )
    await store.update(strategy={"confidence": "high", "recommended": {"instruction": instruction, "legal": False}})
    answer = await brain.ask("what is the strategy")
    assert answer == "Box for SOFT before the flag or the result is a disqualification."


@pytest.mark.asyncio
async def test_race_update_names_the_event_instead_of_its_packet_code(stack, monkeypatch):
    store, brain = await _local_only(stack, monkeypatch, verbosity="standard")
    monkeypatch.setattr(brain, "_defers_to_model", lambda *args: False)
    await store.update(player_car_index=0)
    await store.append_event("PENA", {"type": 4, "infringement": 7, "vehicle_idx": 0, "time": 5})
    answer = await brain.ask("race update")
    assert "PENA" not in answer
    assert "latest event 5-second time penalty for you, corner cutting, gained time" in answer


async def _tow_against_the_trend(store):
    """The gap grew 1.0 -> 2.2 s over two laps; a tow took 0.25 s back in 8 s."""
    history = []
    for k in range(90):
        gap = 1.0 + 1.2 * (k / 89)
        if k >= 86:
            gap -= 0.25 * ((k - 85) / 4)
        history.append({"session_time_s": 3022.0 + 2.0 * k, "lap": 32 + k // 45, "gap_s": -round(gap, 3)})

    def apply(state):
        state.connected = True
        state.player_car_index = 0
        state.player_position = 5
        state.drivers[0].name = "PLAYER"
        state.drivers[0].position = 5
        state.drivers[0].last_lap_ms = 90_400
        rival = state.drivers[1]
        rival.name = "PIASTRI"
        rival.active = True
        rival.position = 4
        rival.last_lap_ms = 89_900
        rival.gap_history = history
        rival.gap_to_player_s = history[-1]["gap_s"]
    await store.mutate(apply)


@pytest.mark.asyncio
async def test_closing_answer_mentions_a_short_term_move_against_the_trend(stack):
    store, database, _, _, _, tools = stack
    await _tow_against_the_trend(store)
    answer = await EngineerBrain(store, tools, database)._fast_answer("am I closing in on the cars in front")
    assert answer.startswith("You are not closing on PIASTRI")
    assert re.search(r"though it shrank 0\.\d over the last \d+ seconds\.$", answer)


# ------------------------------------------------ strategy qualifier and condition


@pytest.mark.asyncio
async def test_terse_full_plan_leads_with_the_call_and_says_each_caveat_once(stack, monkeypatch):
    _, brain = await _scripted(
        stack, monkeypatch, "Copy. Box lap 14 for hards.", verbosity="terse", strategy=_low_confidence_plan(),
    )
    answer = await brain.ask("What is the current pit strategy?")
    assert answer.startswith(
        "Box lap 14 for hards; provisional, confidence low, tyre estimates include track defaults; "
        "the call changes for a safety car"
    )
    assert "Copy" not in answer
    assert answer.casefold().count("provisional") == 1
    assert answer.casefold().count("the call changes") == 1


@pytest.mark.asyncio
async def test_terse_cut_keeps_the_pit_call_over_its_explanation(stack, monkeypatch):
    reply = "Hard wear is only a track default so far. Box lap 14 for hards."
    _, brain = await _scripted(stack, monkeypatch, reply, verbosity="terse", strategy=_low_confidence_plan())
    answer = await brain.ask("What is the current pit strategy?")
    assert answer.startswith("Box lap 14 for hards")


@pytest.mark.asyncio
async def test_a_reply_that_already_hedges_and_conditions_is_not_repeated(stack, monkeypatch):
    reply = (
        "Provisional: box lap 14 for hards, with lap 16 as the alternative. "
        "Hard wear is a track default; a safety car or a wear spike changes it."
    )
    _, brain = await _scripted(stack, monkeypatch, reply, strategy=_low_confidence_plan())
    answer = await brain.ask("What is the current pit strategy?")
    assert answer == reply


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance,reply", [
    ("No, I'm not boxing, I want to stay out.", "Copy, staying out."),
    ("Why is the strategy confidence low?", "Hard wear comes from the track model; there is no long hard run yet."),
])
async def test_a_reply_without_a_pit_call_gets_no_strategy_qualifier(stack, monkeypatch, utterance, reply):
    _, brain = await _scripted(stack, monkeypatch, reply, strategy=_low_confidence_plan())
    assert await brain.ask(utterance) == reply


@pytest.mark.asyncio
async def test_deadline_plan_says_confidence_once_with_spoken_compounds(stack, monkeypatch):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    await store.update(current_lap=6, total_laps=31, mode_profile="race", strategy=_low_confidence_plan())
    brain = EngineerBrain(store, tools, database)

    async def plan_tool():
        return _low_confidence_plan()

    monkeypatch.setattr(tools, "get_pit_strategy", plan_tool)
    answer = await brain._strategy_deadline_answer()
    assert "box lap 14 for hards; alternative: box lap 16 for hards" in answer
    assert answer.count("confidence") == 1
    assert "HARD" not in answer


@pytest.mark.asyncio
async def test_a_volunteered_low_confidence_call_is_qualified_like_an_answered_one(stack, monkeypatch):
    _, brain = await _scripted(stack, monkeypatch, "Box lap 14 for hards.", strategy=_low_confidence_plan())
    spoken = await brain.proactive({"type": "strategy_change", "payload": {"instruction": "Box lap 14 for HARD."}})
    assert spoken == "Box lap 14 for hards; provisional, confidence low, tyre estimates include track defaults."


# --------------------------------------------------------------- briefings


@pytest.mark.asyncio
async def test_spoken_post_race_debrief_is_cut_to_its_length_keeping_the_instruction(stack, monkeypatch):
    reply = (
        "P5 from P8. You gained two places at the start. You lost one in the stops. "
        "Your pace held. Turn 4 cost the most. Next race, brake two metres later into Turn 4."
    )
    captured = []
    _, brain = await _scripted(stack, monkeypatch, reply, captured=captured)
    text = await brain.narrate_briefing("post_race", {"kind": "post_race"})
    assert text == "P5 from P8. Next race, brake two metres later into Turn 4."
    instructions = captured[0]["instructions"]
    assert "at most 2 sentences" in instructions and "no tools" in instructions


@pytest.mark.asyncio
async def test_pre_session_brief_is_not_told_to_be_one_clause(stack, monkeypatch):
    captured = []
    _, brain = await _scripted(stack, monkeypatch, "Brief.", verbosity="terse", captured=captured)
    await brain.narrate_briefing("pre_session", {"kind": "pre_session"})
    instructions = captured[0]["instructions"]
    assert "five to eight concise sentences" in instructions
    assert "Verbosity: terse" not in instructions
    assert "at most 8 sentences" in instructions


@pytest.mark.parametrize("payload,spoken", [
    ({"available": True, "lap_num": 7, "lap_ms": 81712, "delta_to_personal_best_s": 0.0,
      "verdict": {"instruction": "Brake later into turn 1."}},
     "Lap 7, 1:21.712, your best lap so far. Brake later into turn 1."),
    ({"available": True, "lap_num": 8, "lap_ms": 82024, "delta_to_personal_best_s": 0.312,
      "verdict": {"instruction": "Brake later into turn 1."}},
     "Lap 8, 1:22.024, 0.3 off your best. Brake later into turn 1."),
])
def test_qualifying_lap_fallback_reads_like_radio(payload, spoken):
    assert BriefingEngine.fallback_text("post_qualifying_lap", payload) == spoken


@pytest.mark.parametrize("payload,spoken", [
    ({"finish_position": 5, "net_places": 3, "points": 10,
      "top_time_losses": [{"name": "Turn 4", "avg_loss_s": 0.21}]},
     "Race complete: P5, gained 3 places, 10 points. Next session, work on Turn 4: it cost about 0.2 seconds a lap."),
    ({"finish_position": 5, "net_places": None, "points": 10, "top_time_losses": []},
     "Race complete: P5, 10 points. Next session, prioritise repeatable clean laps."),
])
def test_race_fallback_ends_on_an_instruction(payload, spoken):
    assert BriefingEngine.fallback_text("post_race", payload) == spoken
