import asyncio
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.updates import create_updates_router
from pitwall.update_service import UpdateService, validate_release, version_parts


def manifest(version="4.12.0"):
    return {"schema_version": 1, "platform": "windows", "release": {"version": version,
            "download_url": "https://yourpitbox.com/#windows-release", "notes": "New update",
            "published_at": datetime.now(timezone.utc).isoformat(), "sha256": "a" * 64, "size": 123}}


def test_numeric_versions_and_untrusted_manifests():
    assert version_parts("4.11.0") > version_parts("4.9.9")
    for version in ["4.11", "4.11.1beta", "04.11.1", "1.2.9999999", None]:
        with pytest.raises(ValueError):
            version_parts(version)
    for field, value in [("download_url", "https://attacker.test/install"), ("notes", "x" * 4001), ("size", True), ("sha256", "bad")]:
        data = manifest(); data["release"][field] = value
        with pytest.raises(ValueError):
            validate_release(data, "windows")


@pytest.mark.asyncio
async def test_checks_only_platform_deduplicates_and_persists_dismissal(tmp_path):
    calls = []
    def remote(request):
        calls.append(request)
        return httpx.Response(200, json=manifest())
    service = UpdateService(tmp_path / "updates.json", platform="windows", current_version="4.11.0")
    async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as client:
        result = await service.check(client)
        assert result["available"] and result["release"]["version"] == "4.12.0"
        await service.check(client)
    assert len(calls) == 1
    assert str(calls[0].url).endswith("/releases?platform=windows")
    assert not calls[0].content
    assert "authorization" not in calls[0].headers
    await service.preferences(enabled=False, dismiss=True)
    assert service.status()["dismissed"]
    reloaded = UpdateService(service.path, platform="windows")
    assert not reloaded.enabled and reloaded.dismissed == "4.12.0"
    assert set(json.loads(service.path.read_text())) == {"enabled", "dismissed"}


@pytest.mark.asyncio
async def test_offline_huge_responses_and_missing_release_are_nonfatal(tmp_path):
    for response in [httpx.Response(503), httpx.Response(200, text="x" * 32769, headers={"Content-Type": "application/json"}), httpx.Response(302, headers={"Location": "https://attacker.test/"})]:
        service = UpdateService(tmp_path / "none", platform="windows")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            result = await service.check(client)
        assert result["outcome"] == "unavailable" and not result["available"]
    service = UpdateService(tmp_path / "none", platform="windows")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"schema_version": 1, "platform": "windows", "release": None}))) as client:
        assert (await service.check(client))["outcome"] == "not_published"


@pytest.mark.asyncio
async def test_newer_or_same_local_version_and_expiry_do_not_offer_downgrades(tmp_path):
    for version in ["4.12.0", "5.0.0"]:
        service = UpdateService(tmp_path / "none", platform="windows", current_version=version)
        service.latest = manifest()["release"]; service._success = time.monotonic()
        assert not service.status()["available"]
        service._success = time.monotonic() - 86401
        assert service.status()["release"] is None
    service = UpdateService(tmp_path / "none", platform="windows")
    await service.start(); await service.stop()  # startup doesn't wait on a network request
    assert service._task is None


@pytest.mark.asyncio
async def test_update_controls_reject_cross_origin_and_remote_clients(tmp_path):
    service = UpdateService(tmp_path / "none", platform="windows")
    app = FastAPI(); app.include_router(create_updates_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://127.0.0.1:8000") as client:
        assert (await client.get("/api/v1/updates")).status_code == 200
        assert (await client.post("/api/v1/updates", json={"enabled": False}, headers={"Origin": "https://attacker.test"})).status_code == 403
        assert (await client.post("/api/v1/updates", json={"enabled": "yes"})).status_code == 422
        assert (await client.post("/api/v1/updates", json={"enabled": False})).status_code == 200
    assert not service.enabled
