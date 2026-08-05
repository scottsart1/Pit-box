from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.web_security import LanAccessMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def index() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/change")
    async def change() -> dict[str, bool]:
        return {"changed": True}

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
