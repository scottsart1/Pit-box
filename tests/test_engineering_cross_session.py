"""Saved A/B selections keep session ownership and fair-lap evidence."""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from fastapi import FastAPI
from test_engineering_5_2 import lap

from pitwall.api.engineering import create_engineering_router
from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService


async def seed(stack, *, track_a=10, track_b=10, changes_a=None, changes_b=None):
    store, database, *_ = stack
    for uid, track, run in ((61001, track_a, 1), (61002, track_b, 2)):
        for n in range(1, 5):
            item = lap(
                n,
                run,
                session_uid=uid,
                track_id=track,
                session_type="Practice 1" if run == 1 else "Practice 2",
            )
            if run == 2:
                item.update(lap_time_ms=item["lap_time_ms"] - 600, s2_ms=29400)
                item.update(changes_b or {})
            else:
                item.update(changes_a or {})
            await database.save_lap(item, [])
    service = EngineeringService(database)
    a, b = (
        await service.report(session_id(61001)),
        await service.report(session_id(61002)),
    )
    app = FastAPI()
    app.include_router(create_engineering_router(service, store))
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    return database, service, a, b, client


def request_body(report_a, report_b, **updates):
    return {
        "a": report_a["runs"][0]["id"],
        "b": report_b["runs"][0]["id"],
        "b_session_id": report_b["session_id"],
        **updates,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("track", [0, 10])
async def test_same_track_saved_sessions_match_unique_laps_and_identify_both_sources(
    stack, track
):
    _, _, a, b, client = await seed(stack, track_a=track, track_b=track)
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b),
        )
    assert response.status_code == 200
    result = response.json()
    assert result["cross_session"] and result["enough_evidence"]
    assert len(result["pairs"]) == 4
    assert result["median_delta_s"] == -0.6
    assert result["sector_deltas_s"] == [0, -0.6, 0]
    assert result["criteria"]["session_mode"] == "same recorded mode"
    for side, report in (("a", a), ("b", b)):
        selection = result["selections"][side]
        assert selection["session_id"] == report["session_id"]
        assert selection["track_id"] == track
        assert selection["session_type"] == report["session_type"]
        assert selection["mode_profile"] == "practice"
        assert selection["lap_ids"] == [item["id"] for item in report["laps"]]
        assert len({pair[f"{side}_lap_id"] for pair in result["pairs"]}) == 4
    assert set(result["selections"]["a"]["lap_ids"]).isdisjoint(
        result["selections"]["b"]["lap_ids"]
    )
    assert any("game version" in text for text in result["caveats"])


@pytest.mark.asyncio
async def test_each_sessions_notes_change_only_its_owned_lap_without_writing_telemetry(
    stack,
):
    database, service, a, b, client = await seed(stack)
    await service.save_lap_note(
        a["session_id"], [a["laps"][0]["id"]], "A was held up", "traffic", True
    )
    await service.save_lap_note(
        b["session_id"], [b["laps"][1]["id"]], "B felt stable", "balance", False
    )
    await service.save_notes(
        b["session_id"], b["runs"][0]["id"], "Test rear balance", "Repeat this setting"
    )
    with database._connect() as db:
        before = [
            tuple(row)
            for row in db.execute(
                "SELECT id,engineering_json,lap_time_ms,valid FROM recorded_laps ORDER BY id"
            )
        ]
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b),
        )
    result = response.json()
    assert response.status_code == 200 and len(result["pairs"]) == 3
    assert result["excluded"]["a"][0]["lap_id"] == a["laps"][0]["id"]
    assert "driver_reported_traffic" in result["excluded"]["a"][0]["reasons"]
    assert [item["text"] for item in result["notes"]["a"]] == ["A was held up"]
    assert [item["text"] for item in result["notes"]["b"]] == ["B felt stable"]
    assert result["selections"]["b"]["notes"]["objective"] == "Test rear balance"
    assert all(pair["a_lap_id"] != a["laps"][0]["id"] for pair in result["pairs"])
    unmatched_b = next(
        item for item in result["excluded"]["b"] if item["lap_id"] == b["laps"][0]["id"]
    )
    assert unmatched_b["reasons"] == ["no_unused_condition_match"]
    with database._connect() as db:
        after = [
            tuple(row)
            for row in db.execute(
                "SELECT id,engineering_json,lap_time_ms,valid FROM recorded_laps ORDER BY id"
            )
        ]
    assert before == after


@pytest.mark.asyncio
async def test_local_group_ids_can_repeat_in_different_sessions_and_mix_with_runs(
    stack,
):
    _, service, a, b, client = await seed(stack)
    for report in (a, b):
        await service.save_groups(
            report["session_id"],
            [
                {
                    "id": "baseline",
                    "name": "My baseline",
                    "lap_ids": [item["id"] for item in report["laps"]],
                }
            ],
        )
    url = f"/api/v1/sessions/{a['session_id']}/engineering/compare"
    async with client:
        repeated = await client.post(
            url, json=request_body(a, b, source="groups", a="baseline", b="baseline")
        )
        mixed = await client.post(
            url, json=request_body(a, b, b_source="groups", b="baseline")
        )
    assert repeated.status_code == mixed.status_code == 200
    assert repeated.json()["enough_evidence"]
    assert mixed.json()["selections"]["a"]["source"] == "runs"
    assert mixed.json()["selections"]["b"]["source"] == "groups"
    assert mixed.json()["selections"]["b"]["name"] == "My baseline"


@pytest.mark.asyncio
@pytest.mark.parametrize("track_a,track_b", [(10, 11), (-1, -1), (10, -1)])
async def test_cross_track_or_unknown_track_comparisons_are_rejected(
    stack, track_a, track_b
):
    _, _, a, b, client = await seed(stack, track_a=track_a, track_b=track_b)
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b),
        )
    assert response.status_code == 422
    assert "same known track" in response.json()["detail"]


@pytest.mark.asyncio
async def test_missing_sessions_foreign_selection_ids_and_overlapping_laps_are_rejected(
    stack,
):
    _, service, a, b, client = await seed(stack)
    await service.save_groups(
        a["session_id"],
        [
            {
                "id": "overlap",
                "name": "Overlapping selection",
                "lap_ids": [item["id"] for item in a["laps"]],
            }
        ],
    )
    url = f"/api/v1/sessions/{a['session_id']}/engineering/compare"
    async with client:
        for updates in (
            {"b_session_id": "missing"},
            {"a": b["runs"][0]["id"]},
            {"b": a["runs"][0]["id"]},
            {"b_source": "groups"},
        ):
            assert (
                await client.post(url, json=request_body(a, b, **updates))
            ).status_code == 404
        overlap = await client.post(
            url, json={"a": a["runs"][0]["id"], "b": "overlap", "b_source": "groups"}
        )
        assert overlap.status_code == 422 and "overlapping" in overlap.json()["detail"]
        assert (
            await client.post(url, json=request_body(a, b, b_source="laps"))
        ).status_code == 422
        assert (
            await client.post(url, json=request_body(a, b, b_session_id=""))
        ).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"compound": "HARD"},
        {"weather": "Light rain"},
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
        {"setup": {}},
        {"mode_profile": "race"},
        {"mode_profile": ""},
        {"track_temp_c_range": [30, 37]},
    ],
)
async def test_cross_session_setup_matching_retains_all_evidence_gates(stack, change):
    _, _, a, b, client = await seed(stack, changes_b=change)
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b),
        )
    assert response.status_code == 200
    result = response.json()
    assert not result["enough_evidence"] and not result["pairs"]
    assert result["median_delta_s"] is None
    assert len(result["excluded"]["b"]) == 4


@pytest.mark.asyncio
async def test_cross_session_stint_mode_allows_different_compounds_and_modes_with_caveats(
    stack,
):
    _, _, a, b, client = await seed(
        stack, changes_b={"compound": "HARD", "mode_profile": "race", "air_temp_c": 32}
    )
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b, mode="stint"),
        )
    result = response.json()
    assert response.status_code == 200 and result["enough_evidence"]
    assert result["median_delta_s"] == -0.6
    assert any(item["field"] == "compounds" for item in result["condition_differences"])
    assert result["session_modes"] == {"a": ["practice"], "b": ["race"]}
    assert any("Different session modes" in text for text in result["caveats"])
    assert any("does not establish" in text for text in result["caveats"])


@pytest.mark.asyncio
async def test_legacy_cross_session_laps_do_not_fabricate_setup_or_temperature_evidence(
    stack,
):
    database, service, a, b, client = await seed(stack)
    with database._connect() as db:
        db.execute(
            "UPDATE recorded_laps SET engineering_json='{}' WHERE id IN (?,?,?,?)",
            tuple(item["id"] for item in b["laps"]),
        )
    legacy = await service.report(b["session_id"])
    assert legacy["runs"][0]["setup_id"] == "unknown"
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, legacy),
        )
    result = response.json()
    assert response.status_code == 200 and not result["enough_evidence"]
    assert all(
        "missing_condition_or_traffic_evidence" in item["reasons"]
        for item in result["excluded"]["b"]
    )
    assert all("unknown_setup" in item["reasons"] for item in result["excluded"]["b"])
    assert result["conditions"]["b"]["air_temp_c"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.asyncio
async def test_existing_same_session_request_still_compares_two_nonoverlapping_runs(
    stack,
):
    database, _, a, _, client = await seed(stack)
    for n in range(5, 9):
        await database.save_lap(lap(n, 2, session_uid=61001), [])
    report = await EngineeringService(database).report(a["session_id"])
    before = copy.deepcopy(report)
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json={
                "a": report["runs"][0]["id"],
                "b": report["runs"][1]["id"],
            },
        )
    assert response.status_code == 200
    result = response.json()
    assert not result["cross_session"] and result["enough_evidence"]
    assert "session_mode" not in result["criteria"]
    assert report == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["setup", "stint"])
async def test_known_material_layout_length_difference_rejects_both_modes(stack, mode):
    _, _, a, b, client = await seed(
        stack,
        changes_a={"track_length_m": 7004, "packet_format": 2026},
        changes_b={"track_length_m": 7020, "packet_format": 2026},
    )
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b, mode=mode),
        )
    assert response.status_code == 422
    assert "more than 15 metres" in response.json()["detail"]


@pytest.mark.asyncio
async def test_length_tolerance_and_packet_protocol_difference_are_reported_without_inventing_game_identity(
    stack,
):
    _, _, a, b, client = await seed(
        stack,
        changes_a={"track_length_m": 7004, "packet_format": 2025},
        changes_b={"track_length_m": 7019, "packet_format": 2026},
    )
    assert a["track_length_m"] == 7004
    assert a["track_layout_signature"] == "f1:2025:10:7004"
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b),
        )
    assert response.status_code == 200
    result = response.json()
    assert result["enough_evidence"]
    assert result["selections"]["a"]["packet_format"] == 2025
    assert result["selections"]["b"]["track_length_range_m"] == [7019, 7019]
    compatibility = result["comparison_compatibility"]
    assert compatibility["track_length_tolerance_m"] == 15
    assert compatibility["track_length_status"] == "recorded_within_tolerance"
    assert compatibility["packet_format_status"] == "different"
    assert compatibility["game_version"] == compatibility["car_formula"] == "unverified"
    assert any(
        "Protocol format does not identify" in text for text in result["caveats"]
    )


@pytest.mark.asyncio
async def test_missing_layout_game_and_car_metadata_stays_explicitly_unverified(stack):
    _, _, a, b, client = await seed(stack)
    assert a["track_length_m"] is None and b["packet_format"] is None
    async with client:
        response = await client.post(
            f"/api/v1/sessions/{a['session_id']}/engineering/compare",
            json=request_body(a, b),
        )
    result = response.json()
    assert response.status_code == 200 and result["enough_evidence"]
    compatibility = result["comparison_compatibility"]
    assert (
        compatibility["track_length_status"]
        == compatibility["packet_format_status"]
        == "unavailable"
    )
    assert compatibility["car_performance"] == "unverified"
    assert result["selections"]["a"]["track_length_range_m"] is None
    assert any(
        "layout length could not be checked" in text for text in result["caveats"]
    )
    assert any("car formula" in text for text in result["caveats"])
