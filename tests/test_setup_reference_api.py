"""Exercise setup selector contracts through HTTP and the actual voice tools."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

import pitwall.app as app_module


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"basis": "invented"}, {"basis": None},
    {"reference_style": "invented"}, {"reference_style": 12},
    {"conditions": "snow"}, {"conditions": []},
])
async def test_setup_api_rejects_invalid_selectors_before_generation(monkeypatch, invalid):
    generate = AsyncMock(side_effect=AssertionError("Invalid selector reached advisor"))
    monkeypatch.setattr(app_module.setup_advisor, "generate", generate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://test") as client:
        response = await client.post("/api/setup/recommend", json={"track_id": 0, **invalid})
    assert response.status_code == 422
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_reference_is_exact_despite_live_setup_and_preferences(stack, monkeypatch):
    store, _, _, advisor, *_ = stack
    raw_setup = {"front_wing": 45, "rear_wing": 43, "on_throttle": 30, "off_throttle": 70}
    await store.update(
        session_uid=55123, track_id=0, track_name="Melbourne", car_setup=deepcopy(raw_setup),
        driver_preferences={"rotation": 3, "rear_stability": 3, "traction": 3, "tyre_life": 3, "straight_line": 3},
    )
    monkeypatch.setattr(app_module, "setup_advisor", advisor)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://test") as client:
        stable = await client.post("/api/setup/recommend", json={"track_id": 0, "basis": "reference", "conditions": "dry", "reference_style": "stable", "profile": "race"})
        assert stable.status_code == 200
        data = stable.json()
        assert data["recommended"] == data["foundational"] == data["baseline_reference"]["setup"]
        assert data["recommended"]["front_wing"] == 21
        assert data["recommended"]["rear_wing"] == 17
        assert data["driver_preferences"] == {}
        assert not data["pit_adjustment"]["available"]
        assert data["setup_effects"]["calibrated"] is False
        assert data["baseline_reference"]["sources"]
        race = await client.post("/api/setup/recommend", json={"track_id": 0, "basis": "reference", "conditions": "dry", "reference_style": "rotation", "profile": "race"})
        quali = await client.post("/api/setup/recommend", json={"track_id": 0, "basis": "reference", "conditions": "dry", "reference_style": "rotation", "profile": "quali"})
        assert race.status_code == quali.status_code == 200
        assert (race.json()["recommended"]["front_wing"], race.json()["recommended"]["rear_wing"]) == (42, 15)
        assert (quali.json()["recommended"]["front_wing"], quali.json()["recommended"]["rear_wing"]) == (30, 0)
        wet = await client.post("/api/setup/recommend", json={"track_id": 0, "basis": "reference", "conditions": "wet"})
        assert wet.status_code == 409
        assert "wet" in wet.json()["detail"].lower()
    assert (await store.snapshot_analysis())["car_setup"] == raw_setup


@pytest.mark.asyncio
async def test_voice_requests_fresh_reference_and_forwards_explicit_selectors(stack, monkeypatch):
    _, _, _, advisor, _, tools = stack
    generate = AsyncMock(return_value={"available": True, "marker": "fresh"})
    monkeypatch.setattr(advisor, "generate", generate)
    assert (await tools.generate_setup())["marker"] == "fresh"
    generate.assert_awaited_once_with("hybrid", None, "minimum", basis="reference", conditions="auto", reference_style="stable")
    generate.reset_mock()
    await tools.generate_setup("quali", 0, "radical", "personalized", "dry", "rotation")
    generate.assert_awaited_once_with("quali", 0, "radical", basis="personalized", conditions="dry", reference_style="rotation")


@pytest.mark.asyncio
async def test_voice_pit_wing_does_not_reuse_cached_garage_reference(stack, monkeypatch):
    store, _, _, advisor, _, tools = stack
    await store.update(setup_recommendation={"available": True, "basis": "reference", "pit_adjustment": {"available": True, "next_front_wing": 50}})
    fresh = {"available": False, "instruction": "No live wing change is supported."}
    generate = AsyncMock(return_value={"available": True, "pit_adjustment": fresh})
    monkeypatch.setattr(advisor, "generate", generate)
    assert await tools.get_front_wing_adjustment() == fresh
    generate.assert_awaited_once_with("hybrid", basis="personalized", change_level="minimum")
