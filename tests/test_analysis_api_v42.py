from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from pitwall.api.analysis import create_analysis_router
from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.trace_archive import TraceArchiveService
from pitwall.trace_store import TraceFormatError, TraceStore


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing_manifest", "missing_chunk", "corrupt_chunk", "permission_denied"]
)
@pytest.mark.parametrize("legacy_fallback", [False, True])
async def test_unreadable_typed_trace_is_reported_or_uses_existing_legacy_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    legacy_fallback: bool,
) -> None:
    """A listable lap with unavailable files must not fail as AttributeError500.

    Field-only recordings have no legacy JSON; older player recordings do.
    Keep that distinction when a transferred or interrupted trace is missing.
    """
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    lap_id = await _lap(database, TraceArchiveService(database, trace_store), 1)
    with sqlite3.connect(database.path) as connection:
        if not legacy_fallback:
            connection.execute(
                "UPDATE recorded_laps SET legacy_lap_id=NULL WHERE id=?", (lap_id,)
            )
        if failure == "missing_manifest":
            connection.execute(
                "UPDATE recorded_laps SET trace_manifest_id='tm_not_present' WHERE id=?",
                (lap_id,),
            )
    if failure != "missing_manifest":
        error_type = {
            "missing_chunk": FileNotFoundError,
            "corrupt_chunk": TraceFormatError,
            "permission_denied": PermissionError,
        }[failure]

        def unreadable(*args, **kwargs):
            raise error_type("recorded telemetry chunk is unavailable")

        monkeypatch.setattr(trace_store, "read_range", unreadable)

    app = FastAPI()
    app.include_router(create_analysis_router(ComparisonService(database.path, trace_store)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for endpoint in ("trace", "analysis"):
            response = await client.get(f"/api/v1/laps/{lap_id}/{endpoint}")
            if legacy_fallback:
                assert response.status_code == 200, response.text
                payload = response.json()
                source_key = "source" if endpoint == "trace" else "trace_source"
                assert payload[source_key] == "legacy_json"
                if endpoint == "trace":
                    assert payload["series"]["speed"]["values"] == [50.0] * 101
            else:
                assert response.status_code == 409, response.text
                detail = response.json()["detail"]
                assert detail["code"] == "trace_unavailable"
                assert lap_id in detail["message"]
                assert "cannot be read" in detail["message"]
    assert await database.catalog.get_session(
        (await ComparisonService(database.path, trace_store).lap_record(lap_id)).session_id
    ), "Missing trace files must not remove the recorded session"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [FileNotFoundError, TraceFormatError, PermissionError])
async def test_unreadable_optional_motion_keeps_primary_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    lap_id = await _lap(database, TraceArchiveService(database, trace_store), 1)
    with sqlite3.connect(database.path) as connection:
        connection.execute("UPDATE recorded_laps SET legacy_lap_id=NULL WHERE id=?", (lap_id,))
    original_read = trace_store.read_range

    def read_with_unavailable_motion(*args, **kwargs):
        if kwargs.get("sample_group") == "motion":
            raise error_type("optional motion chunk is unavailable")
        return original_read(*args, **kwargs)

    monkeypatch.setattr(trace_store, "read_range", read_with_unavailable_motion)
    app = FastAPI()
    app.include_router(create_analysis_router(ComparisonService(database.path, trace_store)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/laps/{lap_id}/trace", params={"fields": "speed,brake,world_x,world_z"}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["source"] == "trace_store"
        assert payload["series"]["speed"]["values"] == [50.0] * 101
        assert payload["series"]["brake"]["availability"] == "observed"
        for name in ("world_x", "world_z"):
            assert payload["series"][name]["availability"] == "unavailable"
            assert payload["series"][name]["values"] == [None] * 101
        analysis = await client.get(f"/api/v1/laps/{lap_id}/analysis")
        assert analysis.status_code == 200, analysis.text
        assert analysis.json()["top_speed_kph"] == 180.0


@pytest.mark.asyncio
@pytest.mark.parametrize("all_missing", [False, True])
async def test_single_lap_summary_keeps_missing_samples_json_safe_and_honest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, all_missing: bool
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    lap_id = await _lap(database, TraceArchiveService(database, trace_store), 1)
    service = ComparisonService(database.path, trace_store)
    original_lap_trace = service.lap_trace

    async def partial_trace(key):
        record, trace = await original_lap_trace(key)
        signals = {name: values.copy() for name, values in trace.signals.items()}
        for name in ("speed", "brake", "throttle"):
            signals[name][:] = np.nan if all_missing else signals[name]
        if not all_missing:
            signals["speed"][50] = np.nan
            signals["throttle"][10:20] = np.nan
            # An unknown-to-braking sample is not an observed brake onset.
            signals["brake"][39] = np.nan
        return record, replace(trace, signals=signals)

    monkeypatch.setattr(service, "lap_trace", partial_trace)
    app = FastAPI()
    app.include_router(create_analysis_router(service))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/laps/{lap_id}/analysis")
        assert response.status_code == 200, response.text
        assert "NaN" not in response.text
        payload = response.json()
        if all_missing:
            for key in (
                "top_speed_kph", "minimum_speed_kph", "average_speed_kph",
                "full_throttle_pct", "braking_pct", "braking_events",
            ):
                assert payload[key] is None, key
            assert set(payload["metric_coverage"].values()) == {0.0}
            for segment in payload["segments"]:
                assert segment["time_s"] is None
                assert segment["minimum_speed_kph"] is None
                assert segment["availability"] == "unavailable"
        else:
            assert payload["top_speed_kph"] == 180.0
            assert payload["minimum_speed_kph"] == 180.0
            assert payload["average_speed_kph"] == 180.0
            # Only intervals with both speed and control samples count in
            # each denominator; unknown controls are not treated as zero.
            assert payload["full_throttle_pct"] == 89.7
            assert payload["braking_pct"] == 9.4
            assert payload["braking_events"] == 0
            assert all(0 < value < 1 for value in payload["metric_coverage"].values())
            assert any(segment["time_s"] is None for segment in payload["segments"])
            assert any(segment["time_s"] is not None for segment in payload["segments"])
