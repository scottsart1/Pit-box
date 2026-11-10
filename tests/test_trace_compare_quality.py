"""What an engineer answer leaves behind, "say again", and the provider A/B.

Every answer the text radio gives is saved with a bounded trace of how it was
produced (route, provider, model, tool calls and results, the provider's own
text and each post-processing step), labelled with the provider that actually
answered. The driver's reaction to an answer (a repeat request, a correction,
a dispute) is labelled on the driver's own saved line. "Say again" replays the
missed line word for word. The provider comparison sends the request ask()
sends and shows each reply both raw and as the radio would speak it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from pitwall import app as app_module
from pitwall import database as database_module
from pitwall.brain import EngineerBrain
from pitwall.config import Settings
from pitwall.database import PitWallDatabase
from pitwall.migrations import MIGRATIONS
from pitwall.providers import (
    ProviderFailure,
    ProviderRequestError,
    ProviderResult,
    ProviderRouter,
)

UID = 54321


async def _race(stack, **overrides):
    store, database, _, _, _, tools = stack
    for group in (1, 2, 5, 6, 7, 10, 16):
        await store.mark_packet(2026, 26, UID, packet_id=group)
    await store.update(
        **{
            "regulations_2026": True,
            "session_type": "Race",
            "mode_profile": "race",
            "current_lap": 8,
            "total_laps": 20,
            "player_position": 3,
            "player_car_index": 0,
            "fuel_laps_delta": -0.4,
            "fuel_kg": 25,
            "ers_pct": 62,
            "weather": "Clear",
            "tyre": {
                "compound": "MEDIUM",
                "age_laps": 5,
                "wear": [21, 22, 24, 28],
                "inner_temps_c": [92, 94, 96, 98],
            },
            **overrides,
        }
    )
    return store, database, tools, EngineerBrain(store, tools, database)


def _rows(database) -> list[dict[str, Any]]:
    with sqlite3.connect(database.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM radio_messages ORDER BY id")]


def _provider(text: str, name: str = "openai", calls: tuple[tuple[str, dict[str, Any]], ...] = ()):
    async def generate(**kwargs):
        for tool, arguments in calls:
            await kwargs["execute_tool"](tool, arguments)
        return ProviderResult(text, name, f"{name}-model", 900.0, tool_rounds=len(calls))

    return generate


# ---------------------------------------------------------------------------
# The trace of an answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_capped_answer_keeps_the_providers_own_words_and_its_tool_evidence(stack, monkeypatch):
    """Terse radio cuts the verdict; the stored trace shows that the cap did it.

    "Can I attack the car ahead next lap?" was answered "Gap is 1.2 seconds and
    has been stable for three laps. No, do not attack yet; ..." and the
    one-sentence cap delivered only the gap. Only the cut line was stored, so
    nobody could tell whether the model or the cap lost the answer, or whether
    the gap came from a tool.
    """
    _, database, _, brain = await _race(stack, radio_verbosity="terse")
    said = (
        "Gap is 1.2 seconds and has been stable for three laps. "
        "No, do not attack yet; wait for the battery to reach 80 percent."
    )
    monkeypatch.setattr(brain.router, "generate", _provider(said, calls=(("get_ers_report", {}),)))

    heard = await brain.ask("Can I attack the car ahead next lap?")

    assert heard == "Gap is 1.2 seconds and has been stable for three laps."
    engineer = _rows(database)[-1]
    assert engineer["role"] == "engineer" and engineer["text"] == heard
    assert engineer["source"] == "response:openai"
    trace = json.loads(engineer["trace_json"])
    assert trace["path"] == "model"
    assert trace["route"] == brain.classify_request("Can I attack the car ahead next lap?")
    assert (trace["provider"], trace["model"]) == ("openai", "openai-model")
    assert trace["raw_text"] == said
    assert trace["postprocess"] == [{"step": "sentence_cap", "text": heard}]
    assert trace["final_text"] == heard
    assert "DRIVER: Can I attack the car ahead next lap?" in trace["prompt"]
    [call] = trace["tool_calls"]
    assert call["name"] == "get_ers_report" and call["arguments"] == {}
    assert call["result"]["store_pct"] == 62


@pytest.mark.asyncio
async def test_the_stored_provider_is_the_one_that_answered_not_a_concurrent_narration(stack, monkeypatch):
    """A proactive narration from another provider finishing while the reply
    is recorded no longer relabels the reply."""
    store, database, _, brain = await _race(stack)

    async def generate(**kwargs):
        if "PROACTIVE EVENT" in kwargs["prompt"]:
            return ProviderResult("Rear-right is the limiting tyre at 28 percent.", "openai", "gpt", 1.0)
        return ProviderResult("Gap behind is 1.4 seconds and stable.", "deepseek", "deepseek-chat", 1.0)

    monkeypatch.setattr(brain.router, "generate", generate)
    append = store.append_radio

    async def narration_finishes_meanwhile(role, text, **kwargs):
        if role == "engineer" and kwargs.get("source") == "reply":
            await brain.proactive({"type": "tyre_wear", "payload": {"wear_fl_fr_rl_rr": [21, 22, 24, 28]}})
        return await append(role, text, **kwargs)

    monkeypatch.setattr(store, "append_radio", narration_finishes_meanwhile)

    await brain.ask("How is the car behind doing on his tyres compared to me?")

    engineer = _rows(database)[-1]
    assert engineer["text"] == "Gap behind is 1.4 seconds and stable."
    assert engineer["source"] == "response:deepseek"
    assert json.loads(engineer["trace_json"])["provider"] == "deepseek"


@pytest.mark.asyncio
async def test_a_huge_tool_result_is_stored_bounded_with_its_size_and_digest(stack, monkeypatch):
    _, database, tools, brain = await _race(stack)
    huge = {"laps": [{"lap": index, "note": "x" * 90} for index in range(800)]}
    encoded = json.dumps(huge, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) > 75_000

    async def call(name, arguments):
        return huge

    monkeypatch.setattr(tools, "call", call)
    monkeypatch.setattr(
        brain.router,
        "generate",
        _provider("Your long run held its pace.", calls=tuple(("get_session_summary", {"n": n}) for n in range(20))),
    )

    await brain.ask("Explain how my long run went compared to the short run.")

    stored = _rows(database)[-1]["trace_json"]
    assert len(stored) < 40_000
    trace = json.loads(stored)
    assert len(trace["tool_calls"]) == 12 and trace["tool_calls_omitted"] == 8
    first = trace["tool_calls"][0]
    assert "result" not in first
    assert first["result_chars"] == len(encoded)
    assert first["result_sha256"] == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert len(first["result_preview"]) <= 3_000
    assert "test-openai-key" not in stored and "test-deepseek-key" not in stored


@pytest.mark.asyncio
async def test_a_provider_fallback_is_stored_as_local_with_the_failure(stack, monkeypatch):
    store, database, _, brain = await _race(stack)
    plan = {
        "feasible": True, "legal": True, "box_laps": [12], "compounds": ["MEDIUM", "HARD"],
        "box_lap": 12, "fit_compound": "HARD", "instruction": "Box lap 12 for HARD.",
    }
    await store.update(strategy={"recommended": plan, "plans": [plan], "confidence": "high"})

    async def fail(**kwargs):
        raise ProviderRequestError([ProviderFailure("openai", "deadline", "test deadline")])

    monkeypatch.setattr(brain.router, "generate", fail)

    answer = await brain.ask("Give me the full pit strategy and its best alternative.")

    engineer = _rows(database)[-1]
    assert engineer["text"] == answer and engineer["source"] == "response:local"
    trace = json.loads(engineer["trace_json"])
    assert trace["path"] == "provider_fallback"
    assert (trace["provider"], trace["model"], trace["failure"]) == ("local", "strategy-deadline-fallback", "deadline")
    assert trace["provider_error"] == "ProviderRequestError"
    assert trace["final_text"] == answer


@pytest.mark.asyncio
async def test_a_deterministic_answer_is_stored_with_a_local_trace(stack, monkeypatch):
    _, database, _, brain = await _race(stack)

    async def no_provider(**kwargs):
        pytest.fail("A plain lookup is answered from telemetry")

    monkeypatch.setattr(brain.router, "generate", no_provider)

    answer = await brain.ask("What's the weather?")

    engineer = _rows(database)[-1]
    assert engineer["source"] == "deterministic_response"
    trace = json.loads(engineer["trace_json"])
    assert trace["path"] == "deterministic"
    assert (trace["provider"], trace["model"]) == ("local", "deterministic-radio")
    assert trace["final_text"] == answer


@pytest.mark.asyncio
async def test_history_lists_which_lines_carry_a_trace_without_the_trace(stack, monkeypatch):
    _, database, _, brain = await _race(stack)
    monkeypatch.setattr(brain.router, "generate", _provider("Gap behind is 1.4 seconds and stable."))
    await brain.ask("How is the car behind doing on his tyres compared to me?")

    radio = (await database.history_query(session_uid=UID))["radio"]

    assert all("trace_json" not in row for row in radio)
    assert {row["role"]: row["has_trace"] for row in radio} == {"driver": False, "engineer": True}


@pytest.mark.asyncio
async def test_the_trace_endpoint_returns_one_answers_evidence(stack, monkeypatch):
    _, database, _, brain = await _race(stack)
    monkeypatch.setattr(brain.router, "generate", _provider("Gap behind is 1.4 seconds and stable."))
    await brain.ask("How is the car behind doing on his tyres compared to me?")
    engineer = _rows(database)[-1]
    monkeypatch.setattr(app_module, "database", database)
    client = TestClient(app_module.app)

    response = client.get(f"/api/radio/{engineer['id']}/trace")

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Gap behind is 1.4 seconds and stable."
    assert body["session_uid"] == UID
    assert body["trace"]["provider"] == "openai"
    assert "trace_json" not in body
    assert client.get("/api/radio/999999/trace").status_code == 404
    assert client.get("/api/radio/0/trace").status_code == 404
    assert client.get(f"/api/radio/{2**70}/trace").status_code == 404


@pytest.mark.asyncio
async def test_a_database_from_before_traces_migrates_and_stays_readable(tmp_path, monkeypatch):
    path = tmp_path / "pitwall.sqlite3"
    monkeypatch.setattr(
        database_module, "MIGRATIONS", tuple(item for item in MIGRATIONS if item.version != 5600)
    )
    await PitWallDatabase(path).initialize()
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(radio_messages)")}
        assert {"label", "trace_json"}.isdisjoint(columns)
        db.execute(
            "INSERT INTO radio_messages(session_uid, track_id, lap_num, role, text, source, created_at) "
            "VALUES (7, 12, 3, 'engineer', 'Box lap 12 for hards.', 'response:openai', 1.0)"
        )
    monkeypatch.undo()

    database = PitWallDatabase(path)
    await database.initialize()

    [old] = (await database.history_query(session_uid=7))["radio"]
    assert old["text"] == "Box lap 12 for hards." and old["label"] == "" and old["has_trace"] is False
    assert (await database.radio_trace(old["id"]))["trace"] is None
    new_id = await database.save_radio_message(
        {"session_uid": 7, "track_id": 12, "current_lap": 4}, "engineer", "Copy.", "x", trace={"path": "model"}
    )
    assert (await database.radio_trace(new_id))["trace"] == {"path": "model"}


# ---------------------------------------------------------------------------
# The driver's reaction, labelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_corrections_repeat_requests_and_disputes_are_labelled(stack, monkeypatch):
    _, database, _, brain = await _race(stack)

    async def model(*args, **kwargs):
        return "Copy."

    monkeypatch.setattr(brain, "_run", model)
    turns = {
        "That's wrong, I'm on hards.": "correction",
        "Say again?": "repeat_request",
        "That's not what I asked.": "off_target",
        "You already told me that.": "redundant",
        "That strategy is stupid.": "dispute",
        "Are you sure about that call?": "dispute",
        "That strategy works.": "",
        "How is the car behind doing on his tyres compared to me?": "",
    }
    for utterance in turns:
        await brain.ask(utterance)

    labels = {row["text"]: row["label"] for row in _rows(database) if row["role"] == "driver"}
    assert labels == turns
    # Engineer lines carry no reaction label.
    assert {row["label"] for row in _rows(database) if row["role"] == "engineer"} == {""}


# ---------------------------------------------------------------------------
# "Say again"
# ---------------------------------------------------------------------------


def _no_model(monkeypatch, brain):
    async def no_provider(*args, **kwargs):
        pytest.fail("A repeat request replays the last line without a model call")

    monkeypatch.setattr(brain, "_run", no_provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_words", ["Say again?", "Sorry, say that again please", "Mark, repeat that", "Come again?", "I didn't catch that"])
async def test_say_again_replays_the_last_engineer_line_verbatim(stack, monkeypatch, request_words):
    store, database, _, brain = await _race(stack)
    await store.append_radio("driver", "How are the tyres?")
    await store.append_radio("engineer", "Rear-right is the limiting tyre at 28 percent.", source="reply")
    _no_model(monkeypatch, brain)

    answer = await brain.ask(request_words)

    assert answer == "Rear-right is the limiting tyre at 28 percent."
    state = await store.snapshot_analysis()
    assert state["radio_log"][-1]["text"] == answer
    assert state["llm_model"] == "radio-repeat"
    engineer = _rows(database)[-1]
    assert engineer["source"] == "deterministic_repeat"
    assert json.loads(engineer["trace_json"])["path"] == "repeat"


@pytest.mark.asyncio
async def test_say_again_replays_an_unsolicited_call_the_driver_missed(stack, monkeypatch):
    _, _, _, brain = await _race(stack)
    await brain.record_spoken_call("Hamilton has pitted from P2, he is on hards.")
    _no_model(monkeypatch, brain)

    assert await brain.ask("Say again?") == "Hamilton has pitted from P2, he is on hards."


@pytest.mark.parametrize(
    "setup",
    [
        "previous_lap",       # "box this lap" from the last lap means a lap already gone
        "red_flag",           # the line may predate the flag
        "race_control_change",
        "partly_heard",       # the speech radio cut the line off
        "no_engineer_line",
    ],
)
@pytest.mark.asyncio
async def test_say_again_goes_to_the_model_when_the_line_may_no_longer_hold(stack, monkeypatch, setup):
    store, _, _, brain = await _race(stack)
    if setup == "previous_lap":
        await store.update(current_lap=7)
        await store.append_radio("engineer", "Box this lap for hards.", source="reply")
        await store.update(current_lap=8)
    elif setup == "partly_heard":
        await store.append_radio("engineer", "Box this lap for —")
    elif setup != "no_engineer_line":
        await store.append_radio("engineer", "Box this lap for hards.", source="reply")
    if setup == "red_flag":
        await store.update(red_flag_active=True, race_control_phase="red_flag", race_control_changed_at=time.time())
    if setup == "race_control_change":
        await store.update(race_control_phase="green", race_control_changed_at=time.time() - 30)
    called = []

    async def model(*args, **kwargs):
        called.append(args[0])
        return "Model answer."

    monkeypatch.setattr(brain, "_run", model)

    assert await brain.ask("Say again?") == "Model answer."
    assert called


@pytest.mark.asyncio
async def test_say_again_with_a_subject_asks_for_a_fresh_answer(stack, monkeypatch):
    store, _, _, brain = await _race(stack)
    await store.append_radio("engineer", "Rear-right is the limiting tyre at 28 percent.", source="reply")

    async def model(*args, **kwargs):
        return "Gap behind is 1.4 seconds and stable."

    monkeypatch.setattr(brain, "_run", model)

    assert await brain.ask("Say again how the car behind compares to me") == "Gap behind is 1.4 seconds and stable."


# ---------------------------------------------------------------------------
# The provider comparison
# ---------------------------------------------------------------------------


async def _capture_both(brain, monkeypatch, reply: str) -> dict[str, dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}

    async def header(*, include_strategy: bool) -> str:
        return f"SITUATION HEADER strategy={include_strategy}"

    async def generate(**kwargs):
        seen["ask"] = kwargs
        return ProviderResult(reply, "openai", "openai-model", 1.0)

    async def compare(**kwargs):
        seen["compare"] = kwargs
        return {"results": {"openai": {"available": True, "reply": reply}}, "shared_tool_results": 0}

    monkeypatch.setattr(brain, "_header", header)
    monkeypatch.setattr(brain.router, "generate", generate)
    monkeypatch.setattr(brain.router, "compare", compare)
    return seen


@pytest.mark.asyncio
async def test_compare_sends_the_request_ask_sends(stack, monkeypatch):
    """After a strategy exchange, with terse radio and a standing instruction,
    the comparison used to drop the strategy turns, the verbosity and the
    standing rule and offer every tool; both providers looked broken on
    "please answer my question" while in the car the question was there."""
    store, _, _, brain = await _race(
        stack,
        radio_verbosity="terse",
        standing_instructions=[{"rule": "Never mention the weather unless I ask."}],
    )
    for role, text in (
        ("driver", "what's the strategy"),
        ("engineer", "Box lap 14 for hards."),
        ("driver", "why that lap"),
        ("engineer", "Undercut on Alonso."),
    ):
        await store.append_radio(role, text)
    seen = await _capture_both(brain, monkeypatch, "Undercut on Alonso is worth it.")
    utterance = "please answer my question"

    comparison = await brain.compare(utterance)
    await brain.ask(utterance)

    asked, compared = seen["ask"], seen["compare"]
    assert compared["prompt"] == asked["prompt"]
    assert "ENGINEER: Box lap 14 for hards." in compared["prompt"]
    assert "DRIVER: what's the strategy" in compared["prompt"]
    assert compared["instructions"] == asked["instructions"]
    assert "Never mention the weather unless I ask." in compared["instructions"]
    assert (compared["route"], compared["effort"], compared["max_rounds"]) == (
        asked["route"], asked["effort"], asked["max_rounds"],
    )
    asked_tools = [tool["name"] for tool in asked["tools"]]
    assert [tool["name"] for tool in compared["tools"]] == [
        name for name in asked_tools if name != "record_lap_observation"
    ]
    assert comparison["route"] == asked["route"]


@pytest.mark.asyncio
async def test_compare_returns_the_raw_reply_and_what_the_radio_would_say(stack, monkeypatch):
    _, _, _, brain = await _race(stack, radio_verbosity="terse")
    reply = "Car behind is 1.4 seconds back. He is on fresher tyres. Defend into turn 1."
    await _capture_both(brain, monkeypatch, reply)
    utterance = "How is the car behind doing on his tyres compared to me?"

    comparison = await brain.compare(utterance)
    heard = await brain.ask(utterance)

    result = comparison["results"]["openai"]
    assert result["reply"] == reply
    assert result["as_spoken"] == heard == "Car behind is 1.4 seconds back."
    assert result["postprocess"] == ["sentence_cap"]
    assert comparison["fast_path"] is False


@pytest.mark.asyncio
async def test_compare_qualifies_a_low_confidence_pit_call_as_the_radio_does(stack, monkeypatch):
    store, _, _, brain = await _race(stack, radio_verbosity="terse")
    plan = {
        "feasible": True, "legal": True, "box_laps": [12], "compounds": ["MEDIUM", "HARD"],
        "box_lap": 12, "fit_compound": "HARD", "instruction": "Box lap 12 for HARD.",
    }
    await store.update(strategy={"recommended": plan, "plans": [plan], "confidence": "low"})
    reply = "The undercut is worth about a second. Box lap 12 for hards."
    await _capture_both(brain, monkeypatch, reply)
    utterance = "Is the undercut worth it, or should we go longer?"

    comparison = await brain.compare(utterance)
    heard = await brain.ask(utterance)

    result = comparison["results"]["openai"]
    assert "provisional, confidence low" in heard
    assert result["as_spoken"] == heard
    assert result["reply"] == reply


@pytest.mark.asyncio
async def test_compare_says_when_the_radio_answers_without_a_model(stack, monkeypatch):
    _, _, _, brain = await _race(stack)
    await _capture_both(brain, monkeypatch, "Clear, no rain expected.")

    assert (await brain.compare("What's the weather?"))["fast_path"] is True


@pytest.mark.asyncio
async def test_compare_keeps_its_cooldown_and_tool_exclusions(stack, monkeypatch):
    _, _, _, brain = await _race(stack, radio_verbosity="terse")
    offered: dict[str, list[str]] = {}

    class Provider:
        available = True

        def __init__(self, name: str) -> None:
            self.name = name

        async def generate(self, **kwargs):
            offered[self.name] = [tool["name"] for tool in kwargs["tools"]]
            blocked = await kwargs["execute_tool"]("generate_setup", {})
            gap = await kwargs["execute_tool"]("get_ers_report", {})
            return ProviderResult(
                f"{blocked['error']} Battery at {gap['store_pct']} percent. Third sentence.",
                self.name, f"{self.name}-model", 1.0, tool_rounds=1,
            )

    brain.router = ProviderRouter(
        Settings(
            _env_file=None, OPENAI_API_KEY="openai-test", DEEPSEEK_API_KEY="deepseek-test",
            llm_compare_enabled=True, llm_compare_cooldown_s=60,
        ),
        providers={"deepseek": Provider("deepseek"), "openai": Provider("openai")},  # type: ignore[arg-type]
    )

    comparison = await brain.compare("Explain how my energy deployment compares to the car ahead.")

    for name in ("deepseek", "openai"):
        assert "record_lap_observation" not in offered[name]
        result = comparison["results"][name]
        assert "disabled during A/B comparison" in result["reply"]
        assert len(result["as_spoken"].split(". ")) == 1
    assert [call["name"] for call in comparison["tool_calls"]] == ["get_ers_report"]
    with pytest.raises(Exception, match="cooling down"):
        await brain.compare("Explain how my energy deployment compares to the car ahead.")


# ---------------------------------------------------------------------------
# What the comparison says the radio does, and the edges of a replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "utterance", ["Should I box now?", "what's the strategy", "do we box this lap", "any strategy updates"]
)
async def test_compare_flags_a_pit_question_the_radio_answers_from_the_plan(stack, monkeypatch, utterance):
    """The bare pit and plan questions, the ones most worth comparing, are
    answered in the car from the ranked plan; the comparison has to say so."""
    store, _, _, brain = await _race(stack)
    plan = {
        "feasible": True, "legal": True, "box_laps": [12], "compounds": ["MEDIUM", "HARD"],
        "box_lap": 12, "fit_compound": "HARD", "instruction": "Box lap 12 for HARD.",
    }
    await store.update(strategy={"recommended": plan, "plans": [plan], "confidence": "high"})
    await _capture_both(brain, monkeypatch, "Box lap 12 for hards.")

    assert (await brain.compare(utterance))["fast_path"] is True

    async def no_provider(**kwargs):
        pytest.fail("The radio answers this from the plan")

    monkeypatch.setattr(brain.router, "generate", no_provider)
    assert await brain.ask(utterance)


@pytest.mark.asyncio
async def test_compare_flags_the_red_flag_restart_summary(stack, monkeypatch):
    """Under a red flag a request for the whole plan, which otherwise goes to
    the model, is answered with the restart summary."""
    from f1.packets import PacketEventData

    from pitwall.udp import F1DatagramProtocol

    store, database, strategy, _, _, tools = stack
    await store.update(connected=True, session_uid=123, mode_profile="race", session_type="Race",
                       current_lap=10, total_laps=18, track_id=0, player_position=1, active_cars=1)

    def arrange(state):
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 10
        state.tyre.wear = [65] * 4
        state.completed_laps = [{"lap_num": 1, "compound": "HARD", "valid": True, "lap_time_ms": 90000}]
        state.tyre_sets = [
            {"index": index, "compound": compound, "available": True,
             "wear_pct": 0, "usable_life_laps": 40, "life_span_laps": 40}
            for index, compound in enumerate(("SOFT", "MEDIUM", "HARD"))
        ]

    await store.mutate(arrange)
    red_flag = PacketEventData()
    red_flag.event_string_code[:] = [ord(char) for char in "RDFL"]
    await F1DatagramProtocol(store).handle_PacketEventData(red_flag)
    await store.update(game_paused=True)
    await strategy.recompute()
    brain = EngineerBrain(store, tools, database)
    await _capture_both(brain, monkeypatch, "Fresh mediums for the restart.")

    assert (await brain.compare("give me the full strategy"))["fast_path"] is True
    assert (await brain.compare("why would the hard be slower at the restart"))["fast_path"] is False


@pytest.mark.asyncio
async def test_say_again_after_an_unanswered_question_goes_to_the_model(stack, monkeypatch):
    """The driver asked something that got no answer: "say again" wants that
    answer, not the line said before the question."""
    store, _, _, brain = await _race(stack)
    await store.append_radio("engineer", "Rear-right is the limiting tyre at 28 percent.", source="reply")
    await store.append_radio("driver", "What's the gap to Norris?")
    called = []

    async def model(*args, **kwargs):
        called.append(args[0])
        return "Norris is 2.1 seconds ahead."

    monkeypatch.setattr(brain, "_run", model)

    assert await brain.ask("Say again?") == "Norris is 2.1 seconds ahead."
    assert called


@pytest.mark.asyncio
@pytest.mark.parametrize(("lap_elapsed_ms", "replayed"), [(150_000, False), (60_000, True)])
async def test_say_again_after_a_flag_earlier_in_a_slow_lap_goes_to_the_model(
    stack, monkeypatch, lap_elapsed_ms, replayed
):
    """A lap behind the safety car outlasts the two-minute window: a line from
    earlier in that lap may predate the flag. Once the flag is older than the
    lap, the line was said after it and is replayed."""
    store, _, _, brain = await _race(stack)
    line = "Gap ahead is 1.2 seconds, push now."
    await store.append_radio("engineer", line, source="reply")
    await store.update(
        race_control_phase="safety_car",
        race_control_changed_at=time.time() - 130,
        current_lap_time_ms=lap_elapsed_ms,
    )

    async def model(*args, **kwargs):
        return "Safety car, hold position."

    monkeypatch.setattr(brain, "_run", model)

    assert await brain.ask("Say again?") == (line if replayed else "Safety car, hold position.")


@pytest.mark.asyncio
async def test_car_feedback_is_not_labelled_as_a_reaction_to_the_last_answer(stack, monkeypatch):
    """Verdict words count as a dispute only when aimed at what the engineer
    said; a complaint about the car is feedback, not a verdict on the answer."""
    _, database, _, brain = await _race(stack)

    async def model(*args, **kwargs):
        return "Copy."

    monkeypatch.setattr(brain, "_run", model)
    turns = {
        "The rear is terrible in the slow corners.": "",
        "I hate this car.": "",
        "I'm not happy with the balance.": "",
        "I missed that apex.": "",
        "That's ridiculous.": "dispute",
        "I disagree.": "dispute",
        "Not happy with that plan.": "dispute",
        "Can you repeat that please": "repeat_request",
    }
    for utterance in turns:
        await brain.ask(utterance)

    labels = {row["text"]: row["label"] for row in _rows(database) if row["role"] == "driver"}
    assert labels == turns


@pytest.mark.asyncio
async def test_a_long_prompt_keeps_the_drivers_line_in_the_trace(stack, monkeypatch):
    """A long header gives way in the stored prompt; the request focus, the
    recent radio and the driver's line, what the answer is judged against,
    are kept."""
    store, database, _, brain = await _race(stack)
    await store.append_radio("engineer", "Box lap 14 for hards.", source="reply")

    async def header(*, include_strategy: bool) -> str:
        return "SITUATION " + "x" * 9_000

    monkeypatch.setattr(brain, "_header", header)
    monkeypatch.setattr(brain.router, "generate", _provider("Gap behind is 1.4 seconds and stable."))

    await brain.ask("How is the car behind doing on his tyres compared to me?")

    prompt = json.loads(_rows(database)[-1]["trace_json"])["prompt"]
    assert prompt.startswith("SITUATION xxx")
    assert "ENGINEER: Box lap 14 for hards." in prompt
    assert prompt.endswith("DRIVER: How is the car behind doing on his tyres compared to me?")
    assert len(prompt) < 6_100


@pytest.mark.asyncio
async def test_the_session_export_carries_the_traces_it_flags(stack):
    _, database, _, _, _, _ = stack
    trace = {"path": "model", "raw_text": "Gap is 1.2 seconds. No, do not attack yet.",
             "postprocess": [{"step": "sentence_cap", "text": "Gap is 1.2 seconds."}]}
    await database.save_radio_message(
        {"session_uid": 7, "track_id": 12, "current_lap": 4}, "engineer", "Gap is 1.2 seconds.", "x", trace=trace
    )
    listed = (await database.history_query(session_uid=7))["radio"][0]
    assert listed["has_trace"] is True and "trace" not in listed and "trace_json" not in listed
    exported = (await database.history_query(session_uid=7, include_traces=True))["radio"][0]
    assert exported["has_trace"] is True and exported["trace"] == trace
    assert "trace_json" not in exported


@pytest.mark.asyncio
async def test_a_database_a_test_build_upgraded_starts_and_keeps_its_traces(tmp_path):
    """Pre-release builds recorded the trace migration as 5510; 5.6.0 calls it 5600."""
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    kept = await database.save_radio_message(
        {"session_uid": 7, "track_id": 12, "current_lap": 4}, "engineer", "Copy.", "x", trace={"path": "model"}
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE schema_versions SET version = 5510, app_version = '5.5.1' WHERE version = 5600")
        db.execute("PRAGMA user_version=5510")

    reopened = PitWallDatabase(path)
    await reopened.initialize()

    with sqlite3.connect(path) as db:
        versions = {row[0]: row[1] for row in db.execute("SELECT version, app_version FROM schema_versions")}
        user_version = db.execute("PRAGMA user_version").fetchone()[0]
    assert 5510 not in versions and versions[5600] == "5.6.0"
    # Relabelled, not migrated twice; the newest migration still stands.
    from pitwall.migrations import LATEST_SCHEMA_VERSION

    assert user_version == LATEST_SCHEMA_VERSION and reopened.schema_version == LATEST_SCHEMA_VERSION
    assert (await reopened.radio_trace(kept))["trace"] == {"path": "model"}


@pytest.mark.asyncio
async def test_an_earlier_number_with_different_contents_is_refused(tmp_path):
    path = tmp_path / "pitwall.sqlite3"
    await PitWallDatabase(path).initialize()
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE schema_versions SET version = 5510, checksum = 'not-this-migration' WHERE version = 5600"
        )
    with pytest.raises(RuntimeError, match="5510"):
        await PitWallDatabase(path).initialize()
