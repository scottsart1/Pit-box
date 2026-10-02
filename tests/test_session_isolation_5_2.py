from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall.brain import EngineerBrain
from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService
from pitwall.proactive import ProactiveEngineer
from pitwall.session_assembler import EventStamp, SessionAssembler, SessionEvent
from pitwall.session_guard import SessionChangedError
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol
from pitwall.voice import NativeVoiceController


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["uid", "restart", "flashback"])
async def test_slow_question_is_cancelled_and_cannot_enter_next_session_radio(
    stack, monkeypatch, boundary
):
    store, database, *_, tools = stack
    await store.mark_packet(2026, 26, 100)
    await store.update(track_id=10)
    brain = EngineerBrain(store, tools, database)
    entered = asyncio.Event()

    async def delayed(_):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(brain, "_fast_answer", delayed)
    task = asyncio.create_task(brain.ask("How was my last lap?"))
    await asyncio.wait_for(entered.wait(), 2)
    if boundary == "uid":
        await store.mark_packet(2026, 26, 200)
    else:
        await store.synchronize_session_epoch(
            100, int(boundary == "restart"), int(boundary == "flashback")
        )
    with pytest.raises(SessionChangedError):
        await asyncio.wait_for(task, 2)
    assert not [
        entry
        for entry in (await store.peek("radio_log"))["radio_log"]
        if entry["role"] == "engineer"
    ]


@pytest.mark.asyncio
async def test_analysis_of_old_lap_is_saved_without_replacing_live_analysis(
    stack, monkeypatch
):
    store, database, strategy, _, analysis, _ = stack
    await store.mark_packet(2026, 26, 100)
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_best(_):
        entered.set()
        await release.wait()
        return {"lap_time_ms": 100000, "trace": [{"t": 0, "d": 0}, {"t": 1, "d": 100}]}

    monkeypatch.setattr(database, "get_personal_best", old_best)
    recompute = AsyncMock()
    monkeypatch.setattr(strategy, "recompute", recompute)
    lap = {
        "session_uid": 100,
        "session_generation": 0,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Practice",
        "mode_profile": "practice",
        "lap_num": 4,
        "lap_time_ms": 101000,
        "valid": True,
        "compound": "MEDIUM",
        "trace": [],
    }
    task = asyncio.create_task(analysis.process_lap(lap))
    await asyncio.wait_for(entered.wait(), 2)
    await store.mark_packet(2026, 26, 200)
    await store.update(track_id=3, analysis={"marker": "new circuit"})
    release.set()
    await asyncio.wait_for(task, 5)
    state = await store.snapshot_analysis()
    assert state["analysis"] == {"marker": "new circuit"}
    assert state["live_delta_reference"] == ""
    recompute.assert_not_awaited()
    assert (await EngineeringService(database).report(session_id(100)))["runs"][0][
        "laps"
    ][0]["lap_num"] == 4


@pytest.mark.asyncio
async def test_queued_proactive_narration_is_discarded_after_track_switch(
    stack, monkeypatch
):
    store, database, strategy, setup, _, tools = stack
    await store.mark_packet(2026, 26, 100)
    await store.update(
        speed_kph=200, throttle=1, connected=True, current_lap=5, mode_profile="race"
    )
    voice = SimpleNamespace(
        speak_text=AsyncMock(return_value=True), is_busy=False, realtime_active=False
    )
    brain = EngineerBrain(store, tools, database)
    engineer = ProactiveEngineer(store, brain, voice, setup, strategy)
    await engineer._reset_for_session(100)
    engineer._enqueue("tyre_wear", {"wear": 90}, critical=True)
    monkeypatch.setattr(engineer, "_safe_to_speak", lambda *_: True)
    monkeypatch.setattr(engineer, "_relevance_reason", lambda *_: None)
    monkeypatch.setattr(engineer, "_prune", lambda *_: None)
    entered = asyncio.Event()

    async def slow(*_):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(engineer, "_narrate", slow)
    task = asyncio.create_task(engineer._deliver(await store.snapshot_radio()))
    await asyncio.wait_for(entered.wait(), 2)
    await store.mark_packet(2026, 26, 200)
    await asyncio.wait_for(task, 2)
    voice.speak_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_realtime_and_queued_clips_close_at_session_boundary(
    stack, monkeypatch, tmp_path
):
    from pitwall.config import settings

    store, database, *_, tools = stack
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    await store.mark_packet(2026, 26, 100)
    audio = SimpleNamespace(stop_playback=lambda: None)
    voice = NativeVoiceController(store, EngineerBrain(store, tools, database), audio)
    voice.realtime = SimpleNamespace(close=AsyncMock())
    voice._pending_clips.append("old clip")
    voice._wake_preroll.append("old audio")
    watcher = asyncio.create_task(voice._watch_session())
    await asyncio.sleep(0)
    await store.mark_packet(2026, 26, 200)
    for _ in range(10):
        await asyncio.sleep(0)
        if voice.realtime.close.await_count:
            break
    assert not voice._pending_clips and not voice._wake_preroll
    voice.realtime.close.assert_awaited_once_with("telemetry session changed")
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_packet_from_retired_uid_cannot_restore_old_track():
    store = StateStore()
    protocol = F1DatagramProtocol(store)

    def packet(uid):
        return SimpleNamespace(
            header=SimpleNamespace(session_uid=uid, packet_format=2026, game_year=26)
        )

    await protocol._handle(packet(100))
    await protocol._handle(packet(200))
    await protocol._handle(packet(100))
    assert (await store.peek("session_uid"))["session_uid"] == 200


def test_same_uid_new_track_or_session_type_creates_new_archive_epoch():
    assembler = SessionAssembler()

    def event(track, kind):
        return SessionEvent(
            EventStamp(100, 10, 10, 100, 1, 1), track_id=track, session_type=kind
        )

    assembler.consume(event(10, 1))
    first = assembler.session.id
    assembler.consume(event(3, 1))
    assert assembler.session.id != first and assembler.session.restart_epoch == 1
    assembler.consume(event(3, 10))
    assert assembler.session.restart_epoch == 2


@pytest.mark.asyncio
async def test_browser_recordings_cannot_overwrite_or_serve_audio_after_session_change(
    stack, monkeypatch, tmp_path
):
    import io

    from fastapi import HTTPException, UploadFile

    from pitwall import app as application

    store = stack[0]
    await store.mark_packet(2026, 26, 100)
    entered, release = asyncio.Event(), asyncio.Event()

    async def synthesize(_reply, target, _format):
        entered.set()
        await release.wait()
        target.write_bytes(b"old session audio")

    audio = SimpleNamespace(
        transcribe=AsyncMock(return_value="last lap"), synthesize=synthesize
    )
    monkeypatch.setattr(application, "store", store)
    monkeypatch.setattr(application, "audio", audio)
    monkeypatch.setattr(
        application, "brain", SimpleNamespace(ask=AsyncMock(return_value="old reply"))
    )
    monkeypatch.setattr(application, "browser_audio_lock", asyncio.Lock())
    monkeypatch.setattr(application, "browser_audio_origin", None)
    monkeypatch.setattr(application.settings, "data_dir", tmp_path)
    clip = lambda: UploadFile(filename="clip.webm", file=io.BytesIO(b"clip"))
    first = asyncio.create_task(application.browser_voice(clip()))
    await asyncio.wait_for(entered.wait(), 2)
    queued = asyncio.create_task(application.browser_voice(clip()))
    await asyncio.sleep(0)
    await store.mark_packet(2026, 26, 200)
    release.set()
    results = await asyncio.wait_for(
        asyncio.gather(first, queued, return_exceptions=True), 2
    )
    assert all(
        isinstance(error, HTTPException) and error.status_code == 409
        for error in results
    )
    assert audio.transcribe.await_count == 1
    with pytest.raises(HTTPException) as error:
        await application.latest_audio()
    assert error.value.status_code == 409
