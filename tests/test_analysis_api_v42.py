from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.analysis import create_analysis_router
from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.trace_archive import TraceArchiveService
from pitwall.trace_store import TraceStore


async def _lap(database: PitWallDatabase, archive: TraceArchiveService, number: int) -> str:
    trace = [
        {
            "d": float(distance),
            "t": distance / (49.0 if number == 2 else 50.0),
            "speed": 180.0,
            "brake": 0.7 if 40 <= distance <= 50 else 0.0,
            "throttle": 0.0 if 40 <= distance <= 50 else 1.0,
            "steer": 0.2 if 45 <= distance <= 60 else 0.0,
            "gear": 5,
        }
        for distance in range(101)
    ]
    lap = {
        "session_uid": 99,
        "player_car_index": 0,
        "packet_format": 2026,
        "track_id": 1,
        "track_length_m": 100,
        "track_name": "API Track",
        "session_type": "Time Trial",
        "lap_num": number,
        "lap_time_ms": 2000 + number * 10,
        "valid": True,
        "compound": "SOFT",
        "weather": "Clear",
        "trace": trace,
    }
    await database.upsert_session(lap)
    key = await database.save_lap(lap, [])
    assert key
    await archive.archive_player_lap(lap, recorded_lap_id=key)
    return key


@pytest.mark.asyncio
async def test_analysis_router_exposes_real_trace_and_comparison(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)
    reference = await _lap(database, archive, 1)
    candidate = await _lap(database, archive, 2)
    service = ComparisonService(database.path, trace_store)
    app = FastAPI()
    app.include_router(create_analysis_router(service))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        trace = await client.get(
            f"/api/v1/laps/{candidate}/trace",
            params={"fields": "speed,brake,missing", "max_points": 40},
        )
        assert trace.status_code == 200
        assert trace.json()["series"]["missing"]["availability"] == "unavailable"

        references = await client.get(f"/api/v1/laps/{candidate}/references")
        assert references.status_code == 200
        assert references.json()["items"][0]["lap_id"] == reference

        created = await client.post(
            "/api/v1/comparisons",
            json={
                "candidate_lap_id": candidate,
                "reference": {"kind": "lap", "lap_id": reference},
            },
        )
        assert created.status_code == 201, created.text
        comparison_id = created.json()["comparison_id"]
        reopened = await client.get(f"/api/v1/comparisons/{comparison_id}")
        assert reopened.status_code == 200
        assert reopened.json()["comparison_id"] == comparison_id
        explained = await client.post(f"/api/v1/comparisons/{comparison_id}/explain")
        assert explained.status_code == 200
        assert explained.json()["source"] == "deterministic_fallback"


@pytest.mark.asyncio
async def test_analysis_router_rejects_missing_lap_and_bad_range(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    app = FastAPI()
    app.include_router(
        create_analysis_router(ComparisonService(database.path, TraceStore(tmp_path / "traces")))
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/v1/laps/lap_missing/trace")
        assert missing.status_code == 404
        invalid = await client.get(
            "/api/v1/laps/lap_missing/trace", params={"from_m": 2, "to_m": 1}
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_a_lap_can_be_analyzed_on_its_own_with_no_reference(tmp_path: Path) -> None:
    """Lap Lab could only answer "how does this differ from that one".

    A driver with a single interesting lap — a first visit to a circuit, a
    one-off session — had nothing to look at. Everything returned here is
    measured from the lap's own trace.
    """
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)
    only_lap = await _lap(database, archive, 1)
    service = ComparisonService(database.path, trace_store)
    app = FastAPI()
    app.include_router(create_analysis_router(service))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/laps/{only_lap}/analysis")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["lap_id"] == only_lap
        assert payload["lap_number"] == 1
        assert payload["tyre_compound"] == "SOFT"
        assert payload["top_speed_kph"] == 180.0
        # The synthetic lap brakes exactly once, from 40 m to 50 m.
        assert payload["braking_events"] == 1
        assert 0 < payload["braking_pct"] < 100
        assert payload["full_throttle_pct"] > 50
        assert payload["segments"], "per-segment detail is the point of the view"
        for segment in payload["segments"]:
            assert segment["time_s"] > 0
            assert segment["minimum_speed_kph"] > 0
            assert segment["availability"] == "observed"
        # No reference means no delta and no verdict, and it says so.
        assert "no reference lap" in payload["availability_note"]
        assert "delta_s" not in payload

        missing = await client.get("/api/v1/laps/does-not-exist/analysis")
        assert missing.status_code == 404
