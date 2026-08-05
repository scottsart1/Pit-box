from __future__ import annotations

import sqlite3

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.sessions import create_sessions_router
from pitwall.capture import CaptureWriter, scan_capture
from pitwall.database import PitWallDatabase
from pitwall.trace_store import TraceStore


def _lap(uid: int, lap_number: int, *, valid: bool = True) -> dict[str, object]:
    return {
        "session_uid": uid,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "mode_profile": "race",
        "lap_num": lap_number,
        "lap_time_ms": 91_000 + lap_number,
        "valid": valid,
        "compound": "MEDIUM",
        "tyre_age_start": lap_number - 1,
        "tyre_age_end": lap_number,
        "wear_start": [10, 10, 10, 10],
        "wear_end": [11, 11, 11, 11],
        "temps_end": [90, 90, 92, 92],
        "track_temp_c": 31,
        "air_temp_c": 22,
        "weather": "Clear",
        "fuel_start_kg": 40.0,
        "fuel_end_kg": 38.0,
        "position": 5,
        "setup": {},
        "trace": [{"d": 0.0, "t": 0.0}, {"d": 10.0, "t": 0.2}],
    }


async def _seed_database(tmp_path, *, session_count: int = 1):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    for offset in range(session_count):
        uid = 7_000 + offset
        await database.upsert_session(
            {
                "session_uid": uid,
                "track_id": 10 + offset,
                "track_name": "Spa",
                "session_type": "Race" if offset % 2 == 0 else "Practice",
                "mode_profile": "race",
                "total_laps": 2,
                "car_setup": {},
            }
        )
        await database.save_lap(_lap(uid, 1), [])
        await database.save_lap(_lap(uid, 2, valid=False), [])
    await database.catalog.sync_legacy()
    app = FastAPI()
    app.include_router(
        create_sessions_router(
            database.catalog,
            trace_root=tmp_path / "traces",
            capture_root=tmp_path / "captures",
        )
    )
    return database, app


@pytest.mark.asyncio
async def test_library_list_filters_cursor_get_patch_and_laps(tmp_path) -> None:
    _database, app = await _seed_database(tmp_path, session_count=4)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/sessions", params={"limit": 2})
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["schema_version"] == 1
        assert len(first_body["items"]) == 2
        assert first_body["has_more"] is True

        second = await client.get(
            "/api/v1/sessions",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
        )
        ids = [
            item["id"] for item in first_body["items"] + second.json()["items"]
        ]
        assert len(ids) == len(set(ids)) == 4

        malformed = await client.get(
            "/api/v1/sessions", params={"cursor": "not-a-cursor"}
        )
        assert malformed.status_code == 422
        assert malformed.json()["detail"]["code"] == "invalid_session_filter"

        target = ids[0]
        patched = await client.patch(
            f"/api/v1/sessions/{target}",
            json={"display_name": " Sunday race ", "tags": ["wet", "wet"], "starred": True},
        )
        assert patched.status_code == 200
        assert patched.json()["session"]["display_name"] == "Sunday race"
        assert patched.json()["session"]["tags"] == ["wet"]

        filtered = await client.get(
            "/api/v1/sessions", params={"starred": True, "search": "wet"}
        )
        assert [item["id"] for item in filtered.json()["items"]] == [target]

        laps = await client.get(
            f"/api/v1/sessions/{target}/laps", params={"valid": True}
        )
        assert laps.status_code == 200
        assert len(laps.json()["items"]) == 1
        assert laps.json()["items"][0]["id"].startswith("lap_")

        rejected = await client.patch(
            f"/api/v1/sessions/{target}", json={"status": "complete"}
        )
        assert rejected.status_code == 422
        null_patch = await client.patch(
            f"/api/v1/sessions/{target}", json={"display_name": None}
        )
        assert null_patch.status_code == 422

        missing = await client.get("/api/v1/sessions/ses_missing")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_quality_and_reprocess_are_versioned_and_idempotent(tmp_path) -> None:
    _database, app = await _seed_database(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        session_id = (await client.get("/api/v1/sessions")).json()["items"][0]["id"]
        quality = await client.get(f"/api/v1/sessions/{session_id}/quality")
        assert quality.status_code == 200
        report = quality.json()
        assert report["schema_version"] == 1
        assert report["laps"] == {
            "total": 2,
            "valid": 1,
            "with_trace": 0,
            "mean_coverage": 1.0,
        }
        assert report["packet_health_available"] is False
        assert report["warnings"]

        first = await client.post(f"/api/v1/sessions/{session_id}/reprocess")
        second = await client.post(f"/api/v1/sessions/{session_id}/reprocess")
        assert first.status_code == second.status_code == 202
        assert first.json()["reused"] is False
        assert second.json()["reused"] is True
        assert first.json()["job"]["id"] == second.json()["job"]["id"]
        assert first.json()["job"]["state"] == "queued"


@pytest.mark.asyncio
async def test_delete_requires_stopped_session_fresh_preview_and_exact_artifacts(
    tmp_path,
) -> None:
    database, app = await _seed_database(tmp_path)
    catalog = database.catalog
    page = await catalog.list_sessions(limit=10)
    session_id = page["items"][0]["id"]
    recorded_lap = (await catalog.list_laps(session_id))[0]

    traces = TraceStore(tmp_path / "traces")
    traces.append_samples(
        recorded_lap["session_car_id"],
        "telemetry",
        [{"distance": 0.0, "speed": 50.0}, {"distance": 5.0, "speed": 52.0}],
        axis_field="distance",
        axis_unit="m",
    )
    manifest = traces.finalize_lap(
        recorded_lap["id"], session_car_id=recorded_lap["session_car_id"]
    )
    await catalog.register_trace_manifest(session_id, manifest)

    capture_path = tmp_path / "captures" / "2026" / "race.pwcap"
    with CaptureWriter(capture_path) as writer:
        writer.write(b"packet", ("192.168.1.61", 50_000))
    await catalog.register_raw_capture(
        session_id,
        capture_path.relative_to(tmp_path / "captures").as_posix(),
        scan_capture(capture_path),
    )
    unrelated = tmp_path / "traces" / "leave-me-alone.txt"
    unrelated.write_text("unrelated", encoding="utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        active = await client.delete(f"/api/v1/sessions/{session_id}")
        assert active.status_code == 409
        assert active.json()["detail"]["code"] == "session_still_recording"

        with sqlite3.connect(database.path) as db:
            db.execute(
                "UPDATE recorded_sessions SET status='complete' WHERE id=?", (session_id,)
            )

        preview = await client.delete(f"/api/v1/sessions/{session_id}")
        assert preview.status_code == 200
        preview_body = preview.json()
        assert preview_body["phase"] == "preview"
        assert preview_body["irreversible"] is True
        assert preview_body["impact"]["records"]["laps"] == 2
        kinds = {item["kind"] for item in preview_body["impact"]["artifacts"]}
        assert kinds == {"trace_chunk", "trace_manifest", "raw_capture"}

        await client.patch(
            f"/api/v1/sessions/{session_id}", json={"tags": ["changed"]}
        )
        stale = await client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"X-Pitwall-Delete-Token": preview_body["confirmation_token"]},
        )
        assert stale.status_code == 409
        assert "changed after preview" in stale.json()["detail"]["message"]

        fresh = (await client.delete(f"/api/v1/sessions/{session_id}")).json()
        deleted = await client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"X-Pitwall-Delete-Token": fresh["confirmation_token"]},
        )
        assert deleted.status_code == 200
        result = deleted.json()
        assert result["phase"] == "deleted"
        assert result["deleted"] is True
        assert result["cleanup_errors"] == []

        missing = await client.get(f"/api/v1/sessions/{session_id}")
        assert missing.status_code == 404
        reuse = await client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"X-Pitwall-Delete-Token": fresh["confirmation_token"]},
        )
        assert reuse.status_code == 409

    assert unrelated.read_text(encoding="utf-8") == "unrelated"
    assert not capture_path.exists()
    assert all(
        not (tmp_path / "traces" / chunk.relative_path).exists()
        for chunk in manifest.chunks
    )
    assert not (tmp_path / "traces" / "manifests" / f"{manifest.id}.json").exists()

    # Confirmed deletion also removes the explicitly linked legacy rows, so a
    # later compatibility backfill cannot resurrect the session.
    with sqlite3.connect(database.path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM laps").fetchone()[0] == 0
    await catalog.sync_legacy()
    assert await catalog.get_session(session_id) is None
