"""Deleting one restart must not erase history still owned by another."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.sessions import create_sessions_router
from pitwall.database import PitWallDatabase


UID = (1 << 64) - 17


async def seed(database, restarts=(0, 1)):
    state = {
        "session_uid": UID,
        "player_car_index": 0,
        "track_id": 12,
        "track_name": "Singapore",
        "session_type": "Race",
        "mode_profile": "race",
        "packet_format": 2026,
        "current_lap": 1,
    }
    await database.upsert_session(state)
    keys = []
    for restart in restarts:
        context = {**state, "restart_epoch": restart}
        key = await database.catalog.upsert_live_session(context)
        await database.save_lap(
            {**context, "lap_num": 1, "lap_time_ms": 90_000 + restart,
             "valid": True, "compound": "MEDIUM"},
            [],
        )
        await database.save_radio_message(
            context, "assistant", f"Synthetic restart {restart} radio"
        )
        await database.catalog.finalize_session(key)
        keys.append(key)
    return state, keys


def app_for(database):
    app = FastAPI()
    app.include_router(create_sessions_router(database.catalog))
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("first_restart", [0, 1])
async def test_shared_history_survives_either_first_deletion_then_last_owner_cleans_up(
    tmp_path, first_restart
):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    _, keys = await seed(database)
    first, retained = keys[first_restart], keys[1 - first_restart]
    original_history = await database.history_query(session_uid=UID)
    retained_laps = await database.catalog.list_laps(retained)
    assert len(original_history["laps"]) == 1
    assert len(original_history["radio"]) == 2
    assert len(retained_laps) == 1

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_for(database)), base_url="http://test"
    ) as client:
        preview_response = await client.delete(f"/api/v1/sessions/{first}")
        assert preview_response.status_code == 200
        preview = preview_response.json()
        impact = preview["impact"]
        assert impact["records"]["laps"] == 1
        assert impact["records"]["legacy"]["laps"] == 0
        assert impact["records"]["legacy"]["radio_messages"] == 0
        assert impact["retained_shared_legacy_tables"]["radio_messages"] == 2
        assert impact["retained_shared_legacy_session_ids"] == [retained]
        deleted = await client.delete(
            f"/api/v1/sessions/{first}",
            headers={"X-Pitwall-Delete-Token": preview["confirmation_token"]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["records"]["legacy"]["radio_messages"] == 0
        assert await database.catalog.get_session(first) is None
        assert await database.catalog.get_session(retained) is not None
        assert await database.catalog.list_laps(retained) == retained_laps
        assert await database.history_query(session_uid=UID) == original_history

        last_preview = (await client.delete(f"/api/v1/sessions/{retained}")).json()
        last_impact = last_preview["impact"]
        assert last_impact["records"]["legacy"]["laps"] == 1
        assert last_impact["records"]["legacy"]["radio_messages"] == 2
        assert last_impact["retained_shared_legacy_session_ids"] == []
        deleted = await client.delete(
            f"/api/v1/sessions/{retained}",
            headers={"X-Pitwall-Delete-Token": last_preview["confirmation_token"]},
        )
        assert deleted.status_code == 200
        assert deleted.json()["records"]["legacy"]["radio_messages"] == 2
        assert await database.catalog.get_session(retained) is None
        assert all(not rows for rows in (await database.history_query(session_uid=UID)).values())


@pytest.mark.asyncio
async def test_new_sibling_invalidates_deletion_preview_before_shared_rows_can_be_removed(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    state, [first] = await seed(database, restarts=(0,))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_for(database)), base_url="http://test"
    ) as client:
        preview = (await client.delete(f"/api/v1/sessions/{first}")).json()
        assert preview["impact"]["records"]["legacy"]["radio_messages"] == 1
        sibling = await database.catalog.upsert_live_session({**state, "restart_epoch": 1})
        response = await client.delete(
            f"/api/v1/sessions/{first}",
            headers={"X-Pitwall-Delete-Token": preview["confirmation_token"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "delete_preview_invalid"
        assert await database.catalog.get_session(first) is not None
        assert (await database.catalog.get_session(sibling))["status"] == "recording"
        assert len((await database.history_query(session_uid=UID))["radio"]) == 1
        fresh = (await client.delete(f"/api/v1/sessions/{first}")).json()
        assert fresh["impact"]["records"]["legacy"]["radio_messages"] == 0
        assert fresh["impact"]["retained_shared_legacy_session_ids"] == [sibling]


@pytest.mark.asyncio
async def test_removed_sibling_invalidates_shared_preview_before_cleanup_scope_expands(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    _, [first, sibling] = await seed(database)
    catalog = database.catalog
    old_preview = await catalog.preview_delete(first)
    sibling_preview = await catalog.preview_delete(sibling)
    await catalog.delete_session(sibling, sibling_preview["confirmation_token"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_for(database)), base_url="http://test"
    ) as client:
        response = await client.delete(
            f"/api/v1/sessions/{first}",
            headers={"X-Pitwall-Delete-Token": old_preview["confirmation_token"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "delete_preview_invalid"
        assert await catalog.get_session(first) is not None
        assert len((await database.history_query(session_uid=UID))["radio"]) == 2
