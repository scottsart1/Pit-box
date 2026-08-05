from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pitwall.analysis_jobs import AnalysisJobService
from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.trace_archive import TraceArchiveService
from pitwall.trace_store import TraceStore


class _TrackModels:
    def __init__(self) -> None:
        self.sessions: list[str] = []

    async def build_for_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        max_laps: int = 12,
        review_segments: int = 10,
    ) -> dict[str, object]:
        del force, max_laps, review_segments
        self.sessions.append(session_id)
        return {"status": "published"}


def _lap(number: int, time_ms: int) -> dict[str, object]:
    return {
        "session_uid": 9_001,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "player_car_index": 0,
        "packet_format": 2026,
        "track_id": 7,
        "track_name": "Job Circuit",
        "track_length_m": 100,
        "session_type": "Time Trial",
        "mode_profile": "time_trial",
        "lap_num": number,
        "lap_time_ms": time_ms,
        "valid": True,
        "compound": "SOFT",
        "weather": "Clear",
        "trace": [
            {
                "d": float(distance),
                "t": distance / 25 + (0.1 if number == 2 else 0.0),
                "speed": 180.0,
                "brake": 0.0,
                "throttle": 1.0,
                "steer": 0.0,
                "gear": 5,
                "x": float(distance),
                "z": 0.0,
            }
            for distance in range(101)
        ],
    }


@pytest.mark.asyncio
async def test_durable_reprocess_job_builds_comparisons_and_completes(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    trace_archive = TraceArchiveService(database, trace_store)
    for lap in (_lap(1, 4_000), _lap(2, 4_100)):
        await database.upsert_session(lap)
        recorded_id = await database.save_lap(lap, [])
        assert recorded_id is not None
        await trace_archive.archive_player_lap(lap, recorded_lap_id=recorded_id)
    session = (await database.catalog.list_sessions())["items"][0]
    requested = await database.catalog.request_reprocess(str(session["id"]))
    assert requested is not None

    track_models = _TrackModels()
    jobs = AnalysisJobService(
        database.path,
        ComparisonService(database.path, trace_store),
        track_model_builder=track_models,
        worker_count=1,
        queue_size=4,
    )
    await jobs.start()
    await jobs.wait_idle()
    await jobs.stop()

    with sqlite3.connect(database.path) as db:
        state = db.execute(
            "SELECT state, progress FROM analysis_jobs WHERE id=?",
            (requested["job"]["id"],),
        ).fetchone()
        comparison_count = db.execute("SELECT COUNT(*) FROM comparisons").fetchone()[0]
        audit = json.loads(
            db.execute(
                "SELECT detail_json FROM audit_events WHERE subject_id=?",
                (requested["job"]["id"],),
            ).fetchone()[0]
        )
    assert state == ("complete", 1.0)
    assert comparison_count == 1
    assert track_models.sessions == [str(session["id"])]
    assert audit["track_model_status"] == "published"
    assert jobs.snapshot().failed == 0
