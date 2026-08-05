from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from pitwall.api.track_models import create_track_models_router
from pitwall.database import PitWallDatabase
from pitwall.telemetry.track_model import TrackBuildConfig
from pitwall.trace_store import TraceStore
from pitwall.track_model_service import TrackModelCorruptError, TrackModelService


async def _seed_motion_session(
    tmp_path,
    *,
    lap_count: int = 3,
    sample_group: str = "motion",
):
    data_root = tmp_path / "data"
    database = PitWallDatabase(data_root / "pitwall.db")
    await database.initialize()
    trace_store = TraceStore(data_root / "traces")
    session_id = "ses_track_service"
    car_id = "car_track_service"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO recorded_sessions(
                id, game_session_uid, restart_epoch, track_id,
                track_layout_signature, session_type, status, packet_format,
                capture_mode, created_at, updated_at
            ) VALUES (?, '4242', 0, 10, 'spa-test-layout', 'Practice',
                      'complete', 2026, 'balanced', ?, ?)
            """,
            (session_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO session_cars(
                id, session_id, car_index, identity_revision, display_name,
                anonymized_name, is_ai, is_player, identity_confidence
            ) VALUES (?, ?, 0, 0, 'Player', 'Driver 01', 0, 1, 1.0)
            """,
            (car_id, session_id),
        )

    random = np.random.default_rng(4200)
    angle = np.linspace(0.0, 2.0 * np.pi, 360, endpoint=False)
    distance = np.linspace(0.0, 627.0, angle.size)
    for lap_number in range(1, lap_count + 1):
        lap_id = f"lap_track_{lap_number}"
        phase = lap_number * 17
        x = np.roll(100.0 * np.cos(angle), phase)
        z = np.roll(100.0 * np.sin(angle), phase)
        x = x + random.normal(0.0, 0.04, x.shape)
        z = z + random.normal(0.0, 0.04, z.shape)
        trace_store.append_samples(
            car_id,
            sample_group,
            {
                "lap_distance_m": distance,
                "world_x": x,
                "world_z": z,
                "speed_mps": np.full(distance.shape, 50.0),
            },
            axis_field="lap_distance_m",
            axis_unit="m",
            field_metadata={
                "world_x": {"unit": "m", "provenance": "observed"},
                "world_z": {"unit": "m", "provenance": "observed"},
            },
        )
        manifest = trace_store.finalize_lap(
            lap_id,
            session_car_id=car_id,
            manifest_id=f"tm_track_{lap_number}",
        )
        with sqlite3.connect(database.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                """
                INSERT INTO recorded_laps(
                    id, session_car_id, lap_number, timeline_epoch, lap_time_ms,
                    valid, pit_context, flag_context, coverage_ratio,
                    quality_score, created_at
                ) VALUES (?, ?, ?, 0, ?, 1, 0, 0, 1.0, 0.99, ?)
                """,
                (lap_id, car_id, lap_number, 90_000 + lap_number, now),
            )
        await database.catalog.register_trace_manifest(session_id, manifest)

    service = TrackModelService(
        database.path,
        trace_store,
        data_root,
        build_config=TrackBuildConfig(
            resample_points=180,
            min_clean_trajectories=3,
        ),
    )
    return database, trace_store, service, session_id


@pytest.mark.asyncio
async def test_build_persists_quality_gated_model_and_default_review_segments(
    tmp_path,
) -> None:
    database, _trace_store, service, session_id = await _seed_motion_session(tmp_path)

    built = await service.build_for_session(session_id)
    assert built["status"] == "published"
    assert built["quality"]["publishable"] is True
    assert len(built["source_lap_ids"]) == 3
    assert len(built["segment_model"]["segments"]) == 10
    model_id = built["model"]["id"]
    with sqlite3.connect(database.path) as connection:
        relative_path = connection.execute(
            "SELECT relative_path FROM track_models WHERE id=?", (model_id,)
        ).fetchone()[0]
    model_path = service.data_root / relative_path
    assert model_path.is_file()
    assert not list(model_path.parent.glob("*.tmp"))

    status = await service.session_status(session_id)
    assert status["status"] == "ready"
    assert status["candidate_laps"] == 3
    assert status["model"]["id"] == model_id

    projection = await service.get_model(model_id, max_points=64)
    assert projection["geometry"]["source_points"] == 180
    assert projection["geometry"]["returned_points"] == 64
    assert projection["geometry"]["coordinate_system"] == "game_world_xz"
    assert projection["segment_model"]["source"] == "equal_distance_review_v1"

    reused = await service.build_for_session(session_id)
    assert reused["status"] == "reused"
    assert reused["model"]["id"] == model_id
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM track_models").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 10

    rebuilt = await service.build_for_session(session_id, force=True)
    assert rebuilt["status"] == "published"
    assert rebuilt["model"]["model_version"] == 2
    assert rebuilt["model"]["id"] != model_id
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM track_models").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 20
        assert connection.execute(
            "SELECT COUNT(*) FROM track_models WHERE active=1"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_insufficient_saved_motion_requires_calibration_without_catalog_write(
    tmp_path,
) -> None:
    database, _trace_store, service, session_id = await _seed_motion_session(
        tmp_path, lap_count=1
    )

    result = await service.build_for_session(session_id)
    assert result["status"] == "map_calibration_required"
    assert result["model"] is None
    assert "need at least 3 clean trajectories" in result["quality"]["reasons"][0]
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM track_models").fetchone()[0] == 0
    assert not list(service.model_root.rglob("*.pwm"))


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_group", ["telemetry", "player_trace"])
async def test_player_group_world_positions_can_build_track_model(
    tmp_path,
    sample_group: str,
) -> None:
    _database, _trace_store, service, session_id = await _seed_motion_session(
        tmp_path,
        sample_group=sample_group,
    )

    result = await service.build_for_session(session_id)
    assert result["status"] == "published"
    assert result["quality"]["clean_trajectories"] == 3
    assert result["source_rejections"] == []


@pytest.mark.asyncio
async def test_corrupt_source_trace_is_reported_and_other_clean_laps_still_build(
    tmp_path,
) -> None:
    _database, trace_store, service, session_id = await _seed_motion_session(
        tmp_path, lap_count=5
    )
    damaged_manifest = trace_store.load_manifest("tm_track_1")
    damaged_path = trace_store.root / damaged_manifest.chunks[0].relative_path
    damaged = bytearray(damaged_path.read_bytes())
    damaged[-1] ^= 0xFF
    damaged_path.write_bytes(damaged)

    result = await service.build_for_session(session_id)
    assert result["status"] == "published", result
    assert len(result["source_lap_ids"]) == 4
    assert result["source_rejections"][0]["lap_id"] == "lap_track_1"
    assert "checksum mismatch" in result["source_rejections"][0]["reason"]


@pytest.mark.asyncio
async def test_track_model_api_and_corrupt_file_diagnostics(tmp_path) -> None:
    _database, _trace_store, service, session_id = await _seed_motion_session(tmp_path)
    app = FastAPI()
    app.include_router(create_track_models_router(service))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        built = await client.post(
            f"/api/v1/sessions/{session_id}/track-model/build",
            json={"max_laps": 12, "review_segments": 8},
        )
        assert built.status_code == 200
        body = built.json()
        assert body["status"] == "published"
        assert len(body["segment_model"]["segments"]) == 8
        model_id = body["model"]["id"]

        fetched = await client.get(
            f"/api/v1/track-models/{model_id}", params={"max_points": 40}
        )
        assert fetched.status_code == 200
        assert fetched.json()["geometry"]["returned_points"] == 40
        missing = await client.get("/api/v1/track-models/does-not-exist")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "track_model_not_found"

        with sqlite3.connect(service.database_path) as connection:
            relative = connection.execute(
                "SELECT relative_path FROM track_models WHERE id=?", (model_id,)
            ).fetchone()[0]
        path = service.data_root / relative
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 0xFF
        path.write_bytes(damaged)
        status_response = await client.get(
            f"/api/v1/sessions/{session_id}/track-model"
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "corrupt"
        corrupt = await client.get(f"/api/v1/track-models/{model_id}")
        assert corrupt.status_code == 409
        assert corrupt.json()["detail"]["code"] == "track_model_corrupt"

    with pytest.raises(TrackModelCorruptError):
        await service.get_model(model_id)
