from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.trace_archive import TraceArchiveService
from pitwall.trace_store import TraceStore


def _lap(lap_number: int, *, slower: bool) -> dict[str, object]:
    trace = []
    for distance in range(501):
        reference_braking = 225 <= distance <= 245
        candidate_braking = 210 <= distance <= 230
        braking = candidate_braking if slower else reference_braking
        brake_start = 210 if slower else 225
        speed = 70.0 - max(0, min(distance - brake_start, 35)) * 0.9
        if distance > brake_start + 35:
            speed = min(70.0, speed + (distance - brake_start - 35) * 0.8)
        penalty = 0.0
        if slower and distance > 210:
            penalty = min(0.18, (distance - 210) / 40 * 0.18)
        trace.append(
            {
                "d": float(distance),
                "t": distance / 50.0 + penalty,
                "speed": speed * 3.6,
                "brake": 0.75 if braking else 0.0,
                "throttle": 0.0 if braking else (0.3 if distance < 255 else 1.0),
                "steer": 0.35 if 230 <= distance <= 265 else 0.0,
                "gear": 5 if distance < 260 else 6,
                "x": float(distance),
                "z": 2.0 if slower else 0.0,
            }
        )
    return {
        "session_uid": 4_242,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "player_car_index": 3,
        "packet_format": 2026,
        "track_id": 7,
        "track_name": "Comparison Circuit",
        "track_length_m": 500,
        "session_type": "Time Trial",
        "mode_profile": "time_trial",
        "lap_num": lap_number,
        "lap_time_ms": 10_180 if slower else 10_000,
        "valid": True,
        "compound": "SOFT",
        "tyre_age_end": 1,
        "weather": "Clear",
        "trace": trace,
    }


async def _record(
    database: PitWallDatabase,
    archive: TraceArchiveService,
    lap: dict[str, object],
) -> str:
    await database.upsert_session(lap)
    lap_id = await database.save_lap(lap, [])
    assert lap_id is not None
    archived = await archive.archive_player_lap(lap, recorded_lap_id=lap_id)
    assert archived.state == "ready"
    return lap_id


@pytest.mark.asyncio
async def test_comparison_is_distance_aligned_persisted_and_reopenable(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)
    reference_id = await _record(database, archive, _lap(1, slower=False))
    candidate_id = await _record(database, archive, _lap(2, slower=True))
    service = ComparisonService(database.path, trace_store)

    result = await service.create_comparison(
        candidate_id,
        reference_kind="lap",
        reference_lap_id=reference_id,
    )
    assert result["compatibility"]["class"] == "strict"
    assert result["lap_delta_s"] == pytest.approx(0.18, abs=0.01)
    assert len(result["segments"]) == 10
    assert sum(item["delta_s"] or 0.0 for item in result["segments"]) == pytest.approx(
        result["lap_delta_s"], abs=0.003
    )

    reopened = await ComparisonService(database.path, trace_store).get_comparison(
        result["comparison_id"]
    )
    assert reopened["candidate"]["lap_id"] == candidate_id
    assert reopened["reference"]["lap_id"] == reference_id
    assert reopened["segments"][4]["metrics"]["brake_onset_m"][
        "candidate"
    ] == pytest.approx(210.0, abs=0.6)


@pytest.mark.asyncio
async def test_trace_and_reference_projections_are_bounded_and_explicit(tmp_path: Path) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)
    reference_id = await _record(database, archive, _lap(1, slower=False))
    candidate_id = await _record(database, archive, _lap(2, slower=True))
    service = ComparisonService(database.path, trace_store)

    references = await service.list_references(candidate_id)
    assert references["items"][0]["lap_id"] == reference_id
    assert references["items"][0]["suggested"] is True
    trace = await service.get_lap_trace(
        candidate_id,
        fields=["speed", "brake", "not_supplied"],
        start_m=190,
        end_m=270,
        max_points=40,
    )
    assert len(trace["axis"]["values"]) <= 40
    assert trace["series"]["speed"]["unit"] == "m/s"
    assert trace["series"]["not_supplied"]["availability"] == "unavailable"
    assert trace["downsample"]["source_points"] == 81

    comparison = await service.create_comparison(
        candidate_id,
        reference_kind="lap",
        reference_lap_id=reference_id,
    )
    aligned = await service.get_comparison_trace(
        comparison["comparison_id"],
        fields=["speed", "delta", "brake"],
        start_m=190,
        end_m=270,
        max_points=50,
    )
    assert len(aligned["axis"]["values"]) <= 50
    assert aligned["candidate"]["series"]["delta"]["availability"] == "derived"
    assert aligned["sign_convention"].startswith("positive")
