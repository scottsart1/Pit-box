from __future__ import annotations

import copy

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.engineering import create_engineering_router
from pitwall.catalog import session_id
from pitwall.engineering import (
    EngineeringService,
    build_runs,
    compare_runs,
    report_text,
    strategic_rivals,
)


def lap(n, run=1, **changes):
    result = {
        "id": f"lap-{run}-{n}",
        "session_uid": 52001,
        "restart_epoch": 0,
        "timeline_epoch": 0,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Practice",
        "mode_profile": "practice",
        "lap_num": n,
        "lap_time_ms": 90000 + (n % 4) * 100,
        "valid": True,
        "run_serial": run,
        "compound": "MEDIUM",
        "tyre_age_start": (n - 1) % 4 + 1,
        "tyre_age_end": (n - 1) % 4 + 2,
        "fuel_start_kg": 20 - (n - 1) % 4,
        "fuel_end_kg": 19 - (n - 1) % 4,
        "track_temp_c": 30,
        "air_temp_c": 22,
        "weather": "Clear",
        "pit_status": 0,
        "flag_context": 0,
        "setup": {
            "front_wing": 28 if run == 1 else 30,
            "rear_wing": 26,
            "fuel_load": 20,
        },
        "temps_end": [94, 95, 91, 92],
        "learning_exclusions": [],
        "context_observed": True,
        "traffic_observed": True,
        "trace_coverage": 0.98,
        "s1_ms": 30000,
        "s2_ms": 30000,
        "s3_ms": 30000 + (n % 4) * 100,
        "trace": [],
    }
    result.update(changes)
    return result


def runs():
    return build_runs(
        [lap(n) for n in range(1, 5)]
        + [lap(n, 2, lap_time_ms=89700 + n % 4 * 100, s2_ms=29700) for n in range(5, 9)]
    )


def test_matching_reports_unique_pairs_direction_sectors_and_evidence():
    a, b = runs()
    result = compare_runs(a, b)
    assert len(result["pairs"]) == 4
    assert result["enough_evidence"]
    assert result["median_delta_s"] == -0.3
    assert result["sector_deltas_s"] == [0, -0.3, 0]
    assert len({pair["a_lap_id"] for pair in result["pairs"]}) == 4
    assert len({pair["b_lap_id"] for pair in result["pairs"]}) == 4
    assert not result["same_setup"]
    assert b["setup_changes"] == {"front_wing": {"from": 28, "to": 30}}


@pytest.mark.asyncio
async def test_history_arriving_during_analysis_retains_official_sectors(
    stack, monkeypatch
):
    store, database, _, _, analysis, _ = stack
    item = lap(1)
    item.pop("id")
    for key in ("s1_ms", "s2_ms", "s3_ms"):
        item.pop(key)
    await store.update(session_uid=52001, completed_laps=[copy.deepcopy(item)])
    original = database.get_personal_best

    async def history_while_analysing(track):
        await store.merge_player_lap_history(
            [
                {
                    "lap_num": 1,
                    "s1_ms": 30000,
                    "s2_ms": 30000,
                    "s3_ms": 30100,
                    "valid_flags": 15,
                }
            ]
        )
        return await original(track)

    monkeypatch.setattr(database, "get_personal_best", history_while_analysing)
    await analysis.process_lap(item)
    report = await EngineeringService(database).report(session_id(52001))
    saved = report["runs"][0]["laps"][0]
    assert [saved[key] for key in ("s1_ms", "s2_ms", "s3_ms")] == [30000, 30000, 30100]


@pytest.mark.parametrize(
    "change",
    [
        {"compound": "HARD"},
        {"weather": "Rain"},
        {"fuel_start_kg": 40},
        {"tyre_age_start": 20},
        {"track_temp_c": 40},
        {"air_temp_c": 32},
        {"learning_exclusions": ["traffic"]},
        {"valid": False},
        {"pit_status": 1},
        {"flag_context": 1},
        {"context_observed": False},
        {"traffic_observed": False},
        {"trace_coverage": 0.4},
        {"fuel_start_kg": None},
        {"setup": {}},
    ],
)
def test_bad_or_unknown_context_cannot_produce_setup_evidence(change):
    a, b = runs()
    b["laps"] = [{**item, **change} for item in b["laps"]]
    result = compare_runs(a, b)
    assert not result["pairs"]
    assert not result["enough_evidence"]
    assert result["median_delta_s"] is None
    assert len(result["excluded"]["b"]) == 4


def test_one_quick_lap_is_not_a_verdict_and_same_setup_is_disclosed():
    a, b = runs()
    b["laps"] = b["laps"][:1]
    b["setup_id"] = a["setup_id"]
    result = compare_runs(a, b)
    assert len(result["pairs"]) == 1
    assert not result["enough_evidence"]
    assert result["same_setup"]


def test_run_boundaries_and_missing_temperature_are_honest():
    data = [
        lap(1),
        lap(2, learning_exclusions=["pit_lap"]),
        lap(3, 2, temps_end=[0, 0, 0, 0]),
        lap(4, 2, setup={"front_wing": 32}, temps_end=[]),
    ]
    groups = build_runs(data)
    assert len(groups) == 3
    assert groups[0]["summary"]["clean_lap_count"] == 1
    assert groups[1]["summary"]["tyre_inner_temp_range_c"] is None
    assert groups[2]["summary"]["observed_pace_trend_s_per_lap"] is None


def test_strategic_rivals_rank_projected_gaps_not_physical_neighbours():
    state = {
        "mode_profile": "race",
        "strategy": {
            "recommended": {"projected_time_s": 1000, "stops_remaining": 1},
            "rival_finish_projections": [
                {
                    "driver": "Neighbour",
                    "position": 4,
                    "finish_time_s": 1040,
                    "current_gap_s": 1,
                    "likely_remaining_stops": 2,
                },
                {
                    "driver": "Real rival",
                    "position": 9,
                    "finish_time_s": 1002,
                    "current_gap_s": 28,
                    "likely_remaining_stops": 0,
                },
                {
                    "driver": "Leader",
                    "position": 1,
                    "finish_time_s": 960,
                    "current_gap_s": -20,
                    "likely_remaining_stops": 1,
                },
            ],
        },
    }
    result = strategic_rivals(state)
    assert result["rivals"][0]["driver"] == "Real rival"
    assert result["rivals"][0]["projected_position"] == 3
    assert not strategic_rivals({"mode_profile": "practice"})["available"]


@pytest.mark.asyncio
async def test_saved_runs_notes_exports_and_restart_do_not_alias_legacy_rows(stack):
    store, database, *_ = stack
    for group in runs():
        for item in group["laps"]:
            await database.save_lap(item, [])
    service = EngineeringService(database)
    key = session_id(52001)
    report = await service.report(key)
    assert len(report["runs"]) == 2
    run_id = report["runs"][1]["id"]
    await service.save_notes(key, None, "Wing A/B", "Repeat with equal fuel")
    await service.save_notes(
        key, run_id, "Front wing 30", "Better turn-in <script>safe text</script>"
    )
    # The legacy table reuses (UID, lap), but canonical evidence must survive.
    await database.save_lap(
        lap(1, restart_epoch=1, lap_time_ms=110000, setup={"front_wing": 40}), []
    )
    report = await service.report(key)
    assert report["runs"][0]["laps"][0]["lap_time_ms"] == 90100
    assert report["runs"][1]["notes"]["objective"] == "Front wing 30"
    assert "Wing A/B" in report_text(report)
    assert "Better turn-in" in report_text(report)
    assert len((await service.report(session_id(52001, 1)))["runs"]) == 1
    app = FastAPI()
    app.include_router(create_engineering_router(service, store))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/sessions/{key}/engineering/export?format=text"
        )
        assert response.status_code == 200
        assert "attachment;" in response.headers["content-disposition"]
        assert "Front wing 30" in response.text
        assert (
            await client.get("/api/v1/sessions/missing/engineering")
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/sessions/{key}/engineering/compare",
                json={"a": run_id, "b": run_id},
            )
        ).status_code == 422
        assert (
            await client.patch(
                f"/api/v1/sessions/{key}/engineering/notes",
                json={"objective": "x" * 2001},
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/api/v1/sessions/{key}/engineering/compare",
                json={"a": "foreign", "b": run_id},
            )
        ).status_code == 404
        paired = await client.post(
            f"/api/v1/sessions/{key}/engineering/compare",
            json={"a": report["runs"][0]["id"], "b": run_id},
        )
        assert paired.status_code == 200 and paired.json()["enough_evidence"]


@pytest.mark.asyncio
async def test_old_recordings_do_not_invent_traffic_or_setup_context(stack):
    _, database, *_ = stack
    item = lap(1)
    item.pop("context_observed")
    item.pop("traffic_observed")
    await database.save_lap(item, [])
    report = await EngineeringService(database).report(session_id(52001))
    a = report["runs"][0]
    b = copy.deepcopy(a)
    result = compare_runs(a, b)
    assert result["pairs"] == []
    assert (
        "missing_condition_or_traffic_evidence" in result["excluded"]["a"][0]["reasons"]
    )
