"""The speech session and the text radio are one engineer.

A driver who switches on speech-to-speech must get the same stored outcome,
the same rules and the same pit call as on push-to-talk, and the dashboard must
report what was actually said: a line counts as delivered only once it was
spoken, and an interrupted reply is logged only as far as it played.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall import realtime as realtime_module
from pitwall.analysis import AnalysisEngine
from pitwall.brain import _SAFETY_ANCHOR, DriverCommands, EngineerBrain
from pitwall.config import settings
from pitwall.database import PitWallDatabase
from pitwall.proactive import ProactiveEngineer
from pitwall.providers import ProviderResult
from pitwall.realtime import REALTIME_PERSONA, RealtimeRadio
from pitwall.setup_advisor import SetupAdvisor
from pitwall.state import StateStore
from pitwall.strategy import StrategyEngine
from pitwall.tools import TelemetryTools
from pitwall.voice import BRAIN_FALLBACK_LINE, NativeVoiceController


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, event: dict) -> None:
        self.sent.append(event)

    async def close(self) -> None:
        return None

    def of_type(self, kind: str) -> list[dict]:
        return [event for event in self.sent if event.get("type") == kind]


class Event:
    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


class FakeStream:
    """An output device that accepts everything written to it."""

    def __init__(self) -> None:
        self.written = 0

    def write(self, data: bytes) -> None:
        self.written += len(data)

    def abort(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


LOW_CONFIDENCE_PLAN = {
    "recommended": {
        "instruction": "Box lap 14 for mediums.",
        "box_lap": 14,
        "box_laps": [14],
        "fit_compound": "MEDIUM",
        "compounds": ["SOFT", "MEDIUM"],
        "feasible": True,
        "legal": True,
        "call_changes_if": "Changes if the safety car comes out before lap 14.",
    },
    "plans": [],
    "confidence": "low",
    "model_summary": {
        "selected_stint_wear_source": "track_default",
        "compounds": {"MEDIUM": {"wear_source": "track_default", "pace_source": "track_default"}},
    },
}
SPOKEN_LOW_CONFIDENCE_CALL = (
    "Box lap 14 for mediums; provisional, confidence low, tyre estimates include "
    "track defaults; changes if the safety car comes out before lap 14."
)


async def _race(store) -> None:
    def apply(state):
        state.connected = True
        state.current_lap = 12
        state.total_laps = 28
        state.mode_profile = "race"
        state.session_type = "Race"
        state.player_position = 4

    await store.mutate(apply)


async def _brain(stack):
    store, database, _strategy, _setup, _analysis, tools = stack
    await _race(store)
    brain = EngineerBrain(store, tools, database)

    async def generate(**_kwargs):
        return ProviderResult(text="Copy.", provider="test", model="test", latency_ms=1.0, tool_rounds=0)

    brain.router.generate = generate
    return store, brain


@pytest.fixture
def realtime_on(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "voice_realtime_enabled", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)


async def _controller(store, brain, **audio):
    fields = {
        "stop_playback": lambda: None,
        "voice_ready": True,
        "play_ack": AsyncMock(),
        "stream_speech": AsyncMock(return_value=True),
    }
    fields.update(audio)
    controller = NativeVoiceController(store, brain, SimpleNamespace(**fields))
    radio = controller.realtime
    connection = FakeConnection()
    radio._connection = connection
    return controller, radio, connection


async def _turn_stored(radio) -> None:
    """Wait for the driver turn to be stored beside the receive loop."""
    task = getattr(radio, "_turn_task", None)
    if task is not None:
        await task


async def _driver_says(radio, text: str) -> None:
    await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
    await radio._handle_event(
        Event(type="conversation.item.input_audio_transcription.completed", transcript=text)
    )
    await _turn_stored(radio)


def _stored(state: dict) -> dict:
    return {
        "standing": [item.get("rule") for item in state.get("standing_instructions", [])],
        "hold": bool((state.get("strategy_hold") or {}).get("active")),
        "intent": (state.get("strategy_intent") or {}).get("intent"),
        "override": (
            (state.get("strategy_override") or {}).get("locked"),
            (state.get("strategy_override") or {}).get("next_box_lap"),
        ),
        "unit": state.get("temperature_unit"),
        "verbosity": state.get("radio_verbosity"),
        "tyre_feedback": (state.get("driver_tyre_feedback") or {}).get("category"),
        "grip_feedback": (state.get("driver_grip_feedback") or {}).get("category"),
        "incidents": [item.get("lap") for item in state.get("driver_lap_incidents", [])],
        "fuel_silenced": ProactiveEngineer.is_suppressed("fuel_warning", state.get("standing_instructions", [])),
    }


DRIVER_COMMANDS = [
    "stop telling me about fuel",
    "I'm not boxing, I'm staying out",
    "box lap 20 for hards",
    "let's do the overcut",
    "tyre temps in fahrenheit",
    "keep your answers short",
    "the rears are gone",
    "I went off at turn twelve",
    "it's drying out",
]


# ---------------------------------------------------------------- commands


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", DRIVER_COMMANDS)
async def test_a_driver_command_stores_the_same_state_on_either_radio(stack, tmp_path, realtime_on, utterance):
    """Said over the speech session, a command is as binding as on push-to-talk."""
    store, brain = await _brain(stack)
    before = _stored(await store.snapshot_analysis())
    await brain.ask(utterance)
    text_path = _stored(await store.snapshot_analysis())
    assert text_path != before, "the utterance must change something on the text path"

    speech_store = StateStore()
    database = PitWallDatabase(tmp_path / "speech.sqlite3")
    await database.initialize()
    strategy = StrategyEngine(speech_store, database)
    setup = SetupAdvisor(speech_store, database)
    tools = TelemetryTools(speech_store, database, AnalysisEngine(speech_store, database, strategy), strategy, setup)
    speech_brain = EngineerBrain(speech_store, tools, database)
    await _race(speech_store)
    _voice, radio, _connection = await _controller(speech_store, speech_brain)
    try:
        await _driver_says(radio, utterance)
        assert _stored(await speech_store.snapshot_analysis()) == text_path
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_strategy_lock_can_be_cleared_over_the_speech_radio(stack, realtime_on):
    store, brain = await _brain(stack)
    _voice, radio, _connection = await _controller(store, brain)
    try:
        await _driver_says(radio, "box lap 20 for hards")
        assert (await store.snapshot_analysis())["strategy_override"]["locked"] is True
        await _driver_says(radio, "clear my strategy override")
        override = (await store.snapshot_analysis())["strategy_override"]
        assert override["locked"] is False and override["next_box_lap"] is None
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_the_speech_model_is_told_what_was_actually_stored(stack, realtime_on):
    store, brain = await _brain(stack)
    _voice, radio, connection = await _controller(store, brain)
    try:
        await _driver_says(radio, "box lap 20 for hards")
        notes = [
            event["item"] for event in connection.of_type("conversation.item.create")
            if event["item"].get("role") == "system"
        ]
        assert notes and "Driver strategy locked: box lap 20 for hards." in notes[-1]["content"][0]["text"]
        # The refreshed brief goes out after the note.
        assert connection.sent[-1]["type"] == "session.update"

        connection.sent.clear()
        await _driver_says(radio, "box lap for hards")
        note = [
            event["item"]["content"][0]["text"] for event in connection.of_type("conversation.item.create")
        ][-1]
        assert "Nothing is locked" in note and "Ask which lap" in note
        assert (await store.snapshot_analysis())["strategy_override"].get("next_box_lap") == 20

        # A plain question stores nothing and adds nothing to the conversation.
        connection.sent.clear()
        await _driver_says(radio, "what's the gap behind?")
        assert not connection.of_type("conversation.item.create")
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_an_answer_to_the_engineers_tyre_question_is_recorded_on_the_speech_radio(stack, realtime_on):
    """The reply window sends "holding" to a new session; it must still count."""
    store, brain = await _brain(stack)
    _voice, radio, _connection = await _controller(store, brain)
    try:
        await _driver_says(radio, "holding")
        feedback = (await store.snapshot_analysis())["driver_tyre_feedback"]
        assert feedback["category"] == "tyres_fine"
    finally:
        await radio.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "category"),
    [
        ("the rears are gone", "tyres_gone"),
        ("Tyres are gone.", "tyres_gone"),
        ("my tyres are dead", "tyres_gone"),
        ("yeah the rears are going away now", "tyres_going"),
    ],
)
async def test_plain_tyre_reports_reach_the_strategy(stack, utterance, category):
    store, brain = await _brain(stack)
    await brain._capture_feedback(utterance)
    assert (await store.snapshot_analysis())["driver_tyre_feedback"]["category"] == category


@pytest.mark.asyncio
async def test_front_tyres_gone_is_still_a_front_grip_report(stack):
    store, brain = await _brain(stack)
    await brain._capture_feedback("the front tyres are gone")
    state = await store.snapshot_analysis()
    assert state["feedback"][-1]["category"] == "no_front_grip"
    assert not state.get("driver_tyre_feedback")


@pytest.mark.asyncio
async def test_ask_applies_a_turn_once(stack):
    store, brain = await _brain(stack)
    await brain.ask("they're gone, what's the gap behind?")
    state = await store.snapshot_analysis()
    assert [item["category"] for item in state["feedback"]] == ["tyres_gone"]
    assert [entry["role"] for entry in state["radio_log"]] == ["driver", "engineer"]


# ---------------------------------------------------------------- the brief


@pytest.mark.asyncio
async def test_the_speech_brief_carries_the_drivers_rules_and_the_recent_radio(stack, realtime_on, monkeypatch):
    monkeypatch.setattr(settings, "engineer_name", "Sam")
    monkeypatch.setattr(settings, "brutal_mode", True)
    store, brain = await _brain(stack)
    await brain.ask("stop telling me about fuel")
    await brain.ask("keep your answers short")
    await brain.ask("I'm not boxing, I'm staying out")
    _voice, radio, _connection = await _controller(store, brain)
    try:
        instructions = (await radio._session_payload())["instructions"]
    finally:
        await radio.client.close()

    assert instructions.startswith(REALTIME_PERSONA)
    assert "Do not raise fuel unless the driver asks" in instructions
    assert "Verbosity: terse" in instructions
    assert "Your call sign is Sam." in instructions
    assert "Brutal mode is ON" in instructions
    assert "RECENT RADIO" in instructions and "DRIVER: I'm not boxing, I'm staying out" in instructions
    assert "DRIVER DECLINED THE PIT CALL" in instructions
    # User-supplied tone and rules never get the last word.
    assert instructions.endswith(_SAFETY_ANCHOR)
    # The raw compound-rule dict and tyre-evidence JSON are not paid for on
    # every turn; the persona sends the model to the tools for numbers.
    assert "| compound rule " not in instructions
    assert "TYRE EVIDENCE" not in instructions


@pytest.mark.asyncio
async def test_a_rule_set_mid_session_reaches_the_next_reply(stack, realtime_on):
    store, brain = await _brain(stack)
    _voice, radio, connection = await _controller(store, brain)
    try:
        await _driver_says(radio, "stop telling me about the tyres")
        update = connection.of_type("session.update")[-1]
        assert "Standing instructions from the driver" in update["session"]["instructions"]
        assert "tyre" in update["session"]["instructions"].split("Standing instructions from the driver")[1].lower()
    finally:
        await radio.client.close()


# ------------------------------------------------------------- the pit call


@pytest.mark.asyncio
async def test_the_speech_session_gets_the_pit_call_as_the_text_path_speaks_it(stack, realtime_on):
    store, brain = await _brain(stack)
    await store.update(strategy=LOW_CONFIDENCE_PLAN)
    _voice, radio, connection = await _controller(store, brain)
    try:
        instructions = await radio._instructions()
        assert f"PIT CALL AS SPOKEN: {SPOKEN_LOW_CONFIDENCE_CALL}" in instructions

        async def plan(name, arguments):
            return dict(LOW_CONFIDENCE_PLAN)

        radio.tools.call = plan
        radio._response_active = True
        await radio._run_tool(Event(name="get_pit_strategy", call_id="call_1", arguments="{}"))
        output = json.loads(connection.of_type("conversation.item.create")[-1]["item"]["output"])
        assert output["radio_call"] == SPOKEN_LOW_CONFIDENCE_CALL
    finally:
        await radio.client.close()


def test_the_speech_persona_has_the_rules_that_change_pit_and_damage_calls():
    flat = " ".join(REALTIME_PERSONA.lower().split())
    assert "only pit-stop repair is the front wing" in flat
    assert 'no "inspection" stop' in flat
    assert "radio_call" in flat and "provisional qualifier" in flat
    assert "not to having a strategy" in flat and "next option from get_pit_strategy" in flat
    assert "pit wall note" in flat


# ------------------------------------------------- calls into an open session


def _call_event(connection: FakeConnection) -> dict:
    calls = [event for event in connection.of_type("response.create") if event.get("response")]
    assert calls, "the call was not handed to the session"
    return calls[-1]


def _created(connection: FakeConnection, response_id: str) -> Event:
    """The server's response.created for the last call handed to the session."""
    metadata = (_call_event(connection)["response"].get("metadata")) or None
    return Event(type="response.created", response=Event(id=response_id, metadata=metadata))


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_call_during_a_reply_waits_for_it_and_counts_only_once_spoken(stack, realtime_on):
    store, brain = await _brain(stack)
    controller, radio, connection = await _controller(store, brain)
    try:
        await radio._handle_event(Event(type="response.created", response=Event(id="resp_1")))
        speaking = asyncio.create_task(controller.speak_text("Red flag, red flag. Pit lane is closed."))
        await _settle()
        # The API rejects a second response while one is open.
        assert not connection.of_type("response.create")
        assert not speaking.done()

        await radio._handle_event(Event(type="response.done", response=Event(id="resp_1", status="completed")))
        await _settle()
        call = _call_event(connection)
        assert "Red flag, red flag. Pit lane is closed." in call["response"]["instructions"]
        assert not speaking.done(), "sent is not spoken"

        await radio._handle_event(_created(connection, "resp_2"))
        await radio._handle_event(
            Event(type="response.output_audio_transcript.delta", delta="Red flag, red flag. Pit lane is closed.")
        )
        await radio._handle_event(Event(type="response.done", response=Event(id="resp_2", status="completed")))
        assert await speaking is True
        controller.audio.stream_speech.assert_not_awaited()
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_spoken_call_is_logged_once(stack, realtime_on):
    store, brain = await _brain(stack)
    controller, radio, connection = await _controller(store, brain)
    try:
        speaking = asyncio.create_task(controller.speak_text("Safety car, safety car."))
        await _settle()
        await radio._handle_event(_created(connection, "resp_9"))
        await radio._handle_event(Event(type="response.output_audio_transcript.delta", delta="Safety car, safety car."))
        await radio._handle_event(Event(type="response.done", response=Event(id="resp_9", status="completed")))
        assert await speaking is True
        # What ProactiveEngineer does with a delivered call.
        await brain.record_spoken_call("Safety car, safety car.")
        log = [entry["text"] for entry in (await store.snapshot_analysis())["radio_log"]]
        assert log == ["Safety car, safety car."]
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_call_the_server_refuses_is_not_reported_delivered(stack, realtime_on):
    store, brain = await _brain(stack)
    _voice, radio, connection = await _controller(store, brain)
    try:
        saying = asyncio.create_task(radio.say("Red flag, red flag."))
        await _settle()
        call = _call_event(connection)
        await radio._handle_event(Event(type="error", error=Event(
            message="Conversation already has an active response in progress", event_id=call.get("event_id"),
        )))
        # Never spoken by the session, so the other audio path may take it.
        assert await saying is None
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_call_the_driver_talks_over_is_not_delivered_and_not_repeated_on_tts(stack, realtime_on):
    store, brain = await _brain(stack)
    controller, radio, connection = await _controller(store, brain)
    try:
        speaking = asyncio.create_task(controller.speak_text("Penalty, five seconds."))
        await _settle()
        await radio._handle_event(_created(connection, "resp_3"))
        await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
        await radio._handle_event(Event(type="response.done", response=Event(id="resp_3", status="cancelled")))
        assert await speaking is False
        controller.audio.stream_speech.assert_not_awaited()
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_call_held_behind_a_stuck_reply_goes_to_the_other_audio_path(stack, realtime_on, monkeypatch):
    monkeypatch.setattr(realtime_module, "CALL_HOLD_S", 0.05, raising=False)
    store, brain = await _brain(stack)
    controller, radio, connection = await _controller(store, brain)
    try:
        await radio._handle_event(Event(type="response.created", response=Event(id="resp_stuck")))
        assert await controller.speak_text("Red flag, red flag.") is True
        assert not connection.of_type("response.create")
        controller.audio.stream_speech.assert_awaited_once()
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_call_waits_for_the_driver_to_finish_and_for_their_answer(stack, realtime_on):
    store, brain = await _brain(stack)
    controller, radio, connection = await _controller(store, brain)
    try:
        await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
        speaking = asyncio.create_task(controller.speak_text("Safety car deployed."))
        await _settle()
        assert not connection.of_type("response.create"), "never talk over the driver"
        await radio._handle_event(Event(type="input_audio_buffer.speech_stopped"))
        await radio._handle_event(Event(type="response.created", response=Event(id="reply")))
        await radio._handle_event(Event(type="response.done", response=Event(id="reply", status="completed")))
        await _settle()
        await radio._handle_event(_created(connection, "call"))
        await radio._handle_event(Event(type="response.done", response=Event(id="call", status="completed")))
        assert await speaking is True
    finally:
        await radio.client.close()


# --------------------------------------------------------- how a reply ended


@pytest.mark.asyncio
async def test_a_failed_reply_is_shown_and_the_turn_is_answered_on_the_standard_radio(stack, realtime_on):
    store, brain = await _brain(stack)
    controller, radio, _connection = await _controller(store, brain)
    answered: list[tuple] = []

    async def run_command(command, source, *, recorded=None):
        answered.append((command, source, recorded))

    controller._run_command = run_command
    try:
        await _driver_says(radio, "gap to Norris?")
        await radio._handle_event(Event(type="response.created", response=Event(id="r_failed")))
        await radio._handle_event(Event(type="response.done", response=Event(
            id="r_failed", status="failed",
            status_details=Event(type="failed", error=Event(type="insufficient_quota", code="insufficient_quota")),
        )))
        state = await store.snapshot_live()
        assert "insufficient_quota" in state["last_error"]
        assert state["engineer_status"] == "error"
        await controller._realtime_recovery_task
        assert len(answered) == 1
        command, source, recorded = answered[0]
        assert (command, source) == ("gap to Norris?", "realtime fallback")
        assert isinstance(recorded, DriverCommands)
        assert radio.is_open is False
        assert "insufficient_quota" in (await store.snapshot_live())["last_error"]
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_reply_that_fails_before_the_transcript_is_answered_when_it_lands(stack, realtime_on):
    store, brain = await _brain(stack)
    _voice, radio, _connection = await _controller(store, brain)
    answered: list[str] = []

    async def recover(transcript):
        answered.append(transcript)

    radio.on_failed_turn = recover
    try:
        await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
        await radio._handle_event(Event(type="response.created", response=Event(id="r_early")))
        await radio._handle_event(Event(type="response.done", response=Event(id="r_early", status="incomplete")))
        assert answered == []
        await radio._handle_event(
            Event(type="conversation.item.input_audio_transcription.completed", transcript="what's the plan")
        )
        await _turn_stored(radio)
        assert answered == ["what's the plan"]
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_failed_turn_with_no_transcript_still_gets_a_spoken_reply(stack, realtime_on):
    store, brain = await _brain(stack)
    controller, radio, _connection = await _controller(store, brain)
    try:
        await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
        await radio._handle_event(Event(type="response.created", response=Event(id="r_quiet")))
        await radio._handle_event(Event(type="response.done", response=Event(id="r_quiet", status="failed")))
        await radio._handle_event(Event(type="conversation.item.input_audio_transcription.failed"))
        await controller._realtime_recovery_task
        controller.audio.stream_speech.assert_awaited_once()
        assert controller.audio.stream_speech.await_args.args[0] == BRAIN_FALLBACK_LINE
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_the_standard_radio_answer_does_not_log_or_store_the_turn_twice(stack):
    store, brain = await _brain(stack)
    await store.append_radio("driver", "they're gone, what's the gap behind?")
    commands = await brain.apply_driver_commands("they're gone, what's the gap behind?")
    await brain.ask("they're gone, what's the gap behind?", recorded=commands)
    state = await store.snapshot_analysis()
    assert [entry["role"] for entry in state["radio_log"]] == ["driver", "engineer"]
    assert [item["category"] for item in state["feedback"]] == ["tyres_gone"]


@pytest.mark.asyncio
async def test_a_barge_in_logs_only_what_was_heard_and_truncates_the_rest(stack, monkeypatch):
    store, _database, _strategy, _setup, _analysis, tools = stack
    radio = RealtimeRadio(store, tools)
    connection = FakeConnection()
    radio._connection = connection
    radio._output_stream = FakeStream()
    radio._ensure_output_stream = lambda: radio._output_stream or FakeStream()
    clock = {"now": 100.0}
    monkeypatch.setattr(realtime_module.time, "monotonic", lambda: clock["now"])
    try:
        await radio._handle_event(
            Event(type="conversation.item.input_audio_transcription.completed", transcript="what's the plan")
        )
        await radio._handle_event(Event(type="response.created", response=Event(id="r1")))
        one_second = base64.b64encode(bytes(48_000)).decode("ascii")
        sentence = ["Box lap 14 for mediums. ", "Fuel is plus 0.4, ", "so lift and coast is not needed."]
        for delta in sentence:
            await radio._handle_event(Event(type="response.output_audio_transcript.delta", delta=delta))
        for _ in range(4):
            await radio._handle_event(Event(
                type="response.output_audio.delta", delta=one_second, response_id="r1", item_id="item_1",
            ))
        # The driver talks over it one and a half seconds into four seconds of speech.
        clock["now"] += 1.5
        await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
        truncate = connection.of_type("conversation.item.truncate")
        assert truncate == [{
            "type": "conversation.item.truncate", "item_id": "item_1", "content_index": 0, "audio_end_ms": 1500,
        }]
        await radio._handle_event(Event(type="response.done", response=Event(id="r1", status="cancelled")))
        log = [(entry["role"], entry["text"]) for entry in (await store.snapshot_analysis())["radio_log"]]
        assert log[0] == ("driver", "what's the plan")
        role, heard = log[1]
        assert role == "engineer"
        assert heard.startswith("Box lap 14 for mediums.") and heard.endswith("—")
        assert "lift and coast" not in heard
    finally:
        await radio.client.close()


@pytest.mark.asyncio
async def test_a_cancelled_reply_nobody_heard_is_not_logged_and_asks_for_nothing(stack):
    store, _database, _strategy, _setup, _analysis, tools = stack
    radio = RealtimeRadio(store, tools)
    connection = FakeConnection()
    radio._connection = connection
    radio._ensure_output_stream = lambda: None
    try:
        await radio._handle_event(Event(type="response.created", response=Event(id="r2")))
        await radio._handle_event(Event(type="response.output_audio_transcript.delta", delta="Norris is 1.4 ahead."))
        radio._tool_results_pending = True
        await radio._handle_event(Event(type="input_audio_buffer.speech_started"))
        await radio._handle_event(Event(type="response.done", response=Event(id="r2", status="cancelled")))
        assert (await store.snapshot_analysis())["radio_log"] == []
        # The driver has moved on; their new turn is answered by the server.
        assert not connection.of_type("response.create")
    finally:
        await radio.client.close()
