"""A stranded browser transport must not prevent recorded-data finalization."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from uvicorn.lifespan.on import LifespanOn

import pitwall.app as app_module
import pitwall.main as main_module
from pitwall.capture import CaptureReader
from pitwall.capture_service import CaptureService


@pytest.mark.asyncio
async def test_stalled_network_close_still_finalizes_capture(monkeypatch, tmp_path):
    capture = CaptureService(tmp_path / "captures")
    packets = [b"recorded-before-shutdown", b"queued-before-shutdown"]
    finalized = []

    @asynccontextmanager
    async def lifespan(_app):
        await capture.start(relative_path="session.pwcap")
        for packet in packets:
            assert capture.submit(packet, ("127.0.0.1", 20777))
        try:
            yield
        finally:
            # Cleanup may legitimately take longer than the network deadline.
            await asyncio.sleep(0.06)
            finalized.append(await capture.stop())

    application = FastAPI(lifespan=lifespan)
    servers = []
    real_server = uvicorn.Server

    def prepare_server(config):
        server = real_server(config)
        server.run = lambda: None
        servers.append(server)
        return server

    monkeypatch.setattr(app_module, "app", application)
    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module, "another_instance_is_serving", lambda *_: False)
    monkeypatch.setattr(main_module, "close_splash_when_ready", lambda *_: None)
    monkeypatch.setattr(main_module, "NETWORK_DRAIN_TIMEOUT_S", 0.02)
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(
        web_host="127.0.0.1", web_port=0, log_level="info", open_browser=False,
    ))
    monkeypatch.setattr(main_module.uvicorn, "Server", prepare_server)
    main_module.run()
    server = servers[0]

    class StalledListener:
        closed = False
        close_cancelled = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            try:
                await asyncio.Future()
            finally:
                self.close_cancelled = True

    listener = StalledListener()
    server.servers = [listener]
    server.lifespan = LifespanOn(server.config)
    await server.lifespan.startup()
    try:
        await asyncio.wait_for(server.shutdown(), timeout=2)
        assert listener.closed and listener.close_cancelled
        assert not server.lifespan.shutdown_failed
        assert finalized == [tmp_path / "captures" / "session.pwcap"]
        assert [frame.data for frame in CaptureReader(finalized[0])] == packets
        assert capture.snapshot().state == "off"
        assert not list((tmp_path / "captures").rglob("*.tmp"))
    finally:
        if not server.lifespan.shutdown_event.is_set():
            await server.lifespan.shutdown()
        await capture.stop()
