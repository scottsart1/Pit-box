from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.api.field import create_field_router
from pitwall.catalog import lap_id, session_car_id, session_id
from pitwall.database import PitWallDatabase
from pitwall.field_service import ContextMask, FieldAnalysisService


async def _stored_field(path: Path) -> tuple[str, list[str], list[str]]:
    database = PitWallDatabase(path)
    await database.initialize()
    session_key = session_id(420042)
    car_keys = [session_car_id(session_key, index) for index in range(3)]
    lap_keys: list[str] = []
    with sqlite3.connect(path) as db:
        db.execute(
            """
            INSERT INTO recorded_sessions(
                id, game_session_uid, restart_epoch, track_id,
                track_layout_signature, session_type, started_at, ended_at,
                status, packet_format, capture_mode, created_at, updated_at
            ) VALUES (?, '420042', 0, 7, 'f1:2026:7:5300', 'Race',
                      '2026-08-05T12:00:00Z', '2026-08-05T13:00:00Z',
                      'complete', 2026, 'balanced',
                      '2026-08-05T12:00:00Z', '2026-08-05T13:00:00Z')
            """,
            (session_key,),
        )
        for index, car_key in enumerate(car_keys):
            db.execute(
                """
                INSERT INTO session_cars(
                    id, session_id, car_index, identity_revision,
                    display_name, anonymized_name, team_id, is_ai, is_player,
                    identity_confidence
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, 1.0)
                """,
                (
                    car_key,
                    session_key,
                    index,
                    f"Driver {index + 1}",
                    f"Driver {index + 1:02d}",
                    index,
                    0 if index == 0 else 1,
                    1 if index == 0 else 0,
                ),
            )
            for lap_number in range(1, 5):
                key = lap_id(car_key, lap_number)
                lap_keys.append(key)
                invalid = index == 2 and lap_number == 3
                pit = index == 1 and lap_number == 4
                compound = "SOFT" if lap_number < 3 else "MEDIUM"
                tyre_age = lap_number if lap_number < 3 else lap_number - 2
                db.execute(
                    """
                    INSERT INTO recorded_laps(
                        id, session_car_id, lap_number, timeline_epoch,
                        lap_time_ms, valid, tyre_compound, tyre_age_laps,
                        fuel_start_kg, fuel_end_kg, weather_class,
                        pit_context, flag_context, coverage_ratio,
                        quality_score, created_at
                    ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 'dry',
                              ?, 0, 0.99, 0.97, ?)
                    """,
                    (
                        key,
                        car_key,
                        lap_number,
                        90_000 + index * 1_000 + lap_number * 100,
                        0 if invalid else 1,
                        compound,
                        tyre_age,
                        40.0 - lap_number * 1.5,
                        38.5 - lap_number * 1.5,
                        1 if pit else 0,
                        f"2026-08-05T12:{lap_number:02d}:00Z",
                    ),
                )
        db.commit()
    return session_key, car_keys, lap_keys


@pytest.mark.asyncio
async def test_summary_never_invents_official_classification(tmp_path: Path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    session_key, _cars, _laps = await _stored_field(path)
    service = FieldAnalysisService(path)

    result = await service.summary(session_key)

    assert result["cars_observed"] == 3
    assert result["classification_availability"] == "unavailable"
    assert result["classification"][0]["position"]["value"] is None
    assert result["classification"][0]["position"]["availability"] == "unavailable"
    assert result["classification"][0]["best_lap_ms"]["availability"] == "derived"
    assert result["classification"][0]["best_lap_ms"]["n"] == 4


@pytest.mark.asyncio
async def test_pace_matrix_uses_context_masks_and_comparable_sample_n(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pitwall.sqlite3"
    session_key, _cars, _laps = await _stored_field(path)
    service = FieldAnalysisService(path)

    result = await service.pace(session_key)

    assert result["availability"] == "derived"
    assert result["n_by_lap"] == [3, 3, 2, 2]
    assert result["total_valid"] == 10
    invalid_cell = result["cells"][2][2]
    assert invalid_cell["context_mask"] & int(ContextMask.INVALID_LAP)
    assert invalid_cell["included"] is False
    pit_cell = result["cells"][1][3]
    assert pit_cell["context_mask"] & int(ContextMask.PIT_CONTEXT)
    assert pit_cell["raw_lap_time_s"] is not None
    assert pit_cell["lap_time_s"] is None


@pytest.mark.asyncio
async def test_stints_are_derived_only_from_observed_compound_sequences(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pitwall.sqlite3"
    session_key, cars, _laps = await _stored_field(path)
    service = FieldAnalysisService(path)

    result = await service.stints(session_key)

    player = next(item for item in result["drivers"] if item["car_id"] == cars[0])
    assert player["availability"] == "derived"
    assert [item["compound"] for item in player["stints"]] == ["SOFT", "MEDIUM"]
    assert player["stints"][0]["start_lap"] == 1
    assert player["stints"][1]["start_lap"] == 3
    assert (
        player["stints"][0]["pace_slope_s_per_lap"]["availability"]
        == "unavailable"
    )
    assert player["stints"][0]["fuel_context"]["value"] is None


@pytest.mark.asyncio
async def test_positions_report_per_car_gaps_instead_of_zeroes(tmp_path: Path) -> None:
    path = tmp_path / "pitwall.sqlite3"
    session_key, _cars, _laps = await _stored_field(path)
    service = FieldAnalysisService(path)

    result = await service.positions(session_key)

    assert result["availability"] == "unavailable"
    assert result["cars_with_data"] == 0
    assert all(item["points"] == [] for item in result["series"])
    assert all(item["availability"] == "unavailable" for item in result["series"])


@pytest.mark.asyncio
async def test_corner_matrix_uses_only_absolute_persisted_segment_times(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pitwall.sqlite3"
    session_key, cars, laps = await _stored_field(path)
    service = FieldAnalysisService(path)
    first_lap_by_car = [laps[0], laps[4], laps[8]]
    with sqlite3.connect(path) as db:
        for index, candidate_lap in enumerate(first_lap_by_car):
            comparison_id = f"cmp_field_{index}"
            db.execute(
                """
                INSERT INTO comparisons(
                    id, candidate_lap_id, reference_kind, reference_key,
                    compatibility_class, compatibility_json,
                    algorithm_bundle, input_hash, coverage_ratio,
                    quality_score, state, created_at
                ) VALUES (?, ?, 'lap', ?, 'strict', '{}', 'analysis_4.2.0',
                          ?, 0.99, 0.95, 'ready', ?)
                """,
                (
                    comparison_id,
                    candidate_lap,
                    first_lap_by_car[0],
                    f"hash-{index}",
                    f"2026-08-05T13:0{index}:00Z",
                ),
            )
            for ordinal, segment_id in enumerate(("turn_1", "turn_2")):
                db.execute(
                    """
                    INSERT INTO comparison_segment_results(
                        comparison_id, ordinal, segment_key, label,
                        start_m, end_m, delta_s, coverage_ratio, metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 0.1, 0.99, ?)
                    """,
                    (
                        comparison_id,
                        ordinal,
                        segment_id,
                        f"Turn {ordinal + 1}",
                        ordinal * 100.0,
                        (ordinal + 1) * 100.0,
                        json.dumps(
                            {
                                "segment_time_s": {
                                    "candidate": 10.0 + ordinal * 5.0 + index * 0.5,
                                    "unit": "s",
                                }
                            }
                        ),
                    ),
                )
        db.commit()

    result = await service.corners(session_key)

    assert result["availability"] == "derived"
    assert [driver["car_id"] for driver in result["drivers"]] == cars
    assert result["n_by_segment"] == [3, 3]
    assert result["sample_count"] == [[1, 1], [1, 1], [1, 1]]
    assert result["rank"][0] == [1.0, 1.0]
    driver = await service.driver(session_key, cars[0])
    assert driver["strengths"]["availability"] == "derived"
    assert driver["strengths"]["n"] == 2
    assert driver["strengths"]["items"][0]["field_n"] == 3


@pytest.mark.asyncio
async def test_relative_only_corner_results_remain_explicitly_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pitwall.sqlite3"
    session_key, _cars, laps = await _stored_field(path)
    service = FieldAnalysisService(path)
    with sqlite3.connect(path) as db:
        db.execute(
            """
            INSERT INTO comparisons(
                id, candidate_lap_id, reference_kind, reference_key,
                compatibility_class, compatibility_json, algorithm_bundle,
                input_hash, coverage_ratio, quality_score, state, created_at
            ) VALUES ('cmp_relative', ?, 'lap', ?, 'strict', '{}',
                      'analysis_4.2.0', 'relative-hash', 1, 1, 'ready',
                      '2026-08-05T13:00:00Z')
            """,
            (laps[0], laps[4]),
        )
        db.execute(
            """
            INSERT INTO comparison_segment_results(
                comparison_id, ordinal, segment_key, label, start_m, end_m,
                delta_s, coverage_ratio, metrics_json
            ) VALUES ('cmp_relative', 0, 'turn_1', 'Turn 1', 0, 100,
                      0.2, 1, '{"brake_onset_m":{"candidate":90}}')
            """
        )
        db.commit()

    result = await service.corners(session_key)

    assert result["availability"] == "unavailable"
    assert result["median_time_s"] == [[None]]
    assert "Relative deltas" in result["reason"]


def test_field_router_validates_versioned_responses_and_not_found(tmp_path: Path) -> None:
    path = tmp_path / "pitwall.sqlite3"

    async def arrange() -> tuple[str, list[str]]:
        key, cars, _laps = await _stored_field(path)
        return key, cars

    import asyncio

    session_key, cars = asyncio.run(arrange())
    app = FastAPI()
    app.include_router(create_field_router(FieldAnalysisService(path)))
    client = TestClient(app)

    pace = client.get(f"/api/v1/sessions/{session_key}/field/pace")
    assert pace.status_code == 200
    assert pace.json()["schema_version"] == 1
    driver = client.get(
        f"/api/v1/sessions/{session_key}/field/drivers/{cars[0]}"
    )
    assert driver.status_code == 200
    assert driver.json()["driver"]["car_id"] == cars[0]
    missing = client.get("/api/v1/sessions/missing/field")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"
