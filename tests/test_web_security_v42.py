from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pitwall.web_security import LanAccessMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def index() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/change")
    async def change() -> dict[str, bool]:
        return {"changed": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    app.add_middleware(
        LanAccessMiddleware,
        enabled=True,
        token="correct-horse-battery-staple",
    )
    return app


def test_lan_gate_accepts_header_and_rejects_missing_or_cross_origin() -> None:
    client = TestClient(_app(), client=("192.168.1.50", 50_000))

    assert client.get("/").status_code == 401
    accepted = client.get(
        "/",
        headers={"X-Pitwall-Token": "correct-horse-battery-staple"},
    )
    assert accepted.status_code == 200
    rejected = client.post(
        "/change",
        headers={
            "X-Pitwall-Token": "correct-horse-battery-staple",
            "Origin": "https://attacker.example",
        },
    )
    assert rejected.status_code == 403


def test_pairing_query_sets_http_only_cookie_for_followup_requests() -> None:
    client = TestClient(_app(), client=("192.168.1.50", 50_000))
    paired = client.get("/?access_token=correct-horse-battery-staple")
    assert paired.status_code == 200
    assert "HttpOnly" in paired.headers["set-cookie"]
    assert client.get("/").status_code == 200
    assert client.post("/change").status_code == 403
    assert (
        client.post("/change", headers={"Origin": "http://testserver"}).status_code
        == 200
    )


def test_loopback_clients_remain_frictionless() -> None:
    client = TestClient(_app(), client=("127.0.0.1", 50_000))
    assert client.get("/").status_code == 200


def test_loopback_rejects_cross_origin_http_and_websocket_requests() -> None:
    client = TestClient(_app(), client=("127.0.0.1", 50_000))

    response = client.post(
        "/change",
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403

    with pytest.raises(WebSocketDisconnect) as rejected, client.websocket_connect(
        "/ws", headers={"Origin": "https://attacker.example"}
    ):
        pass
    assert rejected.value.code == 4401


def test_same_hostname_with_wrong_port_is_not_same_origin() -> None:
    client = TestClient(_app(), client=("192.168.1.50", 50_000))
    response = client.post(
        "/change",
        headers={
            "Host": "pitwall.local:8000",
            "Origin": "http://pitwall.local:8001",
            "X-Pitwall-Token": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 403


def test_sec_fetch_site_cross_site_is_rejected_even_from_loopback() -> None:
    client = TestClient(_app(), client=("127.0.0.1", 50_000))
    response = client.post(
        "/change",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_legitimate_origin_matches_scheme_host_and_effective_port() -> None:
    client = TestClient(_app(), client=("192.168.1.50", 50_000))

    explicit_port = client.post(
        "/change",
        headers={
            "Host": "pitwall.local:8000",
            "Origin": "http://pitwall.local:8000",
            "X-Pitwall-Token": "correct-horse-battery-staple",
        },
    )
    default_port = client.post(
        "/change",
        headers={
            "Host": "pitwall.local",
            "Origin": "http://pitwall.local:80",
            "X-Pitwall-Token": "correct-horse-battery-staple",
        },
    )

    assert explicit_port.status_code == 200
    assert default_port.status_code == 200
