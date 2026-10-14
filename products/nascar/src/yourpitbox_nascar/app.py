from __future__ import annotations

import asyncio
import csv
import io
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from . import __version__
from .catalog import TRACKS
from .demo import demo_tick, start_demo
from .engine import RaceEngine
from .engineer import Engineer
from .models import ChatRequest, Handling, Lap, Model, Observation, OCRConfig, PitService, RaceConfig
from .observer import Observer, capture, image_bytes, windows
from .secrets import Credentials
from .store import Store

STATIC = Path(__file__).parent / "static"


class SettingsInput(Model):
    api_key: str | None = Field(default=None, max_length=500)
    model: str = Field(default="gpt-6-luna", pattern=r"^[a-zA-Z0-9._-]{1,80}$")


class CSVImport(Model):
    config: RaceConfig
    csv: str = Field(max_length=500_000)


class ScenarioInput(Model):
    yellow_laps: float = Field(ge=0, le=2000)
    reserve_laps: float = Field(ge=0, le=30)
    saving_percent: float = Field(ge=0, le=30)


def parse_csv(text: str) -> list[Lap]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or not {"number", "time_s"}.issubset(reader.fieldnames):
        raise ValueError("CSV requires number and time_s columns; see the downloadable template")
    laps = []
    seen = set()
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise ValueError("The CSV could not be read; check its columns and quoting") from exc
    for line, row in enumerate(rows, 2):
        if len(laps) >= 2200:
            raise ValueError("A session can contain at most 2200 laps")
        try:
            clean = (row.get("clean") or "true").strip().lower()
            if clean not in ("true", "false", "1", "0"):
                raise ValueError("clean must be true or false")
            lap = Lap(number=int(row["number"]), time_s=float(row["time_s"]) if row["time_s"] else None,
                      fuel_used_pct=float(row["fuel_used_pct"]) if row.get("fuel_used_pct") else None,
                      flag=row.get("flag", "unknown") or "unknown", clean=clean in ("true", "1"),
                      stint=int(row.get("stint") or 1), source="csv")
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Invalid lap on CSV line {line}: {exc}") from exc
        if lap.number in seen:
            raise ValueError(f"Duplicate lap {lap.number} on line {line}")
        seen.add(lap.number)
        laps.append(lap)
    if not laps:
        raise ValueError("The CSV contains no laps")
    return sorted(laps, key=lambda lap: lap.number)


def create_app(root: Path, token: str | None = None) -> FastAPI:
    store = Store(root)
    engine = RaceEngine(store)
    key = token or store.setting("access_token") or secrets.token_urlsafe(32)
    store.save_setting("access_token", key)
    credentials = Credentials(root)
    engineer = Engineer(engine, credentials)
    observer = Observer(engine)
    demo_task = None
    active = store.setting("active_session")
    if active and store.read(active):
        engine.resume(active)

    async def tick_loop():
        while True:
            await asyncio.to_thread(engine.proactive)
            await asyncio.sleep(.5)

    async def stop_demo():
        nonlocal demo_task
        if demo_task:
            demo_task.cancel()
            try:
                await demo_task
            except asyncio.CancelledError:
                pass
            demo_task = None

    async def replay(sid):
        for tick in range(1, 265):
            await asyncio.sleep(1)
            if engine.sid != sid:
                break
            await asyncio.to_thread(demo_tick, engine, tick)

    @asynccontextmanager
    async def lifespan(app):
        ticker = asyncio.create_task(tick_loop())
        try:
            yield
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass
            await stop_demo()
            await observer.stop()
            await engineer.close()
            engine.flush()
            store.close()

    app = FastAPI(title="YourPitBox for NASCAR", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine, app.state.store, app.state.token = engine, store, key
    app.state.observer, app.state.engineer = observer, engineer
    app.state.lan_info = {}
    app.state.shutdown = asyncio.Event()

    def is_local(request):
        return request.client and request.client.host in ("127.0.0.1", "::1", "testclient")

    @app.middleware("http")
    async def access(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin access is disabled"}, status_code=403)
        hostname = request.url.hostname
        allowed_hosts = {"127.0.0.1", "localhost", "::1", "testserver", *app.state.lan_info.get("addresses", [])}
        if hostname not in allowed_hosts:
            return JSONResponse({"detail": "Unknown host"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-pitbox-key", "")
            if not secrets.compare_digest(supplied, key):
                return JSONResponse({"detail": "Pair this device using the link shown on the desktop"}, status_code=401)
            if request.url.path in ("/api/settings", "/api/pairing", "/api/quit") and not is_local(request):
                return JSONResponse({"detail": "Open this setting on the desktop"}, status_code=403)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 1_000_000):
            return JSONResponse({"detail": "Request is too large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)[:500]}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        text = (STATIC / "index.html").read_text(encoding="utf-8")
        return text.replace("__LOCAL_TOKEN__", key if is_local(request) else "")

    @app.get("/health")
    async def health():
        return {"product": "yourpitbox-nascar", "version": __version__, "ok": True}

    @app.get("/api/state")
    async def state():
        snap = await asyncio.to_thread(engine.snapshot)
        snap["laps"] = snap["laps"][-240:]
        snap["observer"] = dict(observer.status)
        snap["engineer_configured"] = bool(credentials.read())
        return snap

    @app.get("/api/catalog")
    async def catalog():
        return {"tracks": TRACKS, "version": __version__}

    @app.post("/api/sessions")
    async def session(config: RaceConfig):
        if config.mode != "driver":
            raise ValueError("Use the sample replay button for simulated data")
        await stop_demo()
        await observer.stop()
        return {"id": await asyncio.to_thread(engine.start, config)}

    @app.get("/api/sessions")
    async def sessions():
        return await asyncio.to_thread(store.sessions)

    @app.get("/api/sessions/{sid}")
    async def session_detail(sid: str):
        data = await asyncio.to_thread(store.read, sid)
        if not data:
            raise HTTPException(404, "Session not found")
        return data

    @app.post("/api/sessions/{sid}/resume")
    async def resume(sid: str):
        await stop_demo()
        await observer.stop()
        await asyncio.to_thread(engine.resume, sid)
        return {"id": sid}

    @app.put("/api/config")
    async def config(value: RaceConfig):
        with engine.lock:
            if not engine.sid or engine.config.mode == "demo" or value.mode != "driver":
                raise ValueError("Select a driver weekend before editing its rules")
            with store.lock, store.db:
                store.db.execute("UPDATE sessions SET config=? WHERE id=?", (value.model_dump_json(), engine.sid))
            engine.config = value
        return {"ok": True}

    @app.post("/api/observations")
    async def observations(value: Observation):
        return await asyncio.to_thread(engine.ingest, value)

    @app.post("/api/laps")
    async def lap(value: Lap):
        if engine.config.mode == "demo":
            raise ValueError("Start a driver weekend to log laps")
        await asyncio.to_thread(engine.add_laps, [value])
        return {"ok": True}

    @app.post("/api/pits")
    async def pit(value: PitService):
        await asyncio.to_thread(engine.pit, value)
        return {"ok": True}

    @app.post("/api/handling")
    async def handling(value: Handling):
        return await asyncio.to_thread(engine.handling, value)

    @app.post("/api/scenario")
    async def scenario(value: ScenarioInput):
        return engineer.execute("fuel_scenario", value.model_dump(), engine.snapshot())

    @app.get("/api/brief/{topic}")
    async def brief(topic: str):
        if topic not in ("fuel", "field", "pits", "pace"):
            raise ValueError("Choose fuel, field, pits or pace")
        return {"text": engineer.brief(topic), "mode": "local"}

    @app.post("/api/chat")
    async def chat(value: ChatRequest):
        return await engineer.ask(value.message)

    @app.get("/api/settings")
    async def settings():
        return {"configured": bool(credentials.read()), "model": store.setting("engineer_model", "gpt-6-luna")}

    @app.put("/api/settings")
    async def settings_put(value: SettingsInput):
        if value.api_key is not None:
            credentials.save(value.api_key)
        store.save_setting("engineer_model", value.model)
        return {"configured": bool(credentials.read())}

    @app.post("/api/demo")
    async def demo():
        nonlocal demo_task
        await stop_demo()
        await observer.stop()
        sid = await asyncio.to_thread(start_demo, engine)
        demo_task = asyncio.create_task(replay(sid))
        return {"id": sid}

    @app.post("/api/demo/stop")
    async def demo_stop():
        await stop_demo()
        return {"ok": True}

    @app.post("/api/import")
    async def csv_import(value: CSVImport):
        # Validate completely before creating or switching any session.
        laps = parse_csv(value.csv)
        if value.config.mode != "driver":
            raise ValueError("CSV imports are driver sessions")
        await stop_demo()
        await observer.stop()
        sid = await asyncio.to_thread(engine.import_laps, value.config, laps)
        return {"id": sid, "laps": len(laps)}

    @app.get("/api/template.csv")
    async def csv_template():
        return Response("number,time_s,fuel_used_pct,flag,clean,stint\n1,31.250,2.1,green,true,1\n2,31.310,2.2,green,true,1\n", media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="nascar-laps-template.csv"'})

    @app.get("/api/windows")
    async def window_list():
        return await asyncio.to_thread(windows)

    @app.get("/api/windows/{window_id}/frame")
    async def frame(window_id: int):
        frame = await asyncio.to_thread(capture, window_id)
        frame.thumbnail((1920, 1080))
        return Response(await asyncio.to_thread(image_bytes, frame), media_type="image/png")

    @app.post("/api/observer/preview")
    async def preview(value: OCRConfig):
        frame = await asyncio.to_thread(capture, value.window_id)
        return await observer.read(frame, value)

    @app.post("/api/observer/start")
    async def observer_start(value: OCRConfig):
        await observer.start(value)
        store.save_setting("ocr_regions", [x.model_dump() for x in value.regions])
        return {"ok": True}

    @app.get("/api/observer/config")
    async def observer_config():
        return {"regions": store.setting("ocr_regions", [])}

    @app.post("/api/observer/stop")
    async def observer_stop():
        await observer.stop()
        return {"ok": True}

    @app.get("/api/pairing")
    async def pairing():
        return app.state.lan_info

    @app.post("/api/quit")
    async def quit_app():
        engine.flush()
        app.state.shutdown.set()
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
