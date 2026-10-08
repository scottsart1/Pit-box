"""Saved strategy history follows material live changes within the same lap."""

from copy import deepcopy

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "later_stop", "later_compound", "driver_control", "restart", "rewind",
    "projection_tick",
])
async def test_recompute_records_material_changes_without_recording_every_tick(stack, monkeypatch, change):
    store, database, strategy, *_ = stack
    await store.update(session_uid=42, track_id=13, current_lap=5, total_laps=40,
                       mode_profile="race", tyre={"compound": "MEDIUM"})
    recommendation = {
        "box_laps": [12, 28], "compounds": ["MEDIUM", "HARD", "SOFT"],
        "stops_remaining": 2, "box_lap": 12, "fit_compound": "HARD",
        "projected_finish_position": 5, "risk_adjusted_time_s": 3000,
        "feasible": True, "legal": True,
    }

    async def evaluated(*_):
        return {"available": True, "recommended": deepcopy(recommendation)}, []

    # Supply evaluated race plans so this isolates publication and persisted
    # history from the solver's choice of a particular later stop.
    monkeypatch.setattr(strategy, "_compute_isolated", evaluated)
    original = await strategy.recompute()
    if change == "later_stop":
        recommendation["box_laps"][1] = 30
    elif change == "later_compound":
        recommendation["compounds"][2] = "MEDIUM"
    elif change == "driver_control":
        recommendation["driver_override"] = {"active": True, "honored": True}
    elif change == "restart":
        await store.update(restart_epoch=1)
    elif change == "rewind":
        await store.update(timeline_epoch=1)
    else:
        recommendation["risk_adjusted_time_s"] += 0.01
    latest = await strategy.recompute()
    assert strategy._radio_signature(latest["recommended"]) == strategy._radio_signature(original["recommended"])
    assert (await store.peek("strategy"))["strategy"] == latest

    history = await database.history_query(session_uid=42)
    if change == "projection_tick":
        assert len(history["strategies"]) == 1
    else:
        assert len(history["strategies"]) == 2
        assert history["strategies"][0]["recommended"] == latest["recommended"]

    # An unchanged follow-up keeps the same stored result.
    await strategy.recompute()
    repeated = await database.history_query(session_uid=42)
    assert len(repeated["strategies"]) == len(history["strategies"])
