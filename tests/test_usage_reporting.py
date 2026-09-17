"""All reporting network calls are intercepted; these never contact production."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.api.usage import create_usage_router
from pitwall.state import StateStore
from pitwall.usage_reporting import EVENTS, UsageReporting, utc_day


@pytest.fixture
def usage(tmp_path):
    return UsageReporting(tmp_path / "usage-reporting.json", platform="windows")


@pytest.mark.asyncio
async def test_default_off_no_identifier_no_file_no_network(usage):
    await usage.start()
    for event in EVENTS:
        usage.record(event)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("No opt-in: no request allowed"))) as client:
        assert await usage.flush(client)
    await usage.stop()
    assert usage.status() == {"enabled": False, "decided": False, "pending_days": 0, "last_sent_at": None, "consent_version": 1}
    assert usage.installation_id is None
    assert not usage.path.exists()


@pytest.mark.asyncio
async def test_explicit_consent_persists_and_decline_clears_queue_and_resets_id(usage):
    await usage.set_enabled(True)
    old_id = usage.installation_id
    assert uuid.UUID(old_id).version == 4
    usage.record("engineer")
    await usage.stop()
    restarted = UsageReporting(usage.path, platform="windows")
    restarted._load()
    assert restarted.enabled and restarted.pending == usage.pending
    assert restarted.installation_id == old_id
    await restarted.set_enabled(False)
    assert not restarted.pending and not restarted.seen and restarted.installation_id is None
    assert old_id not in restarted.path.read_text()
    declined = UsageReporting(usage.path, platform="windows")
    declined._load()
    assert declined.decided and not declined.enabled
    await declined.set_enabled(True)
    assert declined.installation_id != old_id


@pytest.mark.asyncio
async def test_consent_disk_failure_never_enables(usage, monkeypatch):
    def fail(_):
        raise OSError("disk full")
    monkeypatch.setattr(usage, "_write", fail)
    with pytest.raises(OSError):
        await usage.set_enabled(True)
    assert not usage.enabled and usage.installation_id is None


@pytest.mark.parametrize("data", ["broken", "[]", '{"enabled":true}', '{"enabled":"true","consent_version":1}', json.dumps({"enabled": True, "consent_version": 1, "installation_id": str(uuid.uuid1())}), "x" * 32769], ids=["invalid-json", "array", "no-consent", "wrong-type", "non-random-id", "oversize"])
def test_corruption_fails_closed(usage, data):
    usage.path.write_text(data)
    usage._load()
    assert not usage.enabled and not usage.decided


@pytest.mark.asyncio
async def test_daily_dedup_allowlist_payload_and_retry(usage):
    await usage.set_enabled(True)
    for _ in range(100):
        usage.record("engineer")
    usage.record("private radio text / API_KEY=secret")
    calls = []
    def receive(request):
        calls.append(json.loads(request.content))
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        assert not await usage.flush(client)
        assert len(usage.pending) == 2
        assert await usage.flush(client)
        assert not usage.pending
        usage.record("engineer")
        assert not usage.pending
    assert calls[0] == calls[1]
    assert set(calls[0]) == {"consent_version", "installation_id", "platform", "version", "events"}
    assert {row["event"] for row in calls[0]["events"]} == {"app_started", "engineer"}
    assert all(set(row) == {"day", "event"} for row in calls[0]["events"])
    assert "secret" not in json.dumps(calls)


@pytest.mark.asyncio
async def test_invalid_ack_keeps_outbox(usage):
    await usage.set_enabled(True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="maintenance"))) as client:
        assert not await usage.flush(client)
    assert usage.pending


@pytest.mark.asyncio
async def test_offline_queue_is_bounded_and_expires(usage):
    await usage.set_enabled(True)
    today = datetime.now(timezone.utc).date()
    usage.pending = {((today - timedelta(days=day)).isoformat(), event) for day in range(7) for event in EVENTS}
    await usage._save_pending()
    assert usage.path.stat().st_size < 32768
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: (calls.append(json.loads(request.content)) or httpx.Response(200, json={"ok": True})))) as client:
        await usage.flush(client)
        await usage.flush(client)
    assert [len(call["events"]) for call in calls] == [28, 21]
    usage.pending = {((today - timedelta(days=7)).isoformat(), "racing")}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Expired report must not be sent"))) as client:
        await usage.flush(client)
    assert not usage.pending


def driving_snapshot(now, **changes):
    return {"session_uid": 999, "restart_epoch": 0, "connected": True, "game_paused": False,
            "speed_kph": 140, "session_time_s": now, "packet_group_freshness": {"6": now}, **changes}


@pytest.mark.asyncio
async def test_only_real_advancing_driving_activates(usage):
    await usage.set_enabled(True)
    for now in range(100, 160):
        usage.observe_racing(driving_snapshot(now), now=now)
    assert (utc_day(), "racing") not in usage.pending
    usage.observe_racing(driving_snapshot(160), now=160)
    assert (utc_day(), "racing") in usage.pending
    assert (utc_day(), "app_used") in usage.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"game_paused": True}, {"connected": False}, {"speed_kph": 0}, {"session_time_s": 10}, {"packet_group_freshness": {}}, {"packet_group_freshness": {"6": 1}}, {"session_uid": 0}])
async def test_paused_stationary_stale_or_frozen_data_never_activates(usage, changes):
    await usage.set_enabled(True)
    for now in range(100, 180):
        usage.observe_racing(driving_snapshot(now, **changes), now=now)
    assert (utc_day(), "racing") not in usage.pending


@pytest.mark.asyncio
async def test_session_switch_and_pause_reset_qualification(usage):
    await usage.set_enabled(True)
    for now in range(100, 145):
        usage.observe_racing(driving_snapshot(now), now=now)
    for now in range(145, 190):
        usage.observe_racing(driving_snapshot(now, session_uid=1000), now=now)
    assert (utc_day(), "racing") not in usage.pending
    usage.observe_racing(driving_snapshot(190, session_uid=1000, game_paused=True), now=190)
    for now in range(191, 235):
        usage.observe_racing(driving_snapshot(now, session_uid=1000), now=now)
    assert (utc_day(), "racing") not in usage.pending


@pytest.mark.asyncio
async def test_engineer_hook_never_receives_radio_content(usage):
    await usage.set_enabled(True)
    store = StateStore()
    store.usage_event = usage.record
    await store.append_radio("driver", "private driver words")
    assert (utc_day(), "engineer") not in usage.pending
    await store.append_radio("engineer", "private engineer words")
    assert (utc_day(), "engineer") in usage.pending
    assert "private" not in json.dumps(usage._state())


def test_local_api_choice_security_and_strict_input(usage):
    app = FastAPI()
    app.include_router(create_usage_router(usage))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/usage").headers["cache-control"] == "no-store"
        assert client.post("/api/v1/usage", json={"enabled": "true"}).status_code == 422
        assert client.post("/api/v1/usage", json={"enabled": True, "text": "secret"}).status_code == 422
        assert client.post("/api/v1/usage", json={"enabled": True}, headers={"origin": "https://other.test"}).status_code == 403
        assert client.post("/api/v1/usage", json={"enabled": True}, headers={"host": "rebound.test"}).status_code == 403
        assert not usage.enabled
        assert client.post("/api/v1/usage", json={"enabled": False}).json()["decided"]
        assert not usage.enabled
        assert client.post("/api/v1/usage", json={"enabled": True}).json()["enabled"]
        client.post("/api/v1/usage/active")
        assert (utc_day(), "app_used") in usage.pending
        assert client.post("/api/v1/usage", json={"enabled": False}).status_code == 200
        assert not usage.pending
