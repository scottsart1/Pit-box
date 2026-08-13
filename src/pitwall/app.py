from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time as _time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analysis import AnalysisEngine
from .analysis_jobs import AnalysisJobService
from .api.analysis import create_analysis_router
from .api.credentials import create_credentials_router
from .api.field import create_field_router
from .api.live import create_live_router
from .api.network import create_network_router
from .api.sessions import create_sessions_router
from .api.storage import create_storage_router
from .api.track_models import create_track_models_router
from .audio import AudioService
from .brain import EngineerBrain
from .briefing import BriefingEngine
from .capture_lifecycle import SessionCaptureCoordinator
from .capture_service import CaptureService
from .catalog import session_id
from .comparison_service import ComparisonService
from .config import settings
from .database import PitWallDatabase
from .field_service import FieldAnalysisService
from .forwarding import DatagramForwarder
from .full_field_archive import FullFieldArchiveService, cars_in_trace_scope
from .line_insights import line_findings
from .network_profiles import NetworkProfileRepository
from .network_service import ListenerBindError, NetworkService
from .networking import PacketHealthTracker
from .prerace import PreRacePlanner
from .proactive import ProactiveEngineer
from .race_plan import (
    PlanError,
    compound_rule_ok,
    describe_plan,
    normalise_plan,
)
from .realtime import RealtimeRadio
from .session_assembler import SessionAssembler
from .settings_service import (
    PREFERENCE_KEY as APP_SETTINGS_KEY,
    apply_runtime,
    apply_saved_overrides,
    coerce,
    settings_view,
)
from .setup_advisor import SetupAdvisor
from .state import StateStore
from .storage_service import RetentionPolicy, StorageService
from .strategy import StrategyEngine
from .tools import TelemetryTools
from .trace_archive import TraceArchiveService
from .trace_store import RecoveryReport, TraceStore
from .track_model_service import TrackModelService
from .udp import TRACKS, F1DatagramProtocol, classify_session
from .voice import NativeVoiceController
from .web_security import LanAccessMiddleware, is_loopback_host

log = logging.getLogger(__name__)

store = StateStore()
database = PitWallDatabase(settings.data_dir / "pitwall.sqlite3")
# Saved dashboard settings override .env installation defaults. This must
# run before any service below constructs from `settings`, which is why it
# reads SQLite synchronously here instead of through the async wrapper.
apply_saved_overrides(settings, database.path)
network_profiles = NetworkProfileRepository(database.path)
trace_store = TraceStore(
    settings.trace_dir,
    cache_max_bytes=settings.trace_cache_max_mb * 1024 * 1024,
)
trace_archive = TraceArchiveService(database, trace_store)
comparison_service = ComparisonService(database.path, trace_store)
field_service = FieldAnalysisService(database.path, trace_store=trace_store)
track_model_service = TrackModelService(database.path, trace_store, settings.data_dir)
analysis_jobs = AnalysisJobService(
    database.path,
    comparison_service,
    track_model_builder=track_model_service,
    worker_count=settings.analysis_workers,
)
capture_service = CaptureService(
    settings.capture_dir,
    queue_size=settings.capture_queue_size,
    max_file_bytes=round(settings.capture_max_gb * 1024**3),
    minimum_free_bytes=round(settings.capture_min_free_gb * 1024**3),
)
capture_coordinator = SessionCaptureCoordinator(
    capture_service,
    database.catalog,
    settings.capture_dir,
)
storage_service = StorageService(
    database.path,
    settings.data_dir,
    policy=RetentionPolicy(
        max_bytes=round(settings.capture_max_gb * 1024**3),
        max_age_days=settings.retention_days,
        minimum_free_bytes=round(settings.capture_min_free_gb * 1024**3),
    ),
    capture_service=capture_service,
)
full_field_archive = FullFieldArchiveService(
    database.path,
    trace_store,
    queue_size=settings.trace_ingest_queue_size,
)
session_assembler = SessionAssembler(
    batch_sink=full_field_archive.submit,
    invalidation_sink=full_field_archive.submit,
    field_trace_hz=settings.field_trace_hz,
)
packet_health = PacketHealthTracker(
    reorder_window_s=settings.packet_loss_confirm_ms / 1000.0,
    recent_frame_capacity=max(64, settings.packet_reorder_window * 128),
)
forwarder = DatagramForwarder(queue_size=settings.forward_queue_size)
strategy = StrategyEngine(store, database)
setup_advisor = SetupAdvisor(store, database)
analysis = AnalysisEngine(store, database, strategy, trace_archive)
tools = TelemetryTools(
    store,
    database,
    analysis,
    strategy,
    setup_advisor,
    session_catalog=database.catalog,
    comparison_service=comparison_service,
    field_analysis_service=field_service,
)
brain = EngineerBrain(store, tools, database)
briefing = BriefingEngine(store, database, analysis, setup_advisor, tools)
prerace = PreRacePlanner(store, strategy)
audio = AudioService()
voice: NativeVoiceController | None = None
proactive: ProactiveEngineer | None = None
network_service: NetworkService
watchdog_task: asyncio.Task[None] | None = None
event_persistence_task: asyncio.Task[None] | None = None
maintenance_task: asyncio.Task[None] | None = None
catalog_task: asyncio.Task[None] | None = None
corner_rebuild_task: asyncio.Task[None] | None = None
interfaces_task: asyncio.Task[None] | None = None


async def _connection_watchdog() -> None:
    last_session_write = 0.0
    classified_sessions: set[int] = set()
    previous_catalog_session_id: str | None = None
    while True:
        await asyncio.sleep(1.0)
        await store.mark_disconnected_if_stale(
            settings.disconnect_after_s, settings.presence_grace_s
        )
        snapshot = await store.snapshot_live()
        # Keep full per-car traces for the cars the analysis actually reads
        # back — in a race that is the player, the teammate, the podium and
        # the cars the player started among. Practice and qualifying keep
        # everything, because full-field lap comparison is their whole point.
        # PITWALL_FIELD_TRACE_SCOPE=all keeps every car in races too.
        full_field_archive.set_trace_scope(
            None
            if settings.field_trace_scope == "all"
            else cars_in_trace_scope(snapshot)
        )
        loop_time = asyncio.get_running_loop().time()
        session_uid = int(snapshot.get("session_uid") or 0)
        current_catalog_session_id = (
            session_id(
                session_uid,
                int(snapshot.get("restart_epoch", 0) or 0),
            )
            if session_uid
            else None
        )
        if (
            previous_catalog_session_id is not None
            and current_catalog_session_id != previous_catalog_session_id
        ):
            await database.catalog.finalize_session(
                previous_catalog_session_id, status="incomplete"
            )
        if current_catalog_session_id is not None:
            previous_catalog_session_id = current_catalog_session_id
        # A finished session must be recorded immediately: waiting for the next
        # periodic write risks losing the result if Your Pit Box is closed straight
        # after the chequered flag.
        classification = snapshot.get("final_classification") or {}
        newly_classified = bool(
            session_uid
            and int(classification.get("position", 0) or 0) > 0
            and session_uid not in classified_sessions
        )
        if session_uid and (newly_classified or loop_time - last_session_write >= 10):
            await database.upsert_session(snapshot)
            last_session_write = loop_time
            if newly_classified:
                classified_sessions.add(session_uid)
                if current_catalog_session_id is not None:
                    await database.catalog.finalize_session(
                        current_catalog_session_id, status="complete"
                    )


async def _event_persistence_worker() -> None:
    while True:
        event = await store.event_queue.get()
        try:
            await database.save_queued_session_event(event)
        finally:
            store.event_queue.task_done()


async def _persist_briefing(kind: str, payload: dict[str, object]) -> dict[str, object]:
    try:
        text = await brain.narrate_briefing(kind, payload)
    except Exception as exc:
        log.warning("Briefing narration fell back to deterministic text: %s", exc)
        text = briefing.fallback_text(kind, payload)
    snapshot = await store.snapshot_analysis()
    save_state = dict(snapshot)
    if payload.get("track_id") is not None:
        save_state["track_id"] = int(payload["track_id"])
    await database.save_briefing(save_state, kind, payload, text)
    briefings = dict(snapshot.get("briefings", {}))
    briefings[kind] = {"payload": payload, "text": text}
    await store.update(briefings=briefings)
    return {"kind": kind, "payload": payload, "text": text}


async def _persist_finished_session() -> None:
    """Persist and debrief the final result in the packet-handling cycle."""
    snapshot = await store.snapshot_live()
    await database.upsert_session(snapshot)
    game_uid = int(snapshot.get("session_uid", 0) or 0)
    if game_uid:
        await database.catalog.finalize_session(
            session_id(game_uid, int(snapshot.get("restart_epoch", 0) or 0)),
            status="complete",
        )
    try:
        result = await _persist_briefing("post_race", await briefing.post_race())
        if voice is not None:
            await voice.speak_text(str(result["text"]))
    except Exception as exc:
        log.warning("Post-race debrief could not be generated: %s", exc)


async def _persist_qualifying_lap() -> None:
    try:
        result = await _persist_briefing(
            "post_qualifying_lap", await briefing.post_qualifying_lap()
        )
        if voice is not None:
            await voice.speak_text(str(result["text"]))
    except Exception as exc:
        log.warning("Qualifying-lap debrief could not be generated: %s", exc)


def _create_udp_protocol() -> F1DatagramProtocol:
    """Build the proven parser behind the managed 4.2 network lifecycle."""

    return F1DatagramProtocol(
        store,
        voice.on_button_status if voice is not None else None,
        on_player_lap_history=database.backfill_lap_sectors,
        on_final_classification=_persist_finished_session,
        on_qualifying_lap=_persist_qualifying_lap,
        packet_health=packet_health,
        capture_service=capture_service,
        session_assembler=session_assembler,
        capture_mode=settings.capture_mode,
        on_session_key_change=capture_coordinator.observe_session,
    )


network_service = NetworkService(
    _create_udp_protocol,
    bind_host=settings.udp_bind_host,
    port=settings.udp_port,
    stale_after_ms=max(250, round(settings.disconnect_after_s * 1000)),
    packet_health=packet_health,
    forwarder=forwarder,
    delegate_tracks_health=True,
    profile_repository=network_profiles,
)
tools.network_service = network_service


async def _startup_maintenance() -> None:
    """Reclaim database space in the background, never blocking the session.

    A large historic database can take a while to VACUUM, so this runs as a
    task rather than inside lifespan startup. Failure is non-fatal.
    """
    try:
        report = await database.maintain(
            keep_trace_sessions=settings.db_keep_trace_sessions
        )
        freed = int(report.get("size_before_bytes", 0)) - int(
            report.get("size_after_bytes", 0)
        )
        log.info("Database maintenance reclaimed %.1f MB: %s", freed / 1e6, report)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Database maintenance skipped: %s", exc)


async def _startup_catalog_sync() -> None:
    """Backfill legacy laps in resumable batches without delaying startup."""

    try:
        while True:
            report = await database.catalog.sync_legacy(batch_size=250)
            if not int(report.get("laps_remaining", 0)):
                return
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("4.2 Library backfill deferred: %s", exc)


async def _startup_corner_rebuild() -> None:
    """Build canonical turn models for tracks that lack one.

    Laps recorded before the milli-g motion fix carry one lap-length "corner"
    each — the traces were always correct, only their segmentation was wrong,
    so the real per-corner record is fully recoverable. Each track is rebuilt
    exactly once (the stored turn model is the marker); a track first raced
    after this ships gets its model on the next launch with enough laps.
    Runs per track so the database lock is released between tracks.
    """

    try:
        total = 0
        for track_id in await database.tracks_with_traces():
            rebuilt = await database.rebuild_track_corners(
                track_id, analysis.segment_corners
            )
            if rebuilt:
                log.info(
                    "Turn model built for track %d: %d lap(s) re-measured",
                    track_id,
                    rebuilt,
                )
            total += rebuilt
            await asyncio.sleep(0)
        if total:
            log.info("Corner history rebuilt from traces: %d lap(s)", total)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Corner history rebuild deferred: %s", exc)


async def _startup_warm_interfaces() -> None:
    """Populate the adapter cache before the driver opens Connection.

    Windows adapter discovery spawns PowerShell, which on a cold start can take
    well over ten seconds. Doing it here means the first Connection Center
    request is served from cache instead of waiting on that process.
    """

    try:
        discovery = await network_service.interfaces()
        log.info(
            "Adapter discovery warmed: %d interface(s) via %s",
            len(discovery.interfaces),
            discovery.source,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Adapter discovery warm-up deferred: %s", exc)


def _report_trace_recovery(recovery: RecoveryReport) -> None:
    """Surface recoverable trace-store damage without aborting application startup."""

    if recovery.invalid_temporary_files:
        log.warning(
            "Trace-store recovery left invalid temporary files: %s",
            recovery.invalid_temporary_files,
        )
    if recovery.orphan_chunks:
        log.warning(
            "Trace-store recovery found unreferenced chunks: %s",
            recovery.orphan_chunks,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global voice, proactive, watchdog_task, event_persistence_task
    global maintenance_task, catalog_task, interfaces_task, session_assembler
    global corner_rebuild_task
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await database.initialize()
    stale_sessions = await database.catalog.finalize_recording_sessions()
    if stale_sessions:
        log.info(
            "Recovered %d session(s) left recording by an earlier exit",
            stale_sessions,
        )
    capture_recovery = await capture_service.recover_pending()
    for report in capture_recovery.recovered:
        try:
            relative_path = report.path.resolve().relative_to(
                settings.capture_dir.resolve()
            )
            await database.catalog.register_raw_capture(
                None,
                relative_path.as_posix(),
                report,
            )
        except Exception as exc:
            log.warning("Recovered capture could not be catalogued: %s", exc)
    if capture_recovery.unresolved:
        log.warning(
            "Capture recovery left %d unresolved file(s): %s",
            len(capture_recovery.unresolved),
            capture_recovery.unresolved,
        )
    try:
        await network_service.load_persisted_profile()
    except Exception as exc:
        log.warning("Saved network profile could not be loaded: %s", exc)
    if session_assembler.closed:
        session_assembler = SessionAssembler(
            batch_sink=full_field_archive.submit,
            invalidation_sink=full_field_archive.submit,
            field_trace_hz=settings.field_trace_hz,
        )
    await full_field_archive.start()
    recovery = await asyncio.to_thread(trace_store.recover_pending_writes)
    _report_trace_recovery(recovery)
    catalog_task = asyncio.create_task(
        _startup_catalog_sync(), name="pitwall-catalog-backfill"
    )
    corner_rebuild_task = asyncio.create_task(
        _startup_corner_rebuild(), name="pitwall-corner-rebuild"
    )
    interfaces_task = asyncio.create_task(
        _startup_warm_interfaces(), name="pitwall-interface-warm"
    )
    if settings.db_maintenance_on_start:
        maintenance_task = asyncio.create_task(
            _startup_maintenance(), name="pitwall-db-maintenance"
        )
    saved_preferences = await database.load_preference("driver_preferences", None)
    if isinstance(saved_preferences, dict):
        risk = str(saved_preferences.get("strategy_risk_appetite", "balanced"))
        if risk not in {"conservative", "balanced", "aggressive"}:
            risk = "balanced"
        await store.update(
            driver_preferences=saved_preferences,
            strategy_risk_appetite=risk,
        )
    saved_instructions = await database.load_preference("standing_instructions", None)
    if isinstance(saved_instructions, list):
        await store.update(standing_instructions=saved_instructions)
    router_status = brain.router.status()
    initial_provider = str(router_status["resolved_provider"])
    await store.update(llm_provider=initial_provider, llm_model=settings.model)
    await analysis.start()
    await analysis_jobs.start()
    voice = NativeVoiceController(store, brain, audio)
    await voice.initialize()
    proactive = ProactiveEngineer(store, brain, voice, setup_advisor, strategy)
    await proactive.start()
    listener_config = network_service.listener_snapshot()
    if settings.raw_capture != "off":
        try:
            await capture_coordinator.start(
                metadata={
                    "parser_version": "f1-packets-2026",
                    "receive_bind": {
                        "host": listener_config.bind_host,
                        "port": listener_config.port,
                    },
                    "capture_mode": settings.capture_mode,
                }
            )
        except Exception as exc:
            await store.update(last_error=f"Raw capture could not start: {exc}")
    try:
        await network_service.start_listener()
    except ListenerBindError as exc:
        await store.update(
            last_error=f"UDP port {listener_config.port} could not be opened: {exc}"
        )
    watchdog_task = asyncio.create_task(_connection_watchdog())
    event_persistence_task = asyncio.create_task(
        _event_persistence_worker(), name="pitwall-event-persistence"
    )
    yield
    # Stop accepting datagrams first. The parser's connection_lost callback
    # closes its bounded consumer before capture and state are finalized.
    await network_service.stop_listener()
    session_assembler.shutdown()
    await full_field_archive.stop()
    if capture_coordinator.running or capture_service.running:
        try:
            await capture_coordinator.stop()
        except Exception as exc:
            log.warning("Raw capture finalization failed: %s", exc)
    await analysis_jobs.stop()
    if watchdog_task:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
    if event_persistence_task:
        try:
            await asyncio.wait_for(store.event_queue.join(), timeout=5.0)
        except TimeoutError:
            log.warning(
                "Timed out draining %d queued session event(s)",
                store.event_queue.qsize(),
            )
        event_persistence_task.cancel()
        try:
            await event_persistence_task
        except asyncio.CancelledError:
            pass
    if catalog_task:
        catalog_task.cancel()
        try:
            await catalog_task
        except asyncio.CancelledError:
            pass
    if corner_rebuild_task:
        corner_rebuild_task.cancel()
        try:
            await corner_rebuild_task
        except asyncio.CancelledError:
            pass
    if interfaces_task:
        interfaces_task.cancel()
        try:
            await interfaces_task
        except asyncio.CancelledError:
            pass
    if maintenance_task:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass
    if proactive:
        await proactive.stop()
    if voice:
        await voice.shutdown()
    await analysis.stop()
    active_session = session_assembler.session
    if active_session is not None:
        await database.catalog.finalize_session(
            active_session.id, status="incomplete"
        )


app = FastAPI(title="Your Pit Box", version="4.6.2", lifespan=lifespan)
app.add_middleware(
    LanAccessMiddleware,
    enabled=settings.web_lan_access,
    token=(
        settings.web_access_token.get_secret_value()
        if settings.web_access_token is not None
        else None
    ),
)
def _rebind_openai_clients() -> None:
    """Re-read the API key into every client that cached it.

    The audio service and the engineer's provider router both build an
    OpenAI client once at construction, so a key saved from the Connection
    Center would otherwise not apply until the next launch.
    """
    audio.rebind_client()
    router = getattr(brain, "router", None)
    rebind = getattr(router, "rebind_clients", None)
    if callable(rebind):
        rebind()


app.include_router(create_credentials_router(on_change=_rebind_openai_clients))
app.include_router(create_network_router(network_service))
app.include_router(
    create_sessions_router(
        database.catalog,
        trace_root=settings.trace_dir,
        capture_root=settings.capture_dir,
        enqueue_reprocess=analysis_jobs.submit,
    )
)
app.include_router(create_analysis_router(comparison_service))
app.include_router(create_field_router(field_service))
app.include_router(create_storage_router(storage_service))
app.include_router(create_track_models_router(track_model_service))
app.include_router(
    create_live_router(
        store,
        network_service=network_service,
        configured_max_hz=settings.live_ws_max_hz,
    )
)
def _static_root_path() -> Path:
    """Locate the dashboard's files in both a source tree and a frozen build.

    Walking up from ``__file__`` is correct for ``src/pitwall/app.py``, but a
    packaged build collects the modules at a different depth, so the same
    expression resolves to a directory outside the bundle and StaticFiles
    raises at import time — before the server ever starts.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "static"
    return Path(__file__).resolve().parents[2] / "static"


_static_root = _static_root_path()
app.mount("/static", StaticFiles(directory=_static_root), name="static")


@app.middleware("http")
async def _no_stale_frontend(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Force revalidation of the dashboard and its assets.

    After the 4.6.0 upgrade a browser kept heuristically-cached JS from the
    previous install running against the new HTML, which blanked entire
    pages. ETags are already served, so no-cache costs one localhost 304
    per file per load and guarantees the frontend is never a version mix.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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


class StrategyOverrideRequest(BaseModel):
    enabled: bool = True
    locked: bool = True
    start_compound: str | None = None
    next_box_lap: int | None = None
    next_compound: str | None = None
    preferred_stops: int | None = None
    priority: str = "balanced"
    note: str = "dashboard override"


class RacePlannerRequest(BaseModel):
    """Plan a race distance before the lights, from stored tyre evidence."""

    track_id: int | None = None
    total_laps: int | None = None
    start_compound: str | None = None


class WhatIfRequest(BaseModel):
    """A hypothetical stop for the Strategy workspace, e.g. 'box lap 18 for hards'."""

    scenario: str


class RacePlanRequest(BaseModel):
    """A whole-race plan: one compound per stint, one lap per stop.

    ``compounds`` is one longer than ``box_laps`` - the first entry is the tyre
    the stint starts on, and every entry after it is what a stop fits.
    """

    compounds: list[str]
    box_laps: list[int]
    lap_tolerance: int = 2
    wet_race: bool = False
    note: str = "driver plan"
    source: str = "dashboard"


class PreRaceReplyRequest(BaseModel):
    """One driver turn in the pre-race strategy discussion."""

    text: str


class DriverPreferencesRequest(BaseModel):
    strategy_priority: str | None = None
    setup_bias: str | None = None
    rear_stability: int | None = None
    rotation: int | None = None
    traction: int | None = None
    tyre_life: int | None = None
    straight_line: int | None = None
    strategy_risk_appetite: str | None = None


class WakeRequest(BaseModel):
    enabled: bool = True


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((_static_root / "index.html").read_text(encoding="utf-8"))


@app.get("/overlay", response_class=HTMLResponse)
async def overlay() -> HTMLResponse:
    """Transparent OBS/second-screen overlay. Append ?bg=1 for an opaque panel."""
    return HTMLResponse((_static_root / "overlay.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, object]:
    snapshot = await store.snapshot_live()
    listener = network_service.listener_snapshot()
    return {
        "ok": True,
        "version": app.version,
        "udp_listener": listener.state.value not in {"off", "error"},
        "udp_listener_state": listener.state.value,
        "capture": capture_service.snapshot().to_dict(),
        "full_field_archive": asdict(full_field_archive.snapshot()),
        "session_assembler": asdict(session_assembler.quality_report()),
        "analysis_jobs": analysis_jobs.snapshot_dict(),
        "trace_store": trace_store.cache_info(),
        "schema_version": database.schema_version,
        "telemetry_connected": snapshot["connected"],
        "telemetry_stale": snapshot["telemetry_stale"],
        "openai_key_configured": bool(settings.api_key),
        "engineer_runtime": "openai-only",
        "model": settings.model,
        "llm": brain.router.status(),
        "stt_model": settings.stt_model,
        "tts_model": settings.tts_model,
        "voice_pipeline": (
            "realtime" if settings.voice_realtime_enabled else "transcribe-reason-speak"
        ),
        "realtime_model": (
            settings.realtime_model if settings.voice_realtime_enabled else None
        ),
        "realtime_session_open": bool(voice is not None and voice.realtime_active),
        "ptt_status": snapshot["ptt_status"],
        "wake_enabled": snapshot["wake_enabled"],
        "wake_status": snapshot["wake_status"],
        "wake_phrase": snapshot["wake_phrase"],
        "wake_config_source": snapshot["wake_config_source"],
        "wake_input_rms": snapshot["wake_input_rms"],
        "wake_noise_rms": snapshot["wake_noise_rms"],
        "wake_threshold_rms": snapshot["wake_threshold_rms"],
        "wake_last_transcript": snapshot["wake_last_transcript"],
        "wake_last_reason": snapshot["wake_last_reason"],
        "radio_indicator": snapshot["radio_indicator"],
        "radio_latency": snapshot["radio_latency"],
        "proactive": snapshot["proactive"],
        "database": str(database.path),
        "last_error": snapshot["last_error"],
    }


@app.get("/api/tracks")
async def tracks() -> dict[str, object]:
    return {
        "tracks": [
            {"id": int(track_id), "name": str(name)}
            for track_id, name in sorted(TRACKS.items())
        ]
    }


@app.get("/api/history")
async def history(
    scope: str = Query(default="current_session"),
    limit: int = Query(default=50, ge=1, le=250),
) -> dict[str, object]:
    snapshot = await store.snapshot_live()
    session_uid = (
        int(snapshot.get("session_uid", 0)) if scope == "current_session" else None
    )
    track_id = (
        int(snapshot.get("track_id", -1))
        if scope in {"current_session", "current_track"}
        else None
    )
    return await database.history_query(
        track_id=track_id, session_uid=session_uid, limit=limit
    )


@app.post("/api/maintenance")
async def run_maintenance(
    keep_trace_sessions: int = Query(default=-1, ge=-1, le=200),
    vacuum: bool = Query(default=True),
) -> dict[str, object]:
    """Reclaim database space on demand. Safe to run between sessions."""
    keep = (
        settings.db_keep_trace_sessions
        if keep_trace_sessions < 0
        else keep_trace_sessions
    )
    return await database.maintain(keep_trace_sessions=keep, vacuum=vacuum)


@app.get("/api/racing-line")
async def racing_line() -> dict[str, object]:
    return await analysis.get_racing_line_analysis("last")


@app.get("/api/state")
async def get_state() -> dict[str, object]:
    return await store.snapshot_live()


@app.get("/api/compare")
async def rival_compare(driver: str = Query(default="ahead")) -> dict[str, object]:
    return await tools.get_pace_verdict(driver)


@app.get("/api/objective")
async def position_objective(
    target: int = Query(default=10, ge=1, le=24),
) -> dict[str, object]:
    return await tools.get_position_target(target)


@app.get("/api/race-flow")
async def race_flow() -> dict[str, object]:
    return await tools.get_race_flow()


@app.post("/api/briefing/pre")
async def pre_session_briefing(
    mode: str = Query(default="race"),
    track_id: int | None = Query(default=None),
) -> dict[str, object]:
    try:
        payload = await briefing.pre_session(mode, track_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await _persist_briefing("pre_session", payload)


@app.get("/api/review")
async def review(track_id: int | None = Query(default=None)) -> dict[str, object]:
    snapshot = await store.snapshot_live()
    selected = int(track_id if track_id is not None else snapshot.get("track_id", -1))
    return await database.track_review(selected, 50)


@app.get("/api/analysis/line")
async def line_analysis(track_id: int | None = Query(default=None)) -> dict[str, object]:
    """Per-turn racing-line findings from the driver's own recorded laps.

    On-demand: it re-reads every stored trace at the circuit (megabytes of
    JSON), which is fine for a page load and wrong for a per-lap hot path.
    """
    snapshot = await store.snapshot_live()
    selected = int(track_id if track_id is not None else snapshot.get("track_id", -1))
    if selected < 0:
        # No live session: review the circuit most recently driven, which is
        # what "how was my line" means the morning after.
        recent = await database.recent_laps(None, 1)
        selected = int(recent[0]["track_id"]) if recent else -1
    if selected < 0:
        return {"available": False, "reason": "Drive a session first — there is nothing recorded yet."}
    model = await database.load_preference(f"turn_model:{selected}", None)
    if not model:
        return {
            "available": False,
            "track_id": selected,
            "reason": (
                "No canonical turn model for this circuit yet — it is built "
                "automatically once enough traced laps are recorded here."
            ),
        }
    laps = await database.recent_lap_traces(selected, 100)
    findings = await asyncio.to_thread(line_findings, model, laps)
    return {
        "available": True,
        "track_id": selected,
        "track": TRACKS.get(selected, "—"),
        "laps_analyzed": len(laps),
        "turns_modelled": len(model),
        "findings": findings,
    }


@app.get("/api/debrief")
async def debrief(session_uid: int | None = Query(default=None)) -> dict[str, object]:
    """Deterministic end-of-session debrief for the current or a named session."""
    snapshot = await store.snapshot_live()
    selected = int(
        session_uid if session_uid is not None else snapshot.get("session_uid", 0)
    )
    if not selected:
        raise HTTPException(404, "No session is active or specified.")
    return await database.session_debrief(selected)


@app.get("/api/export/laps.csv")
async def export_laps_csv(
    track_id: int | None = Query(default=None),
) -> PlainTextResponse:
    """Export stored laps for a track (or the current track) as CSV."""
    snapshot = await store.snapshot_live()
    selected = int(track_id if track_id is not None else snapshot.get("track_id", -1))
    laps = await database.recent_laps(selected, 500)
    columns = [
        "session_uid",
        "lap_num",
        "lap_time_ms",
        "valid",
        "compound",
        "tyre_age_start",
        "tyre_age_end",
        "s1_ms",
        "s2_ms",
        "s3_ms",
        "fuel_start_kg",
        "fuel_end_kg",
        "position",
        "created_at",
    ]
    lines = [",".join(columns)]
    for lap in laps:
        lines.append(",".join(str(lap.get(column, "")) for column in columns))
    body = "\n".join(lines) + "\n"
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="pitwall_laps_{selected}.csv"'
        },
    )


@app.get("/api/export/session.json")
async def export_session_json(
    scope: str = Query(default="current_session"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> JSONResponse:
    """Export the full stored history bundle as a downloadable JSON file."""
    snapshot = await store.snapshot_live()
    session_uid = (
        int(snapshot.get("session_uid", 0)) if scope == "current_session" else None
    )
    track_id = (
        int(snapshot.get("track_id", -1))
        if scope in {"current_session", "current_track"}
        else None
    )
    data = await database.history_query(
        track_id=track_id, session_uid=session_uid, limit=limit
    )
    return JSONResponse(
        data,
        headers={"Content-Disposition": 'attachment; filename="pitwall_session.json"'},
    )


# How often live state is offered to the dashboard, and how long a dashboard
# may go without acknowledging before it is dropped. The timeout is generous:
# a browser busy rendering a 24-car timing tower on a loaded machine can be
# slow without being gone, and dropping a working dashboard mid-race would be
# its own defect.
WEBSOCKET_PUSH_INTERVAL_S = 0.25
WEBSOCKET_ACK_TIMEOUT_S = 15.0


@app.websocket("/ws")
async def websocket_state(websocket: WebSocket) -> None:
    """Push live state, but never faster than the dashboard can take it.

    This used to send a full snapshot every 250 ms unconditionally. send_json
    returns once the frame is handed to the transport, not once the browser has
    it, so a client that renders slower than 4 Hz never applied backpressure:
    frames queued in the transport and the queue grew for as long as the
    session lasted. On an 8 GB machine also running the game, an hour into a
    race that ended in ten MemoryErrors in 83 seconds, each one killing the
    dashboard while the engine behind it carried on fine.

    The client now acknowledges each frame, so at most one is ever in flight. A
    dashboard that stops acknowledging is dropped rather than buffered for: it
    reconnects on its own, and a reconnect is far cheaper than exhausting
    memory mid-race.
    """
    await websocket.accept()
    try:
        while True:
            try:
                await websocket.send_json(await store.snapshot_live())
            except MemoryError:
                # Serializing a frame is the largest single allocation this
                # process makes at 4 Hz, so when the machine is out of memory
                # (game + browser + capture on one box) it fails here first.
                # One dropped dashboard connection is recoverable — the client
                # reconnects and bootstraps over HTTP; an unhandled MemoryError
                # tearing through the ASGI stack mid-race is not.
                log.warning(
                    "Not enough memory to serialize a live state frame; "
                    "dropping this dashboard connection so it can reconnect"
                )
                with suppress(Exception):
                    await websocket.close(code=1011)
                return
            except RuntimeError:
                # uvicorn raises RuntimeError("Unexpected ASGI message
                # 'websocket.send', after sending 'websocket.close'") when the
                # client vanished between our last receive and this send. That
                # is a disconnect, not a server fault. Still offer a close so
                # a transport that remained open is released; if the close was
                # what already happened, this raises again and is ignored.
                with suppress(Exception):
                    await websocket.close()
                return
            try:
                await asyncio.wait_for(
                    websocket.receive_text(), timeout=WEBSOCKET_ACK_TIMEOUT_S
                )
            except TimeoutError:
                log.warning(
                    "Dashboard stopped acknowledging live state after %.0fs; "
                    "closing the socket so frames cannot queue without bound",
                    WEBSOCKET_ACK_TIMEOUT_S,
                )
                await websocket.close(code=1013)
                return
            await asyncio.sleep(WEBSOCKET_PUSH_INTERVAL_S)
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


@app.post("/api/realtime/open")
async def realtime_open() -> dict[str, object]:
    """Open a speech-to-speech session without waiting for the wake phrase."""
    if voice is None or voice.realtime is None:
        raise HTTPException(
            409, "Realtime radio is disabled. Set PITWALL_VOICE_REALTIME_ENABLED=true."
        )
    opened = await voice.realtime.open()
    if not opened:
        snapshot = await store.snapshot_live()
        raise HTTPException(
            503, str(snapshot.get("last_error") or "Could not open session")
        )
    return {"open": True, "model": settings.realtime_model}


@app.post("/api/realtime/shakedown")
async def realtime_shakedown() -> dict[str, object]:
    """Check the live Realtime wire contract. Uses a negligible amount of credit."""
    radio = voice.realtime if voice is not None else None
    if radio is None:
        radio = RealtimeRadio(store, tools)
    result = await radio.shakedown()
    if not result.get("ok"):
        raise HTTPException(
            503, str(result.get("reason") or "Realtime shakedown failed")
        )
    return result


@app.post("/api/realtime/close")
async def realtime_close() -> dict[str, object]:
    if voice is None or voice.realtime is None:
        raise HTTPException(409, "Realtime radio is disabled.")
    await voice.realtime.close("closed from dashboard")
    return {"open": False}


@app.post("/api/setup/recommend")
async def setup_recommendation(request: SetupRequest) -> dict[str, object]:
    result = await setup_advisor.generate(request.profile, request.track_id)
    if not result.get("available"):
        raise HTTPException(409, result.get("reason", "Setup unavailable"))
    return result


async def _persist_app_settings(changes: dict[str, object]) -> dict[str, object]:
    saved = await database.load_preference(APP_SETTINGS_KEY, None)
    merged = dict(saved) if isinstance(saved, dict) else {}
    merged.update(changes)
    await database.save_preference(APP_SETTINGS_KEY, merged)
    return merged


@app.get("/api/v1/app-settings")
async def get_app_settings() -> dict[str, object]:
    saved = await database.load_preference(APP_SETTINGS_KEY, None)
    return {
        "settings": settings_view(settings, saved if isinstance(saved, dict) else {})
    }


@app.post("/api/v1/app-settings")
async def save_app_settings(changes: dict[str, object]) -> dict[str, object]:
    """Validate, persist, and hot-apply dashboard settings.

    Restart-flagged fields are persisted but not applied live — their
    consumers constructed from the old value at launch, and pretending
    otherwise would misreport the app's actual behavior.
    """
    if not changes:
        raise HTTPException(400, "No settings provided.")
    coerced: dict[str, object] = {}
    for name, value in changes.items():
        try:
            coerced[name] = coerce(name, value)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    await _persist_app_settings(coerced)
    results: dict[str, object] = {}
    for name, value in coerced.items():
        results[name] = apply_runtime(settings, name, value)
    if proactive is not None and {
        "proactive_enabled", "proactive_cadence_laps"
    } & coerced.keys():
        await proactive.configure(
            settings.proactive_enabled, settings.proactive_cadence_laps
        )
    saved = await database.load_preference(APP_SETTINGS_KEY, None)
    return {
        "results": results,
        "settings": settings_view(settings, saved if isinstance(saved, dict) else {}),
    }


@app.post("/api/proactive/config")
async def proactive_config(request: ProactiveRequest) -> dict[str, object]:
    if proactive is None:
        raise HTTPException(503, "Proactive engineer not started")
    result = await proactive.configure(request.enabled, request.cadence_laps)
    # The DRIVE card used to change this in memory only, silently reverting
    # to .env defaults at the next launch. Persist through the same store
    # the Settings page uses, and mirror onto the live settings object so
    # both surfaces report the same values.
    settings.proactive_enabled = bool(request.enabled)
    settings.proactive_cadence_laps = max(1, min(10, int(request.cadence_laps)))
    await _persist_app_settings(
        {
            "proactive_enabled": coerce("proactive_enabled", request.enabled),
            "proactive_cadence_laps": coerce(
                "proactive_cadence_laps",
                max(1, min(10, int(request.cadence_laps))),
            ),
        }
    )
    return result


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


def _fitted_compound(snapshot: dict[str, object]) -> str:
    tyre = snapshot.get("tyre") or {}
    if not isinstance(tyre, dict):
        return ""
    return str(tyre.get("compound", "") or "").upper()


@app.get("/api/strategy/override")
async def get_strategy_override() -> dict[str, object]:
    return dict((await store.snapshot_analysis()).get("strategy_override", {}))


@app.post("/api/strategy/override")
async def set_strategy_override(request: StrategyOverrideRequest) -> dict[str, object]:
    snapshot = await store.snapshot_analysis()
    override = dict(snapshot.get("strategy_override", {}))
    payload = request.model_dump()
    for compound_key in ("start_compound", "next_compound"):
        value = payload.get(compound_key)
        if value is not None:
            value = str(value).upper()
            if value not in {"SOFT", "MEDIUM", "HARD", "INTER", "WET"}:
                raise HTTPException(400, f"Invalid compound: {value}")
            payload[compound_key] = value
    if payload.get("preferred_stops") not in {None, 0, 1, 2, 3}:
        raise HTTPException(400, "preferred_stops must be 0 to 3")
    if payload.get("next_box_lap") is not None and int(payload["next_box_lap"]) < 1:
        raise HTTPException(400, "next_box_lap must be positive")
    override.update(payload)
    override["source"] = "dashboard"
    override["updated_at"] = _time.time()
    await store.update(strategy_override=override)
    plan = await strategy.recompute()
    return {"override": override, "strategy": plan}


@app.delete("/api/strategy/override")
async def clear_strategy_override() -> dict[str, object]:
    snapshot = await store.snapshot_analysis()
    override = dict(snapshot.get("strategy_override", {}))
    override.update(
        {
            "enabled": False,
            "locked": False,
            "start_compound": None,
            "start_compound_explicit": False,
            "start_compound_seen_fitted": "",
            "next_box_lap": None,
            "next_compound": None,
            "preferred_stops": None,
            "plan": {},
            "plan_agreed": False,
            "note": "cleared from dashboard",
            "source": "dashboard",
        }
    )
    await store.update(strategy_override=override)
    return {"override": override, "strategy": await strategy.recompute()}


@app.post("/api/strategy/plan-race")
async def plan_race(request: RacePlannerRequest) -> dict[str, object]:
    """Rank whole-race plans for a chosen distance, before the race starts."""
    compound = request.start_compound
    if compound is not None:
        compound = str(compound).upper()
        if compound not in {"SOFT", "MEDIUM", "HARD", "INTER", "WET"}:
            raise HTTPException(400, f"Invalid compound: {compound}")
    if request.total_laps is not None and not 2 <= int(request.total_laps) <= 200:
        raise HTTPException(400, "total_laps must be between 2 and 200")
    return await strategy.plan_race(
        track_id=request.track_id,
        total_laps=request.total_laps,
        start_compound=compound,
    )


@app.post("/api/strategy/what-if")
async def strategy_what_if(request: WhatIfRequest) -> dict[str, object]:
    """Evaluate a hypothetical stop against the current recommendation.

    Deterministic: the same stint simulation the ranked plans use, no model
    call. Serves the Strategy workspace's what-if row.
    """
    scenario = request.scenario.strip()
    if not scenario:
        raise HTTPException(400, "Describe the stop, e.g. 'box lap 18 for hards'.")
    return await strategy.what_if(scenario)


@app.get("/api/strategy/rivals")
async def strategy_rival_stops(
    top_n: int = Query(default=8, ge=1, le=12),
) -> dict[str, object]:
    """Deterministic projected next stops for nearby rivals (timeline overlay)."""
    return await strategy.predict_rival_strategy(top_n)


@app.post("/api/strategy/plan")
async def set_race_plan(request: RacePlanRequest) -> dict[str, object]:
    """Commit a whole-race plan: a compound per stint, a lap per stop."""
    snapshot = await store.snapshot_analysis()
    try:
        plan = normalise_plan(
            {
                "compounds": request.compounds,
                "box_laps": request.box_laps,
                "lap_tolerance": request.lap_tolerance,
            },
            total_laps=int(snapshot.get("total_laps", 0) or 0),
        )
    except PlanError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not compound_rule_ok(plan, wet_race=bool(request.wet_race)):
        raise HTTPException(
            400,
            "That plan uses one dry compound all race, which is a "
            "disqualification. Two different dry compounds are required.",
        )

    override = dict(snapshot.get("strategy_override", {}))
    override.update(
        {
            "enabled": True,
            "locked": True,
            "plan": plan,
            "plan_agreed": True,
            # The single-stop fields only ever constrained the next stop. Leaving
            # a stale one set alongside a whole-race plan would have two
            # constraints disagreeing with no rule for which wins.
            "next_box_lap": None,
            "next_compound": None,
            "preferred_stops": plan["stops"],
            "start_compound": plan["compounds"][0],
            # See PreRacePlanner.commit: only a start tyre that differs from the
            # one on the car counts as a choice, so a plan that simply echoed
            # the fitted tyre never stops the engine following a later change.
            "start_compound_explicit": bool(
                _fitted_compound(snapshot)
                and str(plan["compounds"][0]).upper() != _fitted_compound(snapshot)
            ),
            "start_compound_seen_fitted": _fitted_compound(snapshot),
            "note": request.note,
            "source": request.source,
            "updated_at": _time.time(),
        }
    )
    await store.update(strategy_override=override)
    return {
        "override": override,
        "spoken": describe_plan(plan),
        "strategy": await strategy.recompute(),
    }


# --- Pre-race planning ------------------------------------------------------
# The engineer proposes a race before the lights, the driver pushes back, and
# only agreement commits it. Nothing here constrains the race until /commit.


@app.get("/api/prerace")
async def get_prerace_briefing() -> dict[str, object]:
    state = await store.snapshot_analysis()
    return {
        "briefing": await prerace.snapshot(),
        "available": PreRacePlanner.is_prerace(state),
        "total_laps": int(state.get("total_laps", 0) or 0),
        "current_lap": int(state.get("current_lap", 0) or 0),
    }


@app.post("/api/prerace/propose")
async def propose_prerace_plan() -> dict[str, object]:
    state = await store.snapshot_analysis()
    if not PreRacePlanner.is_prerace(state):
        raise HTTPException(
            409,
            "Pre-race planning needs a race session that has not started yet.",
        )
    return {"briefing": await prerace.propose()}


@app.post("/api/prerace/respond")
async def respond_to_prerace_plan(request: PreRaceReplyRequest) -> dict[str, object]:
    text = request.text.strip()
    if not text:
        raise HTTPException(400, "Say something to respond to.")
    return {"briefing": await prerace.respond(text)}


@app.post("/api/prerace/commit")
async def commit_prerace_plan(request: RacePlanRequest | None = None) -> dict[str, object]:
    """Agree the plan. With no body, commits whatever is on the table."""
    plan = None
    if request is not None and request.compounds:
        plan = {
            "compounds": request.compounds,
            "box_laps": request.box_laps,
            "lap_tolerance": request.lap_tolerance,
        }
    briefing = await prerace.commit(plan)
    if not briefing.get("committed"):
        raise HTTPException(400, briefing.get("spoken", "That plan cannot be raced."))
    return {"briefing": briefing, "strategy": await strategy.get_plan()}


@app.delete("/api/prerace")
async def discard_prerace_plan() -> dict[str, object]:
    return {"briefing": await prerace.discard()}


@app.post("/api/shutdown")
async def shutdown_pitwall(request: Request) -> dict[str, object]:
    """Stop Your Pit Box from the dashboard.

    Restricted to the machine running Your Pit Box, matching the API-key endpoints:
    with LAN access enabled the dashboard is reachable from the console and any
    other device on the network, and none of them should be able to end a
    session that is being recorded.

    Setting uvicorn's own exit flag rather than killing the process means the
    lifespan teardown still runs, so the UDP listener closes before the parser,
    the capture file is finalized and queued session events are drained. A hard
    exit would corrupt the recording of whatever session was in progress, which
    is the one thing a driver cannot get back.
    """
    client = request.client
    host = client.host if client else ""
    if not is_loopback_host(host):
        raise HTTPException(
            403, "Your Pit Box can only be shut down from the computer running it."
        )
    server = getattr(request.app.state, "server", None)
    if server is None:
        raise HTTPException(
            503, "Your Pit Box is not running under its own server and cannot stop itself."
        )
    log.info("Shutdown requested from the dashboard")
    server.should_exit = True
    return {"stopping": True}


@app.get("/api/preferences")
async def get_driver_preferences() -> dict[str, object]:
    return dict((await store.snapshot_analysis()).get("driver_preferences", {}))


@app.post("/api/preferences")
async def set_driver_preferences(
    request: DriverPreferencesRequest,
) -> dict[str, object]:
    snapshot = await store.snapshot_analysis()
    preferences = dict(snapshot.get("driver_preferences", {}))
    payload = request.model_dump(exclude_none=True)
    for key in ("rear_stability", "rotation", "traction", "tyre_life", "straight_line"):
        if key in payload:
            payload[key] = max(0, min(3, int(payload[key])))
    if "strategy_risk_appetite" in payload:
        appetite = str(payload["strategy_risk_appetite"]).lower()
        if appetite not in {"conservative", "balanced", "aggressive"}:
            raise HTTPException(
                400,
                "strategy_risk_appetite must be conservative, balanced, or aggressive",
            )
        payload["strategy_risk_appetite"] = appetite
    preferences.update(payload)
    update: dict[str, object] = {"driver_preferences": preferences}
    if "strategy_risk_appetite" in payload:
        update["strategy_risk_appetite"] = payload["strategy_risk_appetite"]
    await store.update(**update)
    await database.save_preference("driver_preferences", preferences)
    if "strategy_risk_appetite" in payload:
        await strategy.recompute()
    return preferences


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
