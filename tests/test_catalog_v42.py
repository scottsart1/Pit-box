from __future__ import annotations

import sqlite3

import pytest

from pitwall.capture import CaptureWriter, scan_capture
from pitwall.catalog import SessionCatalog, session_car_id, session_id
from pitwall.database import PitWallDatabase
from pitwall.trace_store import TraceStore


def _lap(uid: int, number: int) -> dict[str, object]:
    return {
        "session_uid": uid,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "mode_profile": "race",
        "lap_num": number,
        "lap_time_ms": 92_000 + number,
        "valid": True,
        "compound": "MEDIUM",
        "tyre_age_start": number - 1,
        "tyre_age_end": number,
        "wear_start": [10, 10, 11, 11],
        "wear_end": [12, 12, 13, 13],
        "temps_end": [90, 91, 95, 96],
        "track_temp_c": 31,
        "air_temp_c": 22,
        "weather": "Clear",
        "fuel_start_kg": 40,
        "fuel_end_kg": 38,
        "position": 7,
        "setup": {},
        "trace": [{"d": 0.0, "t": 1.0}, {"d": 10.0, "t": 1.2}],
    }


@pytest.mark.asyncio
async def test_legacy_catalog_backfill_is_batched_idempotent_and_uint64_safe(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    uid = (1 << 64) - 9
    await database.upsert_session(
        {
            "session_uid": uid,
            "track_id": 10,
            "track_name": "Spa",
            "session_type": "Race",
            "mode_profile": "race",
            "total_laps": 3,
            "car_setup": {},
        }
    )
    await database.save_lap(_lap(uid, 1), [])
    await database.save_lap(_lap(uid, 2), [])
    # Simulate an installation created before the additive catalog existed;
    # current writes dual-write and therefore need no backfill.
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM recorded_laps")

    catalog = SessionCatalog(path)
    first = await catalog.sync_legacy(batch_size=1)
    second = await catalog.sync_legacy(batch_size=10)
    third = await catalog.sync_legacy(batch_size=10)
    assert first["laps_inserted"] == 1
    assert first["laps_remaining"] == 1
    assert second["laps_inserted"] == 1
    assert second["laps_remaining"] == 0
    assert third["laps_inserted"] == 0

    key = session_id(uid)
    session = await catalog.get_session(key)
    assert session is not None
    assert session["game_session_uid"] == str(uid)
    assert session["participants"][0]["anonymized_name"] == "Driver 01"
    laps = await catalog.list_laps(key)
    assert [item["lap_number"] for item in laps] == [1, 2]
    assert all(item["id"].startswith("lap_") for item in laps)


@pytest.mark.asyncio
async def test_live_catalog_uses_restart_and_car_identity_in_opaque_keys(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    catalog = SessionCatalog(path)
    state = {
        "session_uid": 1234,
        "restart_epoch": 2,
        "player_car_index": 7,
        "track_id": 10,
        "track_length_m": 7004,
        "packet_format": 2026,
        "session_type": "Race",
        "mode_profile": "race",
        "drivers": [
            {
                "car_idx": 7,
                "name": "Player One",
                "driver_id": 42,
                "race_number": 77,
                "team_id": 3,
                "ai_controlled": False,
            }
        ],
    }
    key = await catalog.upsert_live_session(state)
    assert key == session_id(1234, 2)
    session = await catalog.get_session(key)
    assert session is not None
    assert session["track_layout_signature"] == "f1:2026:10:7004"
    assert session["participants"][0]["id"] == session_car_id(key, 7)
    assert session["participants"][0]["display_name"] == "Player One"


@pytest.mark.asyncio
async def test_recording_sessions_are_finalized_without_downgrading_results(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    catalog = SessionCatalog(path)

    first = await catalog.upsert_live_session({"session_uid": 1001})
    second = await catalog.upsert_live_session({"session_uid": 1002})
    assert first is not None and second is not None
    assert await catalog.finalize_session(first, status="complete") is True
    assert await catalog.finalize_recording_sessions(exclude_session_id=first) == 1

    completed = await catalog.get_session(first)
    interrupted = await catalog.get_session(second)
    assert completed is not None and completed["status"] == "complete"
    assert interrupted is not None and interrupted["status"] == "incomplete"
    assert interrupted["ended_at"] is not None

    # A late live-state write must not turn a classified result back into an
    # actively recording session.
    await catalog.upsert_live_session({"session_uid": 1001})
    completed = await catalog.get_session(first)
    assert completed is not None and completed["status"] == "complete"


@pytest.mark.asyncio
async def test_finalize_session_validates_status(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    with pytest.raises(ValueError, match="complete.*incomplete"):
        await database.catalog.finalize_session("missing", status="recording")


@pytest.mark.asyncio
async def test_library_pagination_and_metadata_patch_are_deterministic(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    catalog = SessionCatalog(path)
    for uid in range(1, 5):
        await catalog.upsert_live_session(
            {
                "session_uid": uid,
                "track_id": uid,
                "track_length_m": 5000 + uid,
                "packet_format": 2026,
                "session_type": "Practice" if uid % 2 else "Race",
                "mode_profile": "practice" if uid % 2 else "race",
            }
        )

    first = await catalog.list_sessions(limit=2)
    second = await catalog.list_sessions(limit=2, cursor=first["next_cursor"])
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 4
    assert first["has_more"] is True
    assert second["has_more"] is False

    target = ids[0]
    assert await catalog.patch_session(
        target, display_name="Sunday race", tags=["wet", " wet ", "league"], starred=True
    )
    updated = await catalog.get_session(target)
    assert updated is not None
    assert updated["display_name"] == "Sunday race"
    assert updated["tags"] == ["league", "wet"]
    assert updated["starred"] is True
    filtered = await catalog.list_sessions(limit=10, starred=True, search="league")
    assert [item["id"] for item in filtered["items"]] == [target]


@pytest.mark.asyncio
async def test_trace_and_capture_catalog_registration_is_transactional(tmp_path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    database = PitWallDatabase(path)
    await database.initialize()
    uid = 808
    await database.upsert_session(
        {
            "session_uid": uid,
            "track_id": 10,
            "track_name": "Spa",
            "session_type": "Race",
            "mode_profile": "race",
            "total_laps": 1,
            "car_setup": {},
        }
    )
    await database.save_lap(_lap(uid, 1), [])
    catalog = SessionCatalog(path)
    await catalog.sync_legacy()
    key = session_id(uid)
    recorded_lap = (await catalog.list_laps(key))[0]

    traces = TraceStore(tmp_path / "traces")
    traces.append_samples(
        recorded_lap["session_car_id"],
        "telemetry",
        [
            {"distance": 0.0, "speed": 50.0, "brake": 0.0},
            {"distance": 10.0, "speed": 55.0, "brake": 0.2},
        ],
        axis_field="distance",
        axis_unit="m",
    )
    manifest = traces.finalize_lap(
        recorded_lap["id"], session_car_id=recorded_lap["session_car_id"]
    )
    await catalog.register_trace_manifest(key, manifest)
    refreshed = (await catalog.list_laps(key))[0]
    assert refreshed["trace_manifest_id"] == manifest.id
    assert refreshed["trace_state"] == "ready"

    capture_root = tmp_path / "captures"
    capture_path = capture_root / "2026" / "session.pwcap"
    with CaptureWriter(capture_path) as writer:
        writer.write(b"packet", ("192.168.1.61", 50_000))
    report = scan_capture(capture_path)
    capture_id = await catalog.register_raw_capture(
        key, capture_path.relative_to(capture_root).as_posix(), report
    )
    assert capture_id.startswith("cap_")
    library = await catalog.list_sessions(limit=10)
    item = next(value for value in library["items"] if value["id"] == key)
    assert item["size_bytes"] >= report.file_size
