"""Numbers the engineer says are numbers it was given, and a harness that grades answers.

The unsolicited calls are narrated from a closed set of evidence: the
situation header and the call's own facts, with no tools. A narration that
says a number found nowhere in that evidence is replaced by the call's
deterministic template, and the saved call says so. The answer evaluation
harness (tools/engineer_eval.py) runs driver commands through the real
EngineerBrain.ask against a scripted provider, so the graders themselves are
exercised here with no network.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from pitwall.brain import EngineerBrain, _freshness_clause
from pitwall.config import settings
from pitwall.grounding import SpokenNumber, lap_counts, place_counts, spoken_numbers, unsupported
from pitwall.proactive import ProactiveEngineer
from pitwall.providers import ProviderResult, ProviderRouter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import engineer_eval

FUEL_TEMPLATE = "Fuel is short by 0.4 laps. Start lifting and coasting on the long straights."
INVENTED = (
    "Fuel is short by 1.4 laps; lift and coast into turns 1, 4 and 12 "
    "and save 0.3 kilos a lap."
)


class _Voice:
    is_busy = False
    busy_reason = ""
    realtime_active = False

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak_text(self, text):
        self.spoken.append(text)
        return True


class _Setup:
    async def learn_current_session(self):
        return True


class _Strategy:
    async def recompute(self):
        return {}


async def _narrating_race(stack, monkeypatch, reply):
    """A live race on lap 8 of 20, fuel 0.4 laps short, and a scripted narrator."""
    store, database, _, _, _, tools = stack
    monkeypatch.setattr(settings, "proactive_safe_hold_s", 0.0)
    monkeypatch.setattr(settings, "proactive_narration_enabled", True)
    for group in (1, 2, 6, 7, 10):
        await store.mark_packet(2026, 26, 7, packet_id=group)
    await store.update(
        connected=True, game_paused=False, session_type="Race", mode_profile="race",
        current_lap=8, total_laps=20, player_position=3, fuel_laps_delta=-0.4, ers_pct=62,
        speed_kph=250, throttle=1.0, brake=0.0, lateral_g=0.2, race_control_phase="green",
        track_length_m=5400.0, lap_distance_m=1000.0,
        tyre={"compound": "MEDIUM", "age_laps": 5, "wear": [21, 22, 24, 28],
              "inner_temps_c": [92, 94, 96, 98]},
    )
    await store.mutate(lambda s: s.proactive.update({"enabled": True, "cadence_laps": 99}))
    brain = EngineerBrain(store, tools, database)
    prompts: list[str] = []

    async def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        return ProviderResult(reply, "test", "test-model", 1.0)

    monkeypatch.setattr(brain.router, "generate", generate)
    voice = _Voice()
    engineer = ProactiveEngineer(store, brain, voice, _Setup(), _Strategy())  # type: ignore[arg-type]

    async def no_refresh(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(no_refresh, engineer)
    await engineer._reset_for_session(7)
    return store, database, engineer, voice, prompts


def _saved_call(database, event_type):
    with database._connect() as db:
        return dict(db.execute(
            "SELECT text, delivered, outcome, refusal_reason FROM proactive_calls WHERE event_type=?",
            (event_type,),
        ).fetchone())


# ------------------------------------------------- a narrated unsolicited call


@pytest.mark.asyncio
async def test_a_narration_that_invents_numbers_is_spoken_from_its_template(stack, monkeypatch):
    """The fuel call said 1.4 laps for a 0.4-lap deficit, plus a corner map nobody supplied."""
    store, database, engineer, voice, prompts = await _narrating_race(stack, monkeypatch, INVENTED)
    engineer._enqueue("fuel_warning", {"fuel_laps_delta": -0.4}, critical=True, cooldown_s=0.0)
    await engineer._deliver(await store.snapshot_radio())

    assert prompts, "the model was asked to word the call"
    assert voice.spoken == [FUEL_TEMPLATE]
    saved = _saved_call(database, "fuel_warning")
    assert saved["text"] == FUEL_TEMPLATE and saved["delivered"] == 1
    # The saved call says the template replaced the narration, and why.
    assert "template spoken: narration said 1.4" in saved["refusal_reason"]
    assert "12" in saved["refusal_reason"] and "0.3" in saved["refusal_reason"]
    # What the driver heard is what the conversation remembers.
    radio = (await store.snapshot_analysis())["radio_log"]
    assert radio[-1]["text"] == FUEL_TEMPLATE


@pytest.mark.asyncio
async def test_a_narration_that_keeps_to_its_facts_is_spoken_as_worded(stack, monkeypatch):
    reply = "Fuel is 0.4 laps short with 12 laps to go; lift and coast on the long straights."
    store, database, engineer, voice, _ = await _narrating_race(stack, monkeypatch, reply)
    engineer._enqueue("fuel_warning", {"fuel_laps_delta": -0.4}, critical=True, cooldown_s=0.0)
    await engineer._deliver(await store.snapshot_radio())

    assert voice.spoken == [reply]
    saved = _saved_call(database, "fuel_warning")
    assert "template spoken" not in saved["refusal_reason"]


@pytest.mark.asyncio
async def test_a_call_worded_again_keeps_only_the_note_for_the_words_spoken(stack, monkeypatch):
    """A call requeued after its narration was replaced starts its next wording clean."""
    store, _, engineer, _, _ = await _narrating_race(stack, monkeypatch, INVENTED)
    event = {"type": "fuel_warning", "payload": {"fuel_laps_delta": -0.4},
             "blocked_reasons": ["minimum interval"]}
    state = await store.snapshot_analysis()
    assert await engineer._narrate(event, state) == FUEL_TEMPLATE
    assert event["narration_rejected"].startswith("narration said 1.4")

    reworded = "Fuel is 0.4 laps short; lift and coast."

    async def generate(**kwargs):
        return ProviderResult(reworded, "test", "test-model", 1.0)

    monkeypatch.setattr(engineer.brain.router, "generate", generate)
    assert await engineer._narrate(event, state) == reworded
    assert "narration_rejected" not in event
    assert event["blocked_reasons"] == ["minimum interval"]


@pytest.mark.asyncio
async def test_the_narrator_is_checked_against_exactly_what_it_was_shown(stack, monkeypatch):
    store, _, _, _, _prompts = await _narrating_race(stack, monkeypatch, "Copy.")
    brain = EngineerBrain(store, stack[5], stack[1])
    shown: list[str] = []

    async def generate(**kwargs):
        shown.append(kwargs["prompt"])
        return ProviderResult("Copy.", "test", "test-model", 1.0)

    monkeypatch.setattr(brain.router, "generate", generate)
    await store.append_radio("engineer", "Box lap 15 for hards.")
    event = {"type": "fuel_warning", "payload": {"fuel_laps_delta": -0.4}}
    await brain.proactive(event)

    evidence = event["narration_evidence"]
    # Exactly what the model was shown, less the packet ages that open the
    # header: those say what is stale and are not facts a call may quote.
    ages = _freshness_clause(shown[0])
    assert ages.startswith("FIELD FRESHNESS ") and '"age_s"' in ages
    assert shown[0].replace(ages, "", 1).startswith(evidence + "\n")
    assert "fuel delta -0.4 laps" in evidence
    assert 'PROACTIVE EVENT: {"type": "fuel_warning"' in evidence
    # Earlier radio is context, not evidence: its numbers are history.
    assert "Box lap 15" in shown[0] and "Box lap 15" not in evidence


@pytest.mark.asyncio
async def test_a_failed_narration_is_recorded_on_the_saved_call(stack, monkeypatch):
    store, database, engineer, voice, _ = await _narrating_race(stack, monkeypatch, "unused")

    async def broken(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(engineer.brain.router, "generate", broken)
    engineer._enqueue("fuel_warning", {"fuel_laps_delta": -0.4}, critical=True, cooldown_s=0.0)
    await engineer._deliver(await store.snapshot_radio())

    assert voice.spoken == [FUEL_TEMPLATE]
    assert "template spoken: narration failed" in _saved_call(database, "fuel_warning")["refusal_reason"]


@pytest.mark.asyncio
async def test_a_narration_check_that_cannot_run_still_delivers_the_call(stack, monkeypatch):
    """The call has left the queue by then: a failed check speaks the template, never nothing."""
    store, database, engineer, voice, _ = await _narrating_race(stack, monkeypatch, INVENTED)

    def broken(*args, **kwargs):
        raise ValueError("unreadable evidence")

    monkeypatch.setattr("pitwall.proactive.unsupported", broken)
    engineer._enqueue("fuel_warning", {"fuel_laps_delta": -0.4}, critical=True, cooldown_s=0.0)
    await engineer._deliver(await store.snapshot_radio())

    assert voice.spoken == [FUEL_TEMPLATE]
    saved = _saved_call(database, "fuel_warning")
    assert saved["delivered"] == 1
    assert "template spoken: narration check failed (ValueError)" in saved["refusal_reason"]


@pytest.mark.asyncio
async def test_a_driver_check_that_lost_its_question_says_why_its_template_was_spoken(stack):
    store, database, *_ = stack

    class _Brain:
        def __init__(self):
            self.database = database

        async def proactive(self, event):
            return "Window opens in two laps."

    engineer = ProactiveEngineer(store, _Brain(), object(), object(), object())
    event = {"type": "driver_check", "payload": {"laps_to_window": 2, "box_lap": 8}}
    spoken = await engineer._narrate(event, await store.snapshot_analysis())
    assert spoken.endswith("?")
    assert event["narration_rejected"] == "narration lost its question"


# --------------------------------------------------------- the number check


def _texts(numbers: list[SpokenNumber]) -> list[str]:
    return [number.text for number in numbers]


def test_the_check_reads_the_numbers_a_radio_line_commits_to():
    line = (
        "P4, lap 1:31.2, three tenths off, half a second to the car ahead, "
        "one point four laps of fuel, 48 to 51 percent wear and fourteen to go."
    )
    read = [(number.text, number.value, number.unit) for number in spoken_numbers(line)]
    assert read == [
        ("P4", 4.0, "position"),
        ("1:31.2", 91.2, "laptime"),
        ("three tenths", 0.3, "s"),
        ("half a second", 0.5, "s"),
        ("one point four", 1.4, "laps"),
        ("48", 48.0, "pct"),
        ("51", 51.0, "pct"),
        ("fourteen", 14.0, "laps"),
    ]


@pytest.mark.parametrize("line", [
    "You still owe a second dry compound.",
    "That is the one thing we can change at a stop; one more lap like that.",
    "First stint done, the 2026 regulations allow it.",
])
def test_ordinals_years_and_the_pronoun_one_are_not_read_as_numbers(line):
    assert spoken_numbers(line) == []


@pytest.mark.parametrize("line,evidence", [
    # Rounded to the precision spoken.
    ("Fuel is 0.4 laps short.", {"fuel_laps_delta": -0.38}),
    ("Rear left at 62 percent.", {"wear": [61.7]}),
    # Unit-gated conversions.
    ("Fronts at 198 and 201 F.", {"inner_temps_c": [92, 94]}),
    ("Your best is 1:31.2.", {"best_lap_ms": 91234}),
    ("Rain is 60 percent likely.", {"rain_probability": 0.6}),
    ("About 13 minutes left.", {"session_time_left_s": 754}),
    ("Two tenths off the target.", {"delta_to_target_s": 0.215}),
    # A count the template itself says.
    ("Push for two laps.", "Car behind has stopped. Push for up to two clean laps."),
    # A gap between two lap times in the evidence.
    ("Four tenths off pole.", "SESSION BEST 1:28.000 | PLAYER BEST 1:28.400"),
    # "point four" with no whole number said.
    ("Fuel is point four laps short.", {"fuel_laps_delta": -0.42}),
    # A half is said to the nearest half.
    ("About half a lap short on fuel.", {"fuel_laps_delta": -0.42}),
    ("Two and a half seconds back.", {"gap_s": 2.34}),
    # A decade covers its ten.
    ("Rear left at 71, the rest in the mid-50s to high 60s.", {"wear": [55.2, 56.1, 71.3, 66.0]}),
])
def test_numbers_backed_by_the_evidence_pass(line, evidence):
    assert _texts(unsupported(line, evidence)) == []


@pytest.mark.parametrize("line,evidence,invented", [
    ("About half a lap short on fuel.", {"fuel_laps_delta": -0.9}, ["half a lap"]),
    ("Two and a half seconds back.", {"gap_s": 2.9}, ["Two and a half"]),
    ("The fronts are in the high 70s.", {"wear": [55.2, 66.0]}, ["70s"]),
    ("Fuel is point six laps short.", {"fuel_laps_delta": -0.42}, ["point six"]),
])
def test_rounded_wording_still_has_to_round_from_the_evidence(line, evidence, invented):
    assert _texts(unsupported(line, evidence)) == invented


def test_point_as_a_word_is_not_a_decimal():
    assert _texts(spoken_numbers("At this point two cars have stopped.")) == ["two"]


def test_counts_of_laps_and_places_the_line_works_out_pass_but_not_bare_numbers():
    laps = lap_counts(8, 20, {"box_lap": 14})
    assert _texts(unsupported("Twelve laps to go.", "LAP 8/20", laps=laps)) == []
    assert _texts(unsupported("Thirteen to go, counting this one.", "LAP 8/20", laps=laps)) == []
    assert _texts(unsupported("Box in 6 laps.", "LAP 8/20", laps=laps)) == []
    # Twelve as a corner number is not a lap count.
    assert _texts(unsupported("Lift into turn 12.", "LAP 8/20", laps=laps)) == ["12"]
    places = place_counts(18, {"active_cars": 20})
    assert _texts(unsupported("P18, two cars behind you.", "P18", places=places)) == []
    assert _texts(unsupported("P18, five cars behind you.", "P18", places=places)) == ["five"]


def test_invented_numbers_are_found():
    evidence = 'fuel delta -0.4 laps | PROACTIVE EVENT: {"facts": {"fuel_laps_delta": -0.4}}'
    assert _texts(unsupported(INVENTED, evidence, FUEL_TEMPLATE)) == ["1.4", "1", "4", "12", "0.3"]


# ------------------------------------------------------ the answer harness


def _case(case_id):
    return next(case for case in engineer_eval.load_manifest()["cases"] if case["id"] == case_id)


@pytest.mark.asyncio
async def test_the_answer_harness_runs_every_manifest_case_offline(monkeypatch):
    async def no_network(*args, **kwargs):
        pytest.fail("the offline harness must never reach a provider")

    monkeypatch.setattr(ProviderRouter, "generate", no_network)
    cases = engineer_eval.load_manifest()["cases"]
    rows = await engineer_eval.run_cases(cases, [engineer_eval.SCRIPTED])
    failures = {row["case"]: (row["answer"], row["checks"], row["error"]) for row in rows if not row["passed"]}
    assert failures == {}
    assert {row["case"]: row["ungrounded"] for row in rows if row["ungrounded"]} == {}
    summary = engineer_eval.summarize(rows)[engineer_eval.SCRIPTED]
    assert summary["cases"] == summary["passed"] == len(cases)
    # Both paths are exercised: commands answered locally and by the provider.
    assert 0 < summary["answered_locally"] < len(cases)


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,reply,problem", [
    (
        "tyre-situation-scenario",
        "Hard tyres are well used: 48–51% wear, with balanced inner temperatures around 99–100°C. "
        "Fourteen laps remain, so manage them cleanly; they should be serviceable to the flag.",
        "endorses finishing on the current set",
    ),
    (
        "stay-out-to-the-end",
        "Yes, you can hold him for now. Gap is steady at 3.6 seconds; protect your exits and "
        "don't compromise the line until he closes.",
        "affirms staying out to the end",
    ),
])
async def test_the_harness_flags_a_stay_out_the_engine_says_is_not_legal(case_id, reply, problem):
    """The published Hungaroring transcript: hards 'serviceable to the flag' while a stop is owed."""
    case = {**_case(case_id), "scripted": {"tools": [["get_pit_strategy", {}]], "reply": reply}}
    app = engineer_eval._app_modules(engineer_eval.REPO)
    row = await engineer_eval.run_case(app, case, engineer_eval.SCRIPTED)
    assert not row["passed"]
    assert any(problem in item for item in row["checks"]["verdict_problems"]), row["checks"]


@pytest.mark.asyncio
async def test_the_harness_reports_a_box_lap_the_model_was_never_shown():
    # 56 is a lap no plan shown for this scenario names. Pick another if the
    # planner's shortlist comes to include it.
    case = {**_case("box-or-stay"), "scripted": {"tools": [["get_pit_strategy", {}]],
                                                 "reply": "Box lap 56 for softs."}}
    app = engineer_eval._app_modules(engineer_eval.REPO)
    row = await engineer_eval.run_case(app, case, engineer_eval.SCRIPTED)
    assert row["ungrounded"] == ["56"]
    assert not row["passed"] and row["checks"]["required_missing"]


def test_the_harness_grades_a_fact_in_the_unit_it_was_asked_for():
    context = {"state": {"tyre": {"inner_temps_c": [92, 94, 96, 98]}}, "tool": {}}
    asked = [{"path": "state.tyre.inner_temps_c", "unit": "temp_f"}]
    assert engineer_eval._check_required("Front 198/201 F; rear 205/208 F.", asked, context) == []
    assert engineer_eval._check_required("Front 92/94 C; rear 96/98 C.", asked, context) != []


def test_a_stay_out_the_answer_rules_out_is_not_an_endorsement():
    context = {"state": {}, "tool": {"get_pit_strategy": {"compound_rule": {
        "applies": True, "compliant": True, "conditional_on_future_use": True}}}}
    question = "Can I stay out to the end?"
    verdict = {"yes_no": "either", "compound_rule": True}
    assert engineer_eval._check_verdict(
        verdict, question, "No, you cannot stay out to the end; box lap 62 for softs.", context
    ) == []
    assert engineer_eval._check_verdict(
        verdict, question, "You can't run to the flag on these; box lap 62.", context
    ) == []
    assert engineer_eval._check_verdict(
        verdict, question, "Yes, stay out to the end and defend.", context
    ) != []


def test_a_negation_elsewhere_in_the_sentence_does_not_undo_an_endorsement():
    context = {"state": {}, "tool": {"get_pit_strategy": {"compound_rule": {
        "applies": True, "compliant": True, "conditional_on_future_use": True}}}}
    verdict = {"compound_rule": True}
    question = "How are the tyres?"
    assert engineer_eval._check_verdict(
        verdict, question, "The hards are good to the flag, no worries.", context
    ) == ["endorses finishing on the current set while the compound rule needs a stop"]
    assert engineer_eval._check_verdict(
        verdict, question, "They will not last to the flag; box lap 62 for softs.", context
    ) == []


@pytest.mark.parametrize("argv", [["--provider", "openai"], ["--live", "--provider", "bogus"]])
def test_the_harness_calls_a_real_provider_only_when_asked_to_by_name(argv):
    with pytest.raises(SystemExit):
        engineer_eval.main(argv)


@pytest.mark.asyncio
async def test_packet_ages_are_not_evidence_for_a_narrated_number(stack, monkeypatch):
    """The header's packet ages (e.g. 0.3 s) must not ground an invented 0.3."""
    store, _database, engineer, _voice, prompts = await _narrating_race(stack, monkeypatch, INVENTED)
    engineer._enqueue("fuel_warning", {"fuel_laps_delta": -0.4}, critical=True, cooldown_s=0.0)
    event = engineer._select(await store.snapshot_radio())
    await engineer.brain.proactive(event)
    assert "FIELD FRESHNESS" in prompts[-1], "the model still sees which data is stale"
    assert "FIELD FRESHNESS" not in event["narration_evidence"]
    assert "age_s" not in event["narration_evidence"]
