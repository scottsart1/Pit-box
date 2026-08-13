from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pitwall.comparison_service import ComparisonService
from pitwall.database import PitWallDatabase
from pitwall.field_service import FieldAnalysisService
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
    assert reopened["segments"][0]["metrics"]["segment_time_s"][
        "candidate"
    ] == pytest.approx(1.0, abs=0.02)

    field = await FieldAnalysisService(
        database.path,
        min_cars_per_segment=1,
    ).corners(result["candidate"]["session_id"])
    assert field["availability"] == "derived"
    assert field["n_by_segment"] == [1] * 10


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


@pytest.mark.asyncio
async def test_comparison_uses_exact_layout_active_segments_and_keeps_fallback(
    tmp_path: Path,
) -> None:
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)
    reference_id = await _record(database, archive, _lap(1, slower=False))
    candidate_id = await _record(database, archive, _lap(2, slower=True))
    service = ComparisonService(database.path, trace_store)

    fallback = await service.create_comparison(
        candidate_id,
        reference_kind="lap",
        reference_lap_id=reference_id,
    )
    assert fallback["analysis_model"] == {
        "track_model_id": None,
        "track_model_version": None,
        "segment_model_id": None,
        "segment_model_version": None,
        "model_quality": 0.65,
        "segment_source": "uniform_distance_v1",
        "fallback": True,
    }
    assert len(fallback["segments"]) == 10

    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database.path) as connection:
        layout = connection.execute(
            """
            SELECT s.track_layout_signature
            FROM recorded_laps l
            JOIN session_cars c ON c.id=l.session_car_id
            JOIN recorded_sessions s ON s.id=c.session_id
            WHERE l.id=?
            """,
            (candidate_id,),
        ).fetchone()[0]
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO track_models(
                id, track_id, layout_signature, model_version,
                algorithm_version, relative_path, length_m, quality_score,
                checksum, active, created_at
            ) VALUES ('track_active_test', 7, ?, 4, 'track_model_test_v4',
                      'track-models/test/model.pwm', 500.0, 0.92,
                      'track-checksum', 1, ?)
            """,
            (layout, now),
        )
        connection.execute(
            """
            INSERT INTO segment_models(
                id, track_model_id, version, source, checksum, active, created_at
            ) VALUES ('segments_active_test', 'track_active_test', 6,
                      'manual_test_v6', 'segment-checksum', 1, ?)
            """,
            (now,),
        )
        for ordinal, (key, label, start_m, end_m) in enumerate(
            (
                ("stable_entry", "Entry complex", -20.0, 180.0),
                ("stable_middle", "Middle complex", 180.0, 360.0),
                ("stable_exit", "Exit complex", 360.0, 520.0),
            )
        ):
            connection.execute(
                """
                INSERT INTO segments(
                    id, segment_model_id, ordinal, label, start_m, end_m,
                    phase_json, direction, confidence
                ) VALUES (?, 'segments_active_test', ?, ?, ?, ?, ?, NULL, 0.9)
                """,
                (
                    key,
                    ordinal,
                    label,
                    start_m,
                    end_m,
                    json.dumps({"source": "manual_test_v6"}),
                ),
            )

    active = await service.create_comparison(
        candidate_id,
        reference_kind="lap",
        reference_lap_id=reference_id,
    )
    assert active["comparison_id"] != fallback["comparison_id"]
    assert active["analysis_model"] == {
        "track_model_id": "track_active_test",
        "track_model_version": 4,
        "segment_model_id": "segments_active_test",
        "segment_model_version": 6,
        "model_quality": 0.92,
        "segment_source": "manual_test_v6",
        "fallback": False,
    }
    assert [item["segment_id"] for item in active["segments"]] == [
        "stable_entry",
        "stable_middle",
        "stable_exit",
    ]
    assert active["segments"][0]["start_m"] == 0.0
    assert active["segments"][-1]["end_m"] == 500.0
    assert active["segments"][0]["model_source"] == "manual_test_v6"
    assert active["quality_score"] == pytest.approx(0.92)
    with sqlite3.connect(database.path) as connection:
        model_ids = connection.execute(
            """
            SELECT track_model_id, segment_model_id FROM comparisons WHERE id=?
            """,
            (active["comparison_id"],),
        ).fetchone()
        assert model_ids == ("track_active_test", "segments_active_test")

    reopened = await service.get_comparison(active["comparison_id"])
    assert reopened["analysis_model"] == active["analysis_model"]
    assert reopened["segments"][0]["model_source"] == "manual_test_v6"

    # Historical rows can lack a layout signature. They remain comparable and
    # deliberately use the established distance-only fallback.
    with sqlite3.connect(database.path) as connection:
        connection.execute("UPDATE recorded_sessions SET track_layout_signature=NULL")
    legacy_fallback = await service.create_comparison(
        candidate_id,
        reference_kind="lap",
        reference_lap_id=reference_id,
    )
    assert legacy_fallback["analysis_model"]["fallback"] is True
    assert legacy_fallback["comparison_id"] == fallback["comparison_id"]
    assert len(legacy_fallback["segments"]) == 10


@pytest.mark.asyncio
async def test_a_lap_is_never_compared_against_itself(tmp_path: Path) -> None:
    """Self-comparison yields 0.000 s everywhere and looks like a perfect lap.

    A real session review reported "100% coverage", "zero seconds lost" AND
    "room for improvement" at once; one contributing path was that nothing
    stopped candidate == reference for direct reference kinds.
    """
    from pitwall.comparison_service import UnsupportedReferenceError

    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)
    lap_id = await _record(database, archive, _lap(1, slower=True))
    service = ComparisonService(database.path, trace_store)

    with pytest.raises(UnsupportedReferenceError, match="candidate itself"):
        await service.create_comparison(
            lap_id, reference_kind="lap", reference_lap_id=lap_id
        )


@pytest.mark.asyncio
async def test_unmeasurable_segment_loss_is_none_not_zero(tmp_path: Path) -> None:
    """From a real session: "Measured: 0.000 s" beside an improvement tip.

    A segment whose aligned time delta cannot be measured must report its
    loss as None (rendered "time attribution unavailable"), never as a
    confident 0.000 — while control-signal findings may legitimately remain.
    """
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    trace_store = TraceStore(tmp_path / "traces")
    archive = TraceArchiveService(database, trace_store)

    # The candidate's trace carries no usable time channel: every "t" is the
    # same value, so per-segment time deltas cannot be interpolated.
    broken = _lap(2, slower=True)
    for point in broken["trace"]:
        point["t"] = 0.0
    reference_id = await _record(database, archive, _lap(1, slower=False))
    candidate_id = await _record(database, archive, broken)
    service = ComparisonService(database.path, trace_store)

    result = await service.create_comparison(
        candidate_id,
        reference_kind="lap",
        reference_lap_id=reference_id,
    )
    losses = [finding.get("measured_loss_s") for finding in result["findings"]]
    assert all(loss is None or abs(loss) > 0.0005 for loss in losses), losses

    # And the persisted copy keeps None as NULL rather than storing 0.
    reopened = await ComparisonService(database.path, trace_store).get_comparison(
        result["comparison_id"]
    )
    stored = [finding.get("measured_loss_s") for finding in reopened["findings"]]
    assert stored == losses
