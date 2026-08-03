"""Speech-to-speech radio session behaviour.

These exercise the parts that do not need a live socket: the session payload
sent to OpenAI, resampling to the 24 kHz the API requires, the tool-call round
trip, the allow-list boundary, and the idle/close accounting that keeps a
session from billing for a whole race.
"""

from __future__ import annotations

import base64
import json

import numpy as np
import pytest

from pitwall.config import settings
from pitwall.realtime import RADIO_TOOLS, REALTIME_RATE, RealtimeRadio


class FakeConnection:
    """Records what the session would have sent over the socket."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, event: dict) -> None:
        self.sent.append(event)

    async def close(self) -> None:
        self.closed = True

    def of_type(self, kind: str) -> list[dict]:
        return [event for event in self.sent if event.get("type") == kind]


class Event:
    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


@pytest.fixture
def radio(stack):
    store, _database, _strategy, _setup, _analysis, tools = stack
    return RealtimeRadio(store, tools)


def test_resampling_targets_the_only_supported_rate():
    """The Realtime API accepts 24 kHz PCM only; capture runs at 16 kHz."""
    source_rate = 16_000
    one_second = np.zeros(source_rate, dtype=np.int16)
    resampled = RealtimeRadio.resample_to_realtime(one_second, source_rate)
    assert resampled.dtype == np.int16
    assert resampled.size == REALTIME_RATE

    # A tone survives resampling with its shape intact rather than being clipped.
    t = np.arange(source_rate) / source_rate
    tone = (np.sin(2 * np.pi * 440 * t) * 12_000).astype(np.int16)
    out = RealtimeRadio.resample_to_realtime(tone, source_rate)
    assert out.size == REALTIME_RATE
    assert 0.8 < (float(np.abs(out).mean()) / float(np.abs(tone).mean())) < 1.2

    # Already-correct audio is passed through untouched, and empty stays empty.
    already = np.ones(64, dtype=np.int16)
    assert np.array_equal(
        RealtimeRadio.resample_to_realtime(already, REALTIME_RATE), already
    )
    assert RealtimeRadio.resample_to_realtime(np.zeros(0, dtype=np.int16), 16_000).size == 0


@pytest.mark.asyncio
async def test_session_payload_configures_barge_in_and_audio_format(radio):
    payload = await radio._session_payload()

    assert payload["type"] == "realtime"
    assert payload["output_modalities"] == ["audio"]
    assert payload["audio"]["input"]["format"] == {
        "type": "audio/pcm",
        "rate": REALTIME_RATE,
    }
    assert payload["audio"]["output"]["format"]["rate"] == REALTIME_RATE

    turn = payload["audio"]["input"]["turn_detection"]
    assert turn["type"] == "server_vad"
    # Barge-in: the driver talking cancels the engineer mid-sentence, which the
    # file-based pipeline could not do.
    assert turn["interrupt_response"] is True
    assert turn["create_response"] is True

    # Cost control: a bounded reply, not an open-ended one.
    assert 0 < payload["max_output_tokens"] <= 4096


@pytest.mark.asyncio
async def test_session_offers_only_allow_listed_telemetry_tools(radio):
    payload = await radio._session_payload()
    names = [tool["name"] for tool in payload["tools"]]

    assert names, "the engineer must be able to look up telemetry"
    assert set(names) <= set(RADIO_TOOLS)
    assert all(tool["type"] == "function" for tool in payload["tools"])
    # The field-wide tools are the point of the exercise.
    for required in ("get_race_flow", "get_rival_car_state", "get_field_state"):
        assert required in names
    # Nothing that writes, generates or reconfigures is exposed to speech.
    for forbidden in ("generate_setup", "get_stored_history"):
        assert forbidden not in names


@pytest.mark.asyncio
async def test_persona_forbids_unsourced_numbers(radio):
    payload = await radio._session_payload()
    instructions = payload["instructions"].lower()
    assert "must come from a tool call" in instructions
    assert "restricted" in instructions
    assert "manual override" in instructions


@pytest.mark.asyncio
async def test_tool_call_result_is_returned_and_a_reply_requested(radio):
    connection = FakeConnection()
    radio._connection = connection

    await radio._run_tool(
        Event(
            type="response.function_call_arguments.done",
            name="get_session_overview",
            call_id="call_1",
            arguments="{}",
        )
    )

    outputs = connection.of_type("conversation.item.create")
    assert len(outputs) == 1
    item = outputs[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_1"
    payload = json.loads(item["output"])
    assert "session_type" in payload
    # The model is prompted to answer once the result is back.
    assert connection.of_type("response.create")


@pytest.mark.asyncio
async def test_unknown_tool_is_refused_rather_than_executed(radio):
    """The model may only reach the allow-list, exactly as on the text path."""
    connection = FakeConnection()
    radio._connection = connection

    await radio._run_tool(
        Event(name="os.system", call_id="call_2", arguments='{"cmd": "rm -rf /"}')
    )

    item = connection.of_type("conversation.item.create")[0]["item"]
    assert "not available" in json.loads(item["output"])["error"]


@pytest.mark.asyncio
async def test_malformed_tool_arguments_return_an_error_not_a_crash(radio):
    connection = FakeConnection()
    radio._connection = connection

    await radio._run_tool(
        Event(name="get_gap", call_id="call_3", arguments="{not json")
    )

    item = connection.of_type("conversation.item.create")[0]["item"]
    assert "Invalid tool arguments" in json.loads(item["output"])["error"]


@pytest.mark.asyncio
async def test_failing_tool_is_reported_to_the_model(radio, monkeypatch):
    connection = FakeConnection()
    radio._connection = connection

    async def explode(name, arguments):
        raise RuntimeError("telemetry bus offline")

    monkeypatch.setattr(radio.tools, "call", explode)
    await radio._run_tool(
        Event(name="get_gap", call_id="call_4", arguments='{"target": "ahead"}')
    )

    item = connection.of_type("conversation.item.create")[0]["item"]
    assert "telemetry bus offline" in json.loads(item["output"])["error"]


@pytest.mark.asyncio
async def test_audio_is_sent_base64_encoded_at_the_required_rate(radio):
    connection = FakeConnection()
    radio._connection = connection

    block = (np.ones(1600, dtype=np.int16) * 500)
    await radio.send_audio(block, 16_000)

    appended = connection.of_type("input_audio_buffer.append")
    assert len(appended) == 1
    decoded = np.frombuffer(base64.b64decode(appended[0]["audio"]), dtype=np.int16)
    assert decoded.size == 2400  # 100 ms at 24 kHz


@pytest.mark.asyncio
async def test_audio_is_dropped_when_no_session_is_open(radio):
    """Nothing may be streamed to a paid endpoint outside a conversation."""
    radio._connection = None
    await radio.send_audio(np.ones(1600, dtype=np.int16), 16_000)  # must not raise
    assert radio.is_open is False


@pytest.mark.asyncio
async def test_driver_and_engineer_speech_reach_the_radio_log(radio, stack):
    store = stack[0]
    recorded: list[tuple[str, str]] = []

    async def capture(role: str, text: str) -> None:
        recorded.append((role, text))

    radio.on_transcript = capture
    radio._connection = FakeConnection()

    await radio._handle_event(
        Event(
            type="conversation.item.input_audio_transcription.completed",
            transcript="what is the gap to Norris",
        )
    )
    await radio._handle_event(Event(type="response.created"))
    await radio._handle_event(
        Event(type="response.output_audio_transcript.delta", delta="Norris is 1.4 ahead.")
    )
    await radio._handle_event(Event(type="response.done"))

    assert ("driver", "what is the gap to Norris") in recorded
    assert ("engineer", "Norris is 1.4 ahead.") in recorded

    snapshot = await store.snapshot_live()
    roles = [entry["role"] for entry in snapshot["radio_log"]]
    assert roles == ["driver", "engineer"]
    assert snapshot["radio_last_transcript"] == "what is the gap to Norris"


@pytest.mark.asyncio
async def test_response_lifecycle_tracks_whether_the_engineer_is_speaking(radio):
    radio._connection = FakeConnection()
    assert radio.is_speaking is False
    await radio._handle_event(Event(type="response.created"))
    assert radio.is_speaking is True
    await radio._handle_event(Event(type="response.done"))
    assert radio.is_speaking is False


@pytest.mark.asyncio
async def test_idle_and_maximum_session_limits_are_configured():
    """A session bills while connected, so it must not stay open all race."""
    assert 0 < settings.realtime_idle_timeout_s <= 120
    assert 0 < settings.realtime_max_session_s <= 3600
    assert settings.realtime_max_session_s > settings.realtime_idle_timeout_s
    # Off by default: enabling a metered voice pipeline is an explicit choice.
    assert settings.voice_realtime_enabled is False
