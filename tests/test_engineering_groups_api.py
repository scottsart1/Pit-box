from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from test_engineering_5_2 import lap

from pitwall.api.engineering import create_engineering_router
from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService, compare_runs, report_text
from pitwall.engineering_groups import build_groups


async def seed(stack):
    store, database, *_ = stack
    for n in range(1, 9):
        await database.save_lap(
            lap(n, 1 if n < 5 else 2, compound="MEDIUM" if n < 5 else "HARD"), []
        )
    service = EngineeringService(database)
    key = session_id(52001)
    app = FastAPI()
    app.include_router(create_engineering_router(service, store))
    return (
        database,
        service,
        key,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ),
    )


@pytest.mark.asyncio
async def test_groups_and_reported_notes_round_trip_without_changing_measurements(
    stack,
):
    database, _service, key, client = await seed(stack)
    url = f"/api/v1/sessions/{key}/engineering"
    with database._connect() as db:
        before = [
            tuple(row)
            for row in db.execute(
                "SELECT id,engineering_json,lap_time_ms,valid FROM recorded_laps ORDER BY id"
            )
        ]
    async with client:
        suggested = await client.post(url + "/groups/suggest", json={"count": 2})
        assert suggested.status_code == 200
        definitions = suggested.json()["groups"]
        definitions[0]["name"] = "Medium baseline"
        definitions[1]["name"] = "Hard experiment"
        saved = await client.put(url + "/groups", json={"groups": definitions})
        assert saved.status_code == 200
        report = saved.json()
        assert len(report["groups"]) == 2
        comparison = {
            "a": definitions[0]["id"],
            "b": definitions[1]["id"],
            "source": "groups",
            "mode": "stint",
        }
        descriptive = await client.post(url + "/compare", json=comparison)
        assert descriptive.status_code == 200
        assert descriptive.json()["median_delta_s"] is not None
        strict = await client.post(
            url + "/compare", json={**comparison, "mode": "setup"}
        )
        assert not strict.json()["enough_evidence"]
        selected = definitions[0]["lap_ids"][1]
        response = await client.patch(
            url + "/lap-notes",
            json={
                "lap_ids": [selected],
                "text": "Held up in sector two <not HTML>",
                "category": "traffic",
                "exclude_from_pace": True,
            },
        )
        assert response.status_code == 200
        annotated = response.json()
        note = annotated["lap_notes"][0]
        assert note["source"] == "driver"
        assert annotated["groups"][0]["summary"]["clean_lap_count"] == 3
        assert (
            next(item for item in annotated["laps"] if item["id"] == selected)[
                "context_notes"
            ][0]["text"]
            == note["text"]
        )
        text = (await client.get(url + "/export")).text
        assert "Medium baseline" in text and "Held up in sector two" in text
        assert "driver-reported traffic" in text and "excluded from pace" in text
        changed = await client.patch(
            url + "/lap-notes",
            json={
                "note_id": note["id"],
                "lap_ids": [selected],
                "text": "Traffic cleared before timed lap",
                "category": "traffic",
                "exclude_from_pace": False,
            },
        )
        assert changed.json()["groups"][0]["summary"]["clean_lap_count"] == 4
        deleted = await client.delete(url + "/lap-notes/" + note["id"])
        assert not deleted.json()["lap_notes"]
    reloaded = await EngineeringService(database).report(key)
    assert reloaded["groups"][0]["name"] == "Medium baseline"
    with database._connect() as db:
        after = [
            tuple(row)
            for row in db.execute(
                "SELECT id,engineering_json,lap_time_ms,valid FROM recorded_laps ORDER BY id"
            )
        ]
    assert before == after


@pytest.mark.asyncio
async def test_invalid_group_note_targets_and_bounds_are_rejected_atomically(stack):
    _, service, key, client = await seed(stack)
    report = await service.report(key)
    ids = [item["id"] for item in report["laps"]]
    group = {"id": "custom-a", "name": "A", "lap_ids": ids[:4]}
    url = f"/api/v1/sessions/{key}/engineering"
    async with client:
        for groups in (
            [{**group, "lap_ids": ["foreign"]}],
            [group, {**group, "id": "custom-b"}],
            [{**group, "lap_ids": []}],
            [{**group, "name": " "}],
            [{**group, "id": report["runs"][0]["id"]}],
        ):
            assert (
                await client.put(url + "/groups", json={"groups": groups})
            ).status_code == 422
        for count in (0, 1, 9, 13, 2.5, True):
            assert (
                await client.post(url + "/groups/suggest", json={"count": count})
            ).status_code == 422
        for note in (
            {"lap_ids": ["foreign"], "text": "wrong session"},
            {"lap_ids": [ids[0], ids[0]], "text": "duplicate"},
            {"lap_ids": [ids[0]], "text": " "},
            {"lap_ids": [ids[0]], "text": "x", "source": "measured"},
        ):
            assert (
                await client.patch(url + "/lap-notes", json=note)
            ).status_code == 422
        assert (await client.delete(url + "/lap-notes/missing")).status_code == 404
    report = await service.report(key)
    assert not report["groups"] and not report["lap_notes"]


@pytest.mark.asyncio
async def test_pending_note_binds_only_exact_attempt_and_survives_restart(stack):
    database, service, key, client = await seed(stack)
    await client.aclose()
    pending = await service.save_lap_note(
        key,
        [],
        "Lost time behind traffic this lap",
        "traffic",
        True,
        pending_laps=[{"lap_num": 9, "timeline_epoch": 0}],
    )
    assert "pending lap 9" in report_text(await service.report(key))
    await database.save_lap(lap(9, timeline_epoch=1), [])
    await database.save_lap(lap(9, restart_epoch=1), [])
    report = await service.report(key)
    assert not report["lap_notes"][0]["lap_ids"]
    assert not (await service.report(session_id(52001, 1)))["lap_notes"]
    await database.save_lap(lap(9), [])
    report = await EngineeringService(database).report(key)
    marked = [item for item in report["laps"] if item.get("context_notes")]
    assert len(marked) == 1 and marked[0]["timeline_epoch"] == 0
    assert marked[0]["context_notes"][0]["id"] == pending["id"]


@pytest.mark.asyncio
async def test_concurrent_notes_and_group_edits_preserve_each_other_and_new_laps(stack):
    database, service, key, client = await seed(stack)
    await client.aclose()
    ids = [item["id"] for item in (await service.report(key))["laps"]]
    await asyncio.gather(
        service.save_groups(key, [{"id": "custom-a", "name": "A", "lap_ids": ids[:4]}]),
        service.save_notes(key, None, "Test compounds", "Repeat tomorrow"),
        service.save_lap_note(key, [ids[0]], "Understeer in long corners", "balance"),
        service.save_lap_note(key, [ids[1]], "Felt confident", "other"),
    )
    await database.save_lap(lap(9), [])
    report = await service.report(key)
    assert len(report["laps"]) == 9
    assert len(report["groups"][0]["laps"]) == 4
    assert len(report["lap_notes"]) == 2
    assert report["notes"]["objective"] == "Test compounds"
    await service.save_notes(key, "custom-a", "Group objective", "Group conclusion")
    report = await service.report(key)
    assert "Group conclusion" in report_text(report)
    json.dumps(report, allow_nan=False)


@pytest.mark.asyncio
async def test_recorded_temperature_ranges_and_gap_evidence_are_frozen(stack):
    store, *_ = stack
    await store.mark_packet(2026, 26, 52001, packet_id=2)
    await store.update(air_temp_c=20, track_temp_c=30, speed_kph=180, current_lap=0)
    await store.transition_lap(1, 0, False, 2, 0, 0)

    def telemetry(state):
        state.drivers[0].delta_to_front_s = 0.9
        state.air_temp_c = 23
        state.track_temp_c = 35
        state.traces = [{"d": 0}, {"d": 5000}]

    await store.mutate(telemetry)
    completed = await store.transition_lap(2, 90000, False, 2, 0, 0)
    assert completed["air_temp_c_range"] == [20, 23]
    assert completed["track_temp_c_range"] == [30, 35]
    assert completed["traffic_evidence"]["min_gap_ahead_s"] == 0.9
    assert completed["traffic_evidence"]["close_following_observed"]


def test_strict_setup_comparison_rejects_mixed_setups_and_changing_temperatures():
    laps = [lap(n, 1 if n % 2 else 2) for n in range(1, 9)]
    groups = build_groups(
        laps,
        [
            {"id": "a", "name": "A", "lap_ids": [item["id"] for item in laps[:4]]},
            {"id": "b", "name": "B", "lap_ids": [item["id"] for item in laps[4:]]},
        ],
    )
    result = compare_runs(*groups)
    assert not result["enough_evidence"] and not result["same_setup"]
    assert "multiple_setups_in_group" in result["excluded"]["a"][0]["reasons"]
    for item in laps:
        item["setup"] = {"front_wing": 28}
    laps[0]["air_temp_c_range"] = [20, 26]
    groups = build_groups(
        laps,
        [
            {"id": group["id"], "name": group["name"], "lap_ids": group["lap_ids"]}
            for group in groups
        ],
    )
    result = compare_runs(*groups)
    assert any(
        "changing_air_temp_c" in item["reasons"] for item in result["excluded"]["a"]
    )
