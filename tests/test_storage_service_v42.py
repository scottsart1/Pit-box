from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.api.storage import create_storage_router
from pitwall.capture import CaptureWriter, scan_capture
from pitwall.capture_service import CaptureService
from pitwall.database import PitWallDatabase
from pitwall.storage_service import RetentionPolicy, StorageService


@pytest.mark.asyncio
async def test_retention_preview_protects_starred_and_recording_sessions(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    old = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    with sqlite3.connect(database.path) as db:
        for key, starred, status in (
            ("old-delete", 0, "complete"),
            ("old-starred", 1, "complete"),
            ("old-recording", 0, "recording"),
        ):
            db.execute(
                """
                INSERT INTO recorded_sessions(
                    id, game_session_uid, restart_epoch, status, starred,
                    capture_mode, created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, 'balanced', ?, ?)
                """,
                (key, key, status, starred, old, old),
            )
        db.commit()
    service = StorageService(
        database.path,
        tmp_path,
        policy=RetentionPolicy(10_000_000, 90, 1),
    )

    preview = await service.preview_retention()

    assert [item["session_id"] for item in preview["candidates"]] == ["old-delete"]
    assert preview["automatic_deletion"] is False
    assert preview["protected"] == {
        "starred": 1,
        "recording": 1,
        "unassigned_capture_bytes": 0,
        "active_capture_bytes": 0,
    }


@pytest.mark.asyncio
async def test_storage_counts_unassigned_and_active_captures(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    capture_root = tmp_path / "captures"
    closed_path = capture_root / "unassigned.pwcap"
    with CaptureWriter(closed_path) as writer:
        writer.write(b"closed", ("127.0.0.1", 20_777))
    closed = scan_capture(closed_path)
    await database.catalog.register_raw_capture(
        None, "unassigned.pwcap", closed
    )

    active = CaptureService(capture_root)
    await active.start(relative_path="active.pwcap")
    active.submit(b"active", ("127.0.0.1", 20_777))
    await active.queue.join()
    service = StorageService(
        database.path,
        tmp_path,
        policy=RetentionPolicy(10_000_000, 90, 1),
        capture_service=active,
    )

    status = await service.status()
    preview = await service.preview_retention()

    assert status["catalogued_capture_bytes"] == closed.file_size
    assert status["unassigned_capture_bytes"] == closed.file_size
    assert status["active_capture_bytes"] > 0
    assert status["capture_bytes"] == (
        closed.file_size + status["active_capture_bytes"]
    )
    assert preview["protected"]["unassigned_capture_bytes"] == closed.file_size
    assert preview["protected"]["active_capture_bytes"] > 0
    await active.stop()


def test_storage_api_reports_budget_and_preview(tmp_path: Path) -> None:
    path = tmp_path / "pitwall.sqlite3"

    async def initialize() -> None:
        await PitWallDatabase(path).initialize()

    import asyncio

    asyncio.run(initialize())
    service = StorageService(
        path,
        tmp_path,
        policy=RetentionPolicy(1_000, 30, 1),
    )
    app = FastAPI()
    app.include_router(create_storage_router(service))
    client = TestClient(app)

    status = client.get("/api/v1/storage/status")
    preview = client.get("/api/v1/storage/retention/preview")

    assert status.status_code == 200
    assert status.json()["schema_version"] == 1
    assert preview.status_code == 200
    assert preview.json()["requires_per_session_confirmation"] is True
