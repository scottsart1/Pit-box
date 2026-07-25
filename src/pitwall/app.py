from __future__ import annotations

import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .analysis import AnalysisEngine
from .audio import AudioService
from .brain import EngineerBrain
from .config import settings
from .database import PitWallDatabase
from .proactive import ProactiveEngineer
from .setup_advisor import SetupAdvisor
from .state import StateStore
from .strategy import StrategyEngine
from .tools import TelemetryTools
from .udp import F1DatagramProtocol, TRACKS, classify_session
from .voice import NativeVoiceController

store = StateStore()
database = PitWallDatabase(settings.data_dir / "pitwall.sqlite3")
strategy = StrategyEngine(store, database)
setup_advisor = SetupAdvisor(store, database)
analysis = AnalysisEngine(store, database, strategy)
tools = TelemetryTools(store, database, analysis, strategy, setup_advisor)
brain = EngineerBrain(store, tools, database)
audio = AudioService()
voice: NativeVoiceController | None = None
proactive: ProactiveEngineer | None = None
udp_transport: asyncio.DatagramTransport | None = None
watchdog_task: asyncio.Task[None] | None = None
event_persistence_task: asyncio.Task[None] | None = None


async def _connection_watchdog() -> None:
    last_session_write = 0.0
    classified_sessions: set[int] = set()
    while True:
        await asyncio.sleep(1.0)
        await store.mark_disconnected_if_stale(settings.disconnect_after_s)
        snapshot = await store.snapshot_live()
        loop_time = asyncio.get_running_loop().time()
        session_uid = int(snapshot.get("session_uid") or 0)
        # A finished session must be recorded immediately: waiting for the next
        # periodic write risks losing the result if Pit Wall is closed straight
        # after the chequered flag.
        classification = snapshot.get("final_classification") or {}
        newly_classified = bool(
            session_uid
            and int(classification.get("position", 0) or 0) > 0
            and session_uid not in classified_sessions
        )
        if session_uid and (
            newly_classified or loop_time - last_session_write >= 10
        ):
            await database.upsert_session(snapshot)
            last_session_write = loop_time
            if newly_classified:
                classified_sessions.add(session_uid)


async def _event_persistence_worker() -> None:
    while True:
        event = await store.event_queue.get()
        try:
            await database.save_queued_session_event(event)
        finally:
            store.event_queue.task_done()


async def _persist_finished_session() -> None:
    """Persist the final result in the same packet-handling cycle."""
    await database.upsert_session(await store.snapshot_live())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global voice, proactive, udp_transport, watchdog_task, event_persistence_task
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await database.initialize()
    router_status = brain.router.status()
    initial_provider = str(router_status["resolved_provider"])
    await store.update(
        llm_provider=initial_provider,
        llm_model=(
            settings.deepseek_fast_model
            if initial_provider == "deepseek"
            else settings.model
        ),
    )
    await analysis.start()
    voice = NativeVoiceController(store, brain, audio)
    await voice.initialize()
    proactive = ProactiveEngineer(store, brain, voice, setup_advisor, strategy)
    await proactive.start()
    loop = asyncio.get_running_loop()
    try:
        udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: F1DatagramProtocol(
                store,
                voice.on_button_status,
                on_player_lap_history=database.backfill_lap_sectors,
                on_final_classification=_persist_finished_session,
            ),
            local_addr=(settings.udp_host, settings.udp_port),
        )
    except OSError as exc:
        await store.update(
            last_error=f"UDP port {settings.udp_port} could not be opened: {exc}"
        )
    watchdog_task = asyncio.create_task(_connection_watchdog())
    event_persistence_task = asyncio.create_task(
        _event_persistence_worker(), name="pitwall-event-persistence"
    )
    yield
    if watchdog_task:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
    if event_persistence_task:
        event_persistence_task.cancel()
        try:
            await event_persistence_task
        except asyncio.CancelledError:
            pass
    if proactive:
        await proactive.stop()
    if voice:
        await voice.shutdown()
    await analysis.stop()
    if udp_transport:
        udp_transport.close()


app = FastAPI(title="Pit Wall", version="3.4.1", lifespan=lifespan)


class AskRequest(BaseModel):
    text: str


class CompareRequest(BaseModel):
    text: str


class SetupRequest(BaseModel):
    profile: str = "hybrid"
    track_id: int | None = None


class ProactiveRequest(BaseModel):
    enabled: bool = True
    cadence_laps: int = 2


class SessionModeRequest(BaseModel):
    mode: str = "auto"


class WakeRequest(BaseModel):
    enabled: bool = True


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    path = Path(__file__).resolve().parents[2] / "static" / "index.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/overlay", response_class=HTMLResponse)
async def overlay() -> HTMLResponse:
    """Transparent OBS/second-screen overlay. Append ?bg=1 for an opaque panel."""
    path = Path(__file__).resolve().parents[2] / "static" / "overlay.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, object]:
    snapshot = await store.snapshot_live()
    return {
        "ok": True,
        "version": app.version,
        "udp_listener": udp_transport is not None,
        "telemetry_connected": snapshot["connected"],
        "telemetry_stale": snapshot["telemetry_stale"],
        "openai_key_configured": bool(settings.api_key),
        "deepseek_key_configured": bool(settings.deepseek_key),
        "model": settings.model,
        "llm": brain.router.status(),
        "stt_model": settings.stt_model,
        "tts_model": settings.tts_model,
        "ptt_status": snapshot["ptt_status"],
        "wake_enabled": snapshot["wake_enabled"],
        "wake_status": snapshot["wake_status"],
        "wake_phrase": snapshot["wake_phrase"],
        "radio_indicator": snapshot["radio_indicator"],
        "radio_latency": snapshot["radio_latency"],
        "proactive": snapshot["proactive"],
        "database": str(database.path),
        "last_error": snapshot["last_error"],
    }


@app.get("/api/tracks")
async def tracks() -> dict[str, object]:
    return {"tracks": [{"id": int(track_id), "name": str(name)} for track_id, name in sorted(TRACKS.items())]}


@app.get("/api/history")
async def history(
    scope: str = Query(default="current_session"),
    limit: int = Query(default=50, ge=1, le=250),
) -> dict[str, object]:
    snapshot = await store.snapshot_live()
    session_uid = int(snapshot.get("session_uid", 0)) if scope == "current_session" else None
    track_id = int(snapshot.get("track_id", -1)) if scope in {"current_session", "current_track"} else None
    return await database.history_query(track_id=track_id, session_uid=session_uid, limit=limit)


@app.get("/api/racing-line")
async def racing_line() -> dict[str, object]:
    return await analysis.get_racing_line_analysis("last")

@app.get("/api/state")
async def get_state() -> dict[str, object]:
    return await store.snapshot_live()


@app.get("/api/review")
async def review(track_id: int | None = Query(default=None)) -> dict[str, object]:
    snapshot = await store.snapshot_live()
    selected = int(track_id if track_id is not None else snapshot.get("track_id", -1))
    return await database.track_review(selected, 50)


@app.get("/api/export/laps.csv")
async def export_laps_csv(track_id: int | None = Query(default=None)) -> PlainTextResponse:
    """Export stored laps for a track (or the current track) as CSV."""
    snapshot = await store.snapshot_live()
    selected = int(track_id if track_id is not None else snapshot.get("track_id", -1))
    laps = await database.recent_laps(selected, 500)
    columns = [
        "session_uid", "lap_num", "lap_time_ms", "valid", "compound",
        "tyre_age_start", "tyre_age_end", "s1_ms", "s2_ms", "s3_ms",
        "fuel_start_kg", "fuel_end_kg", "position", "created_at",
    ]
    lines = [",".join(columns)]
    for lap in laps:
        lines.append(",".join(str(lap.get(column, "")) for column in columns))
    body = "\n".join(lines) + "\n"
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="pitwall_laps_{selected}.csv"'},
    )


@app.get("/api/export/session.json")
async def export_session_json(
    scope: str = Query(default="current_session"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> JSONResponse:
    """Export the full stored history bundle as a downloadable JSON file."""
    snapshot = await store.snapshot_live()
    session_uid = int(snapshot.get("session_uid", 0)) if scope == "current_session" else None
    track_id = int(snapshot.get("track_id", -1)) if scope in {"current_session", "current_track"} else None
    data = await database.history_query(track_id=track_id, session_uid=session_uid, limit=limit)
    return JSONResponse(
        data,
        headers={"Content-Disposition": 'attachment; filename="pitwall_session.json"'},
    )


@app.websocket("/ws")
async def websocket_state(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(await store.snapshot_live())
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        pass


@app.post("/api/ask")
async def ask(request: AskRequest) -> dict[str, str]:
    if not request.text.strip():
        raise HTTPException(400, "Text is required")
    await store.update(engineer_status="thinking", last_error="")
    try:
        reply = await brain.ask(request.text.strip())
        return {"reply": reply}
    except Exception as exc:
        await store.update(last_error=str(exc), engineer_status="error")
        raise HTTPException(503, str(exc)) from exc
    finally:
        snapshot = await store.snapshot_live()
        if not snapshot.get("ptt_pressed"):
            await store.update(engineer_status="standing by")


@app.get("/api/llm/providers")
async def llm_providers() -> dict[str, object]:
    return brain.router.status()


@app.post("/api/llm/compare")
async def compare_llms(request: CompareRequest) -> dict[str, object]:
    if not settings.llm_compare_enabled:
        raise HTTPException(403, "LLM comparison is disabled in .env")
    if not request.text.strip():
        raise HTTPException(400, "Text is required")
    try:
        return await brain.compare(request.text.strip())
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/llm/shakedown")
async def llm_shakedown() -> dict[str, object]:
    """Run a small, explicit live provider wire-contract check."""
    try:
        return await brain.router.shakedown()
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/ptt/calibrate")
async def calibrate_ptt() -> dict[str, object]:
    if voice is None:
        raise HTTPException(503, "Voice controller not started")
    return await voice.begin_calibration()


@app.post("/api/wake/config")
async def wake_config(request: WakeRequest) -> dict[str, object]:
    if voice is None:
        raise HTTPException(503, "Voice controller not started")
    return await voice.configure_wake(request.enabled)


@app.post("/api/setup/recommend")
async def setup_recommendation(request: SetupRequest) -> dict[str, object]:
    result = await setup_advisor.generate(request.profile, request.track_id)
    if not result.get("available"):
        raise HTTPException(409, result.get("reason", "Setup unavailable"))
    return result


@app.post("/api/proactive/config")
async def proactive_config(request: ProactiveRequest) -> dict[str, object]:
    if proactive is None:
        raise HTTPException(503, "Proactive engineer not started")
    return await proactive.configure(request.enabled, request.cadence_laps)


@app.post("/api/proactive/test")
async def proactive_test() -> dict[str, object]:
    if proactive is None:
        raise HTTPException(503, "Proactive engineer not started")
    result = await proactive.queue_test_update()
    if not result.get("ok"):
        raise HTTPException(409, str(result.get("reason", "Test call unavailable")))
    return result


@app.post("/api/session/mode")
async def session_mode(request: SessionModeRequest) -> dict[str, object]:
    mode = request.mode.strip().lower()
    valid = {"auto", "race", "sprint", "qualifying", "practice", "time_trial"}
    if mode not in valid:
        raise HTTPException(400, f"Mode must be one of: {', '.join(sorted(valid))}")
    snapshot = await store.snapshot_analysis()
    label, profile, source, reason = classify_session(
        raw_type_id=int(snapshot.get("raw_session_type_id", 0)),
        total_laps=int(snapshot.get("total_laps", 0)),
        current_lap=int(snapshot.get("current_lap", 0)),
        session_time_left_s=int(snapshot.get("session_time_left_s", 0)),
        session_duration_s=int(snapshot.get("session_duration_s", 0)),
        session_length_id=int(snapshot.get("session_length_id", 0)),
        weekend_structure=snapshot.get("weekend_structure", []),
        override=mode,
    )
    await store.update(
        session_mode_override=mode,
        session_type=label,
        mode_profile=profile,
        session_detection_source=source,
        session_detection_reason=reason,
    )
    await strategy.recompute()
    return {
        "mode": mode,
        "session_type": label,
        "mode_profile": profile,
        "source": source,
        "reason": reason,
    }


@app.post("/api/strategy/recompute")
async def recompute_strategy() -> dict[str, object]:
    return await strategy.recompute()


@app.post("/api/voice")
async def browser_voice(file: UploadFile = File(...)) -> dict[str, str]:
    suffix = Path(file.filename or "clip.webm").suffix or ".webm"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / f"input{suffix}"
        source.write_bytes(await file.read())
        try:
            snapshot = await store.snapshot_live()
            text = await audio.transcribe(
                source,
                [driver["name"] for driver in snapshot["drivers"]],
            )
            if not text:
                raise HTTPException(422, "No speech detected")
            reply = await brain.ask(text)
            target = settings.data_dir / "latest_engineer.mp3"
            await audio.synthesize(reply, target, "mp3")
            return {
                "transcript": text,
                "reply": reply,
                "audio_url": "/api/latest-audio",
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/latest-audio")
async def latest_audio() -> FileResponse:
    candidates = [
        settings.data_dir / "latest_engineer.mp3",
        settings.data_dir / "latest_engineer.wav",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise HTTPException(404, "No generated audio yet")
    return FileResponse(
        path,
        media_type="audio/mpeg" if path.suffix == ".mp3" else "audio/wav",
    )
