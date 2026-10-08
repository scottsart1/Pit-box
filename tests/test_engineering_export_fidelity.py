from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from test_engineering_5_2 import lap

from pitwall.api.engineering import create_engineering_router
from pitwall.catalog import session_id
from pitwall.engineering import EngineeringService


@pytest.mark.asyncio
async def test_text_export_preserves_authored_text_and_formats_missing_metrics(stack):
    store, database, *_ = stack
    # Invalid laps still belong in the report, but cannot establish clean pace.
    for number in (1, 2):
        await database.save_lap(lap(number, valid=False, temps_end=[]), [])
    key = session_id(52001)
    service = EngineeringService(database)
    report = await service.report(key)
    await service.save_groups(
        key,
        [
            {
                "id": "manual-a",
                "name": "Nonetheless, baseline",
                "lap_ids": [item["id"] for item in report["laps"]],
            }
        ],
    )
    await service.save_notes(
        key, None, "None of the laps were clean", "Nonetheless, keep this session"
    )
    await service.save_notes(
        key, report["runs"][0]["id"], "None needed", "Nonetheless, stable"
    )
    await service.save_notes(
        key, "manual-a", "None of these are race laps", "Nonetheless, useful context"
    )
    await service.save_lap_note(
        key, [report["laps"][0]["id"]], "None left in reserve — Nonetheless, no spin"
    )

    app = FastAPI()
    app.include_router(create_engineering_router(service, store))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/sessions/{key}/engineering/export?format=text"
        )
        assert response.status_code == 200
        text = response.text
        for authored in (
            "Nonetheless, baseline",
            "None of the laps were clean",
            "Nonetheless, keep this session",
            "None needed",
            "Nonetheless, stable",
            "None of these are race laps",
            "Nonetheless, useful context",
            "None left in reserve — Nonetheless, no spin",
        ):
            assert authored in text
        assert "Median pace: Unavailable s; consistency SD: Unavailable s" in text
        assert "Observed pace trend: Unavailable s/lap" in text
        assert "Tyre inner temperatures: Unavailable C" in text
        assert "median pace: Unavailable s" in text

        exported = (
            await client.get(f"/api/v1/sessions/{key}/engineering/export?format=json")
        ).json()
        assert exported["runs"][0]["summary"]["median_pace_s"] is None
        assert (
            exported["lap_notes"][0]["text"]
            == "None left in reserve — Nonetheless, no spin"
        )
