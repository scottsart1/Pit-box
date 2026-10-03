from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from pitwall.brain import PERSONA, EngineerBrain
from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService
from pitwall.engineering_context import lap_note_origin
from pitwall.providers import ProviderResult
from pitwall.realtime import RealtimeRadio
from pitwall.session_guard import SessionChangedError
from pitwall.tools import TelemetryTools
from pitwall.voice import NativeVoiceController


def saved_lap(number, **changes):
    return {
        "session_uid": 53001,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Practice",
        "mode_profile": "practice",
        "lap_num": number,
        "lap_time_ms": 90000 + number * 10,
        "valid": True,
        "compound": "MEDIUM",
        "run_serial": 1,
        "tyre_age_start": number,
        "fuel_start_kg": 20 - number,
        "air_temp_c": 21,
        "track_temp_c": 30,
        "weather": "Clear",
        "context_observed": True,
        "traffic_observed": True,
        "trace_coverage": 0.99,
        "setup": {"front_wing": 28},
        "learning_exclusions": [],
        "trace": [],
        **changes,
    }


async def engineering_stack(stack, *, current_lap=3, timeline_epoch=0):
    store, database, *_, tools = stack
    await store.update(session_uid=53001, track_id=10, track_name="Spa", session_type="Practice",
                       mode_profile="practice", current_lap=current_lap, timeline_epoch=timeline_epoch)
    tools.engineering = EngineeringService(database)
    return store, database, tools


@pytest.mark.asyncio
async def test_reported_traffic_excludes_only_explicit_lap_and_review_preserves_provenance(stack):
    _, database, tools = await engineering_stack(stack)
    for number in (1, 2):
        await database.save_lap(saved_lap(number), [])
    result = await tools.record_lap_observation(
        "A car blocked me in sector two and ruined my last lap", category="traffic",
        exclude_from_pace=True, reference="last_lap",
    )
    assert result["saved"]
    assert result["provenance"] == "driver_reported"
    report = await tools.engineering.report(session_id(53001))
    laps = report["laps"]
    assert result["note"]["lap_ids"] == [laps[1]["id"]]
    assert report["runs"][0]["summary"]["clean_lap_ids"] == [laps[0]["id"]]
    review = await tools.get_practice_run_review()
    assert review["lap_notes"][0]["text"] == result["note"]["text"]
    assert review["lap_evidence"][1]["air_temp_c"] == 21
    assert review["lap_evidence"][1]["traffic_observed"] is True
    assert "reported" in review["note"]
    filtered = await tools.get_lap_observations(reference="laps", lap_numbers=[1])
    assert filtered["lap_notes"] == []
    assert len(filtered["lap_evidence"]) == 1


@pytest.mark.asyncio
async def test_context_only_note_keeps_lap_eligible_and_engineer_source_distinct(stack):
    _, database, tools = await engineering_stack(stack)
    await database.save_lap(saved_lap(2), [])
    result = await tools.record_lap_observation(
        "Air temperature rose; its contribution to pace is not established",
        category="conditions", source="engineer", reference="last_lap",
    )
    assert result["saved"] and result["provenance"] == "engineer_interpretation"
    report = await tools.engineering.report(session_id(53001))
    assert report["runs"][0]["summary"]["clean_lap_count"] == 1
    assert not report["lap_notes"][0]["exclude_from_pace"]


@pytest.mark.asyncio
async def test_current_lap_note_survives_before_first_lap_and_resolves_only_its_timeline(stack):
    store, database, tools = await engineering_stack(stack, current_lap=1)
    result = await tools.record_lap_observation(
        "I locked up on this lap", category="mistake", reference="current_lap", exclude_from_pace=True,
    )
    assert result["saved"]
    assert result["note"]["lap_ids"] == []
    assert result["note"]["pending_laps"] == [{"lap_num": 1, "timeline_epoch": 0}]
    assert (await tools.get_lap_observations(reference="current_lap"))["note_count"] == 1
    await database.save_lap(saved_lap(1, timeline_epoch=1), [])
    report = await tools.engineering.report(session_id(53001))
    assert not report["lap_notes"][0]["lap_ids"]
    assert report["runs"][0]["summary"]["clean_lap_count"] == 1
    await database.save_lap(saved_lap(1), [])
    report = await tools.engineering.report(session_id(53001))
    original = next(lap for lap in report["laps"] if lap["timeline_epoch"] == 0)
    assert report["lap_notes"][0]["lap_ids"] == [original["id"]]
    await store.update(timeline_epoch=1)
    assert (await tools.get_lap_observations(reference="current_lap"))["note_count"] == 0


@pytest.mark.asyncio
async def test_same_lap_number_after_flashback_targets_current_timeline_only(stack):
    _, database, tools = await engineering_stack(stack, timeline_epoch=1)
    for epoch in (0, 1):
        await database.save_lap(saved_lap(2, timeline_epoch=epoch), [])
    result = await tools.record_lap_observation("Tried a later apex", reference="laps", lap_numbers=[2])
    report = await tools.engineering.report(session_id(53001))
    lap = next(item for item in report["laps"] if item["timeline_epoch"] == 1)
    assert result["saved"] and result["note"]["lap_ids"] == [lap["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"reference": "laps", "lap_numbers": [99]},
    {"reference": "laps", "lap_numbers": []},
    {"reference": "all"},
    {"reference": "group", "group_id": "another-session-group"},
    {"reference": "last_lap", "lap_numbers": [2]},
    {"source": "observed"},
    {"exclude_from_pace": "false"},
])
async def test_unresolved_or_invalid_note_scope_is_never_silently_saved(stack, arguments):
    _, database, tools = await engineering_stack(stack)
    await database.save_lap(saved_lap(2), [])
    result = await tools.record_lap_observation("Reported context", **arguments)
    assert result["saved"] is False
    assert not (await tools.engineering.report(session_id(53001)))["lap_notes"]


@pytest.mark.asyncio
async def test_track_change_while_resolving_note_cannot_write_next_session(stack, monkeypatch):
    store, database, tools = await engineering_stack(stack)
    await database.save_lap(saved_lap(2), [])
    original = tools.engineering.report
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def delayed(key):
        report = await original(key)
        entered.set()
        await proceed.wait()
        return report

    monkeypatch.setattr(tools.engineering, "report", delayed)
    save = AsyncMock()
    monkeypatch.setattr(tools.engineering, "save_lap_note", save)
    task = asyncio.create_task(tools.record_lap_observation("Blocked on my last lap"))
    await asyncio.wait_for(entered.wait(), 2)
    await store.update(session_uid=53002, track_id=3)
    proceed.set()
    with pytest.raises(SessionChangedError):
        await task
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_written_during_transition_retains_original_session_identity(stack, monkeypatch):
    store, database, tools = await engineering_stack(stack)
    await database.save_lap(saved_lap(2), [])
    original = tools.engineering.save_lap_note

    async def switch_after_save(*args, **kwargs):
        result = await original(*args, **kwargs)
        await store.update(session_uid=53002, track_id=3)
        return result

    monkeypatch.setattr(tools.engineering, "save_lap_note", switch_after_save)
    with pytest.raises(SessionChangedError):
        await tools.record_lap_observation("Blocked on my last lap")
    old = await tools.engineering.report(session_id(53001))
    assert len(old["lap_notes"]) == 1
    assert old["lap_notes"][0]["lap_ids"] == [old["laps"][0]["id"]]
    assert not (await tools.get_lap_observations())["available"]


@pytest.mark.asyncio
async def test_feedback_reaches_model_note_tool_instead_of_gap_lookup(stack):
    store, database, tools = await engineering_stack(stack)
    brain = EngineerBrain(store, tools, database)
    utterance = "My last lap was bad because traffic held me up, note that gap to the car ahead"
    assert await brain._fast_answer(utterance) is None
    names = {schema["name"] for schema in tools.schemas_for_route("fast")}
    assert {"record_lap_observation", "get_lap_observations", "compare_practice_groups"} <= names
    assert "Only say a note was saved when the tool returns saved=true" in PERSONA


@pytest.mark.asyncio
async def test_live_incident_hint_uses_explicit_previous_lap_only(stack):
    store, database, tools = await engineering_stack(stack, current_lap=18)
    brain = EngineerBrain(store, tools, database)
    await brain._capture_feedback("I went off on the last lap")
    assert [item["lap"] for item in (await store.peek("driver_lap_incidents"))["driver_lap_incidents"]] == [17]


@pytest.mark.asyncio
@pytest.mark.parametrize("reference,expected_lap", [("current_lap", 3), ("last_lap", 2)])
async def test_crossing_line_during_model_response_keeps_spoken_lap_reference(stack, monkeypatch, reference, expected_lap):
    store, database, tools = await engineering_stack(stack, current_lap=3)
    await database.save_lap(saved_lap(2), [])
    brain = EngineerBrain(store, tools, database)
    monkeypatch.setattr(brain, "_fast_answer", AsyncMock(return_value=None))
    monkeypatch.setattr(brain, "_header", AsyncMock(return_value="Practice at Spa"))
    captured = []

    async def respond(**kwargs):
        await database.save_lap(saved_lap(3), [])
        await store.update(current_lap=4)
        note = await kwargs["execute_tool"]("record_lap_observation", {
            "text": "Blocked in sector two", "category": "traffic", "source": "driver",
            "exclude_from_pace": True, "reference": reference, "lap_numbers": [], "group_id": None,
        })
        captured.append(note)
        return ProviderResult(text="Logged the traffic on that lap.", provider="test", model="test", latency_ms=1, tool_rounds=1)

    monkeypatch.setattr(brain.router, "generate", respond)
    await brain.ask("Log traffic on this lap" if reference == "current_lap" else "Log traffic on my last lap")
    report = await tools.engineering.report(session_id(53001))
    target = next(lap for lap in report["laps"] if lap["lap_num"] == expected_lap)
    assert captured[0]["saved"]
    assert captured[0]["note"]["lap_ids"] == [target["id"]]
    assert lap_note_origin.get() is None


@pytest.mark.asyncio
async def test_realtime_notes_keep_turn_lap_and_reject_interrupted_turn_writes(stack):
    store, database, tools = await engineering_stack(stack, current_lap=3)
    await database.save_lap(saved_lap(2), [])
    radio = RealtimeRadio(store, tools)
    connection = SimpleNamespace(send=AsyncMock())
    radio._connection = connection
    await radio._handle_event(SimpleNamespace(type="input_audio_buffer.speech_started"))
    await radio._handle_event(SimpleNamespace(type="response.created", response=SimpleNamespace(id="response-a")))
    await store.update(current_lap=4)
    arguments = json.dumps({"text": "Traffic compromised this lap", "category": "traffic", "source": "driver",
                            "exclude_from_pace": True, "reference": "current_lap", "lap_numbers": [], "group_id": None})
    event = SimpleNamespace(name="record_lap_observation", arguments=arguments, call_id="call-a", response_id="response-a")
    await radio._run_tool(event)
    await radio._run_tool(event)  # Duplicate wire delivery must not duplicate a note.
    report = await tools.engineering.report(session_id(53001))
    assert len(report["lap_notes"]) == 1
    assert report["lap_notes"][0]["pending_laps"] == [{"lap_num": 3, "timeline_epoch": 0}]
    await radio._handle_event(SimpleNamespace(type="input_audio_buffer.speech_started"))
    event.call_id = "call-from-interrupted-response"
    await radio._run_tool(event)
    assert len((await tools.engineering.report(session_id(53001)))["lap_notes"]) == 1
    result = json.loads(connection.send.call_args.args[0]["item"]["output"])
    assert result["saved"] is False and "interrupted" in result["reason"]
    for unknown in ("unknown-response", ""):
        event.response_id = unknown
        await radio._run_tool(event)
        result = json.loads(connection.send.call_args.args[0]["item"]["output"])
        assert result["saved"] is False
    assert len((await tools.engineering.report(session_id(53001)))["lap_notes"]) == 1
    assert lap_note_origin.get() is None


def test_large_realtime_engineering_results_remain_valid_json_and_disclose_truncation():
    result = {"lap_notes": [{"text": "reported traffic " * 200, "source": "driver"} for _ in range(100)]}
    encoded = RealtimeRadio._tool_output(result)
    assert len(encoded) <= 16000
    decoded = json.loads(encoded)
    assert decoded["truncated"] is True
    assert decoded["lap_notes"][0]["source"] == "driver"


def test_long_voice_notebook_is_bounded_without_mutating_saved_evidence():
    notes = [{"id": f"note-{i}", "text": "traffic report " * 100, "source": "driver",
              "lap_ids": [f"lap-{j}" for j in range(100)]} for i in range(1000)]
    payload = {"lap_notes": notes, "groups": [{"id": "test-a", "context_notes": notes}], "median_delta_s": -0.3}
    bounded = TelemetryTools._bounded_engineering_payload(payload)
    assert len(json.dumps(bounded, ensure_ascii=False)) <= 64_000
    assert bounded["notes_truncated"] and bounded["evidence_truncated"]
    assert bounded["median_delta_s"] == -0.3
    assert "specific laps" in bounded["retrieval_hint"]
    assert len(payload["lap_notes"]) == 1000
    assert payload["lap_notes"][0]["text"] == "traffic report " * 100


@pytest.mark.asyncio
async def test_voice_comparison_reads_custom_groups_and_relevant_reported_context(stack):
    _, database, tools = await engineering_stack(stack, current_lap=7)
    for number in range(1, 7):
        await database.save_lap(saved_lap(number, compound="MEDIUM" if number < 4 else "HARD"), [])
    report = await tools.engineering.report(session_id(53001))
    await tools.engineering.save_groups(session_id(53001), [
        {"id": "a", "name": "Medium test", "lap_ids": [lap["id"] for lap in report["laps"][:3]]},
        {"id": "b", "name": "Hard test", "lap_ids": [lap["id"] for lap in report["laps"][3:]]},
    ])
    note = await tools.record_lap_observation(
        "Tried an earlier turn-in on the hard stint", reference="group", group_id="b", category="balance"
    )
    assert note["saved"] and len(note["note"]["lap_ids"]) == 3
    compared = await tools.compare_practice_groups("a", "b")
    assert compared["available"] and compared["comparison"]["enough_evidence"]
    assert compared["comparison"]["mode"] == "stint"
    assert compared["lap_notes"][0]["text"] == note["note"]["text"]
    strict = await tools.compare_practice_groups("a", "b", mode="setup")
    assert strict["available"] and not strict["comparison"]["enough_evidence"]


@pytest.mark.asyncio
async def test_ptt_records_speech_start_lap_before_transcription_crosses_timing_line(stack, monkeypatch):
    store, database, tools = await engineering_stack(stack, current_lap=3)
    await database.save_lap(saved_lap(2), [])
    brain = EngineerBrain(store, tools, database)
    audio = SimpleNamespace(voice_ready=True)
    voice = NativeVoiceController(store, brain, audio)
    monkeypatch.setattr(voice, "_start_realtime", AsyncMock(return_value=False))
    monkeypatch.setattr(voice, "_write_wav", lambda *args: None)
    monkeypatch.setattr(voice, "_mark_latency", AsyncMock())
    notes = []

    async def transcribe(*args, **kwargs):
        await store.update(current_lap=5)
        return "Log traffic on this lap"

    async def command(*args):
        notes.append(await tools.record_lap_observation("Blocked", reference="current_lap"))

    audio.transcribe = transcribe
    monkeypatch.setattr(voice, "_run_command", command)
    await voice._begin_interaction("ptt")
    await store.update(current_lap=4)
    voice.frames = [np.ones((1000, 1), dtype=np.int16) * 5000]
    await voice._stop_recording(process=True)
    await voice._process_task
    assert notes[0]["saved"]
    assert notes[0]["note"]["pending_laps"] == [{"lap_num": 3, "timeline_epoch": 0}]
    assert lap_note_origin.get() is None


@pytest.mark.asyncio
async def test_buffered_realtime_clip_uses_original_recording_lap(stack):
    store, _, tools = await engineering_stack(stack, current_lap=3)
    radio = RealtimeRadio(store, tools)
    origin = await store.peek("session_uid", "restart_epoch", "timeline_epoch", "session_generation", "current_lap")
    await store.update(current_lap=4)
    radio.queue_clip_origin(origin)
    await radio._handle_event(SimpleNamespace(type="input_audio_buffer.speech_started"))
    assert radio._lap_note_origin["current_lap"] == 3
    await radio._handle_event(SimpleNamespace(type="input_audio_buffer.speech_started"))
    assert radio._lap_note_origin["current_lap"] == 4
