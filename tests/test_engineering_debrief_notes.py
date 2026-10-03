from __future__ import annotations

import asyncio
import copy
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile

from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService
from pitwall.engineering_context import lap_note_origin


def lap(number, **changes):
    return {
        "session_uid": 53041,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Practice",
        "mode_profile": "practice",
        "lap_num": number,
        "lap_time_ms": 92000 if number == 2 else 90000,
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
        "temps_end": [93, 94, 93, 94],
        "trace": [],
        **changes,
    }


async def application_stack(stack, monkeypatch, tmp_path, *, current_lap=5):
    from pitwall.config import settings

    # Import-time services must use disposable paths even when this file runs
    # alone, before any other app tests have imported the module.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from pitwall import app as application

    store, database, *_, tools = stack
    await store.update(
        session_uid=53041,
        track_id=10,
        track_name="Spa",
        session_type="Practice",
        mode_profile="practice",
        current_lap=current_lap,
        run_serial=1,
        proactive={"enabled": True},
    )
    engineering = EngineeringService(database)
    tools.engineering = engineering
    voice = SimpleNamespace(is_busy=False, speak_text=AsyncMock(return_value=True))
    for name, value in (
        ("store", store),
        ("database", database),
        ("engineering", engineering),
        ("voice", voice),
        ("browser_audio_lock", asyncio.Lock()),
        ("browser_audio_origin", None),
    ):
        monkeypatch.setattr(application, name, value)
    return application, store, database, engineering, tools, voice


@pytest.mark.asyncio
async def test_stint_debrief_uses_saved_exclusions_and_keeps_attributed_context(
    stack, monkeypatch, tmp_path
):
    application, store, database, engineering, _, voice = await application_stack(
        stack, monkeypatch, tmp_path
    )
    raw_laps = [lap(number) for number in range(1, 5)]
    for item in raw_laps:
        await database.save_lap(item, [])
    await store.update(completed_laps=copy.deepcopy(raw_laps))
    key = session_id(53041)
    report = await engineering.report(key)
    bad_id = report["laps"][1]["id"]
    traffic_note = await engineering.save_lap_note(
        key,
        [bad_id],
        "Held up through sector two",
        category="traffic",
        exclude_from_pace=True,
        source="driver",
    )
    await engineering.save_lap_note(
        key,
        [report["laps"][2]["id"]],
        "A reported balance change, without evidence of its pace effect",
        category="balance",
        source="engineer",
    )
    snapshot = await store.snapshot_analysis()
    await application._persist_stint_debrief(snapshot)

    debrief = (await store.peek("briefings"))["briefings"]["post_stint"]
    payload = debrief["payload"]
    assert payload["summary"]["lap_count"] == 4
    assert payload["summary"]["clean_lap_count"] == 3
    assert bad_id not in payload["summary"]["clean_lap_ids"]
    assert payload["summary"]["excluded_laps"][0]["reasons"] == [
        "driver_reported_traffic"
    ]
    assert payload["conditions"]["air_temp_c"] == [21, 21]
    assert {note["source"] for note in payload["context_notes"]} == {
        "driver",
        "engineer",
    }
    assert payload["context_notes"][0]["lap_ids"] == [bad_id]
    assert "3 clean laps" in debrief["text"]
    assert "Reported compromised laps were excluded from pace" in debrief["text"]
    voice.speak_text.assert_awaited_once_with(debrief["text"])
    with database._connect() as db:
        row = db.execute(
            "SELECT payload_json,text FROM briefings WHERE kind='post_stint'"
        ).fetchone()
    assert json.loads(row["payload_json"]) == payload
    assert row["text"] == debrief["text"]
    assert (await store.peek("completed_laps"))["completed_laps"] == raw_laps

    # Correcting a note must affect the next debrief, without rewriting the
    # measured lap or leaving a sticky derived exclusion in the report.
    await engineering.delete_lap_note(key, traffic_note["id"])
    await application._persist_stint_debrief(snapshot)
    corrected = (await store.peek("briefings"))["briefings"]["post_stint"]
    assert corrected["payload"]["summary"]["clean_lap_count"] == 4
    assert "Reported compromised laps were excluded" not in corrected["text"]
    assert len(corrected["payload"]["context_notes"]) == 1
    assert corrected["payload"]["context_notes"][0]["source"] == "engineer"


@pytest.mark.asyncio
async def test_stint_debrief_cannot_reuse_an_earlier_timeline_with_the_same_run_serial(
    stack, monkeypatch, tmp_path
):
    application, store, database, _, _, voice = await application_stack(
        stack, monkeypatch, tmp_path
    )
    for number in range(1, 4):
        await database.save_lap(lap(number), [])
    await store.update(timeline_epoch=1, current_lap=2, completed_laps=[])
    save = AsyncMock()
    monkeypatch.setattr(database, "save_briefing", save)
    await application._persist_stint_debrief(await store.snapshot_analysis())
    save.assert_not_awaited()
    voice.speak_text.assert_not_awaited()
    assert "post_stint" not in (await store.peek("briefings"))["briefings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["track", "timeline"])
async def test_stint_debrief_discards_report_if_context_changes_while_loading(
    stack, monkeypatch, tmp_path, boundary
):
    application, store, database, engineering, _, voice = await application_stack(
        stack, monkeypatch, tmp_path
    )
    await database.save_lap(lap(1), [])
    original_report = engineering.report

    async def changed_report(key):
        report = await original_report(key)
        if boundary == "track":
            await store.update(session_uid=53042, track_id=3)
        else:
            await store.update(timeline_epoch=1)
        return report

    monkeypatch.setattr(engineering, "report", changed_report)
    save = AsyncMock()
    monkeypatch.setattr(database, "save_briefing", save)
    await application._persist_stint_debrief(await store.snapshot_analysis())
    save.assert_not_awaited()
    voice.speak_text.assert_not_awaited()
    assert "post_stint" not in (await store.peek("briefings"))["briefings"]


@pytest.mark.asyncio
async def test_browser_transcription_crossing_the_line_keeps_this_lap_note_on_its_origin(
    stack, monkeypatch, tmp_path
):
    application, store, database, engineering, tools, _ = await application_stack(
        stack, monkeypatch, tmp_path, current_lap=4
    )
    for number in range(1, 4):
        await database.save_lap(lap(number), [])
    captured = {}
    outer_origin = {"outer": "caller context must survive"}

    async def transcribe(_source, _names):
        assert lap_note_origin.get() is outer_origin
        await database.save_lap(lap(4), [])
        await store.update(current_lap=5)
        return "This lap was ruined by traffic in sector two"

    async def ask(_text):
        captured["origin"] = lap_note_origin.get()
        captured["note"] = await tools.record_lap_observation(
            "Ruined by traffic in sector two",
            category="traffic",
            reference="current_lap",
            exclude_from_pace=True,
        )
        return "Saved the report for lap four."

    async def synthesize(_reply, target, _format):
        assert lap_note_origin.get() is outer_origin
        target.write_bytes(b"isolated test audio")

    monkeypatch.setattr(
        application,
        "audio",
        SimpleNamespace(transcribe=transcribe, synthesize=synthesize),
    )
    monkeypatch.setattr(application, "brain", SimpleNamespace(ask=ask))
    token = lap_note_origin.set(outer_origin)
    try:
        result = await application.browser_voice(
            UploadFile(filename="clip.webm", file=io.BytesIO(b"test clip"))
        )
        assert lap_note_origin.get() is outer_origin
    finally:
        lap_note_origin.reset(token)
    assert result["reply"] == "Saved the report for lap four."
    assert captured["origin"]["current_lap"] == 4
    assert (await store.peek("current_lap"))["current_lap"] == 5
    assert captured["note"]["saved"] is True
    report = await engineering.report(session_id(53041))
    intended = next(item["id"] for item in report["laps"] if item["lap_num"] == 4)
    assert captured["note"]["note"]["lap_ids"] == [intended]
    assert captured["note"]["note"]["pending_laps"] == []
    assert report["lap_notes"][0]["lap_ids"] == [intended]
    assert application.browser_audio_origin["current_lap"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("provider failed"), asyncio.CancelledError()]
)
async def test_browser_note_origin_is_restored_when_the_engineer_fails_or_is_cancelled(
    stack, monkeypatch, tmp_path, failure
):
    application, _, _, _, _, _ = await application_stack(
        stack, monkeypatch, tmp_path, current_lap=4
    )
    outer_origin = {"outer": "preserved"}
    synthesize = AsyncMock()

    async def ask(_text):
        assert lap_note_origin.get()["current_lap"] == 4
        raise failure

    monkeypatch.setattr(application, "brain", SimpleNamespace(ask=ask))
    monkeypatch.setattr(
        application,
        "audio",
        SimpleNamespace(
            transcribe=AsyncMock(return_value="note this lap"), synthesize=synthesize
        ),
    )
    token = lap_note_origin.set(outer_origin)
    try:
        expected = (
            asyncio.CancelledError
            if isinstance(failure, asyncio.CancelledError)
            else HTTPException
        )
        with pytest.raises(expected) as raised:
            await application.browser_voice(
                UploadFile(filename="clip.webm", file=io.BytesIO(b"test clip"))
            )
        if isinstance(raised.value, HTTPException):
            assert raised.value.status_code == 503
        assert lap_note_origin.get() is outer_origin
    finally:
        lap_note_origin.reset(token)
    synthesize.assert_not_awaited()
    assert application.browser_audio_origin is None
