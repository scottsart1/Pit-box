"""What-if stops given as values, not only as an English sentence.

The Strategy workspace's lap and tyre selectors travel as ``lap`` and
``compound`` beside the scenario text. The engine must use them as given, so a
scenario written in any language (or none) still prices the stop that was
chosen; without them it keeps reading the English sentence.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import pitwall.app as app_module


def _race_state() -> dict:
    laps = [
        {"session_uid": 1, "restart_epoch": 0, "timeline_epoch": 0,
         "lap_num": age, "lap_time_ms": round((90 + .08 * age) * 1000),
         "valid": True, "compound": "MEDIUM", "tyre_age_start": age - 1,
         "tyre_age_end": age, "wear_start": [age - 1.] * 4, "wear_end": [float(age)] * 4,
         "fuel_start_kg": 20., "fuel_end_kg": 20., "weather": "Clear", "setup": {}, "pit_status": 0}
        for age in range(2, 11)
    ]
    return {"session_uid": 1, "restart_epoch": 0, "timeline_epoch": 0,
            "current_lap": 11, "total_laps": 20, "mode_profile": "race", "session_type": "Race",
            "track_id": 11, "weather": "Clear", "race_control_phase": "green", "safety_car": "none",
            "player_car_index": 0, "player_position": 1, "active_cars": 1,
            "tyre": {"compound": "MEDIUM", "age_laps": 10, "wear": [10.] * 4},
            "car_setup": {}, "completed_laps": laps, "penalties_s": 0,
            # No soft set left, so the recommended tyre (the text fallback) is not SOFT.
            "tyre_sets": [{"compound": "HARD", "available": True},
                          {"compound": "MEDIUM", "available": True},
                          {"compound": "SOFT", "available": False}]}


@pytest.mark.asyncio
async def test_explicit_lap_and_compound_are_used_when_the_text_names_neither(stack):
    store, _, engine, *_ = stack
    await store.update(**_race_state())
    unparsed = await engine.what_if("boxen")
    # The text alone names no tyre and no lap: the next lap and the
    # recommended tyre, as before.
    assert unparsed["available"] is True
    assert (unparsed["box_lap"], unparsed["compound"]) == (12, "HARD")

    chosen = await engine.what_if("boxen", lap=14, compound="SOFT")
    assert chosen["available"] is True
    assert chosen["scenario"] == "Box lap 14 for SOFT"
    assert (chosen["box_lap"], chosen["compound"]) == (14, "SOFT")

    # The same stop written as English text prices identically.
    english = await engine.what_if("box lap 14 for softs")
    assert english["scenario"] == chosen["scenario"]
    for key in ("projected_time_s", "risk_adjusted_time_s", "delta_to_best_s",
                "projected_finish_wear_pct", "feasible"):
        assert chosen[key] == english[key], key


@pytest.mark.asyncio
async def test_explicit_values_take_precedence_over_the_text(stack):
    store, _, engine, *_ = stack
    await store.update(**_race_state())
    result = await engine.what_if("box lap 16 for hards", lap=13, compound="soft")
    assert (result["box_lap"], result["compound"]) == (13, "SOFT")
    assert result["scenario"] == "Box lap 13 for SOFT"
    # A lap that has already been raced still clamps to the current lap.
    assert (await engine.what_if("boxen", lap=3, compound="SOFT"))["box_lap"] == 11


def test_endpoint_passes_the_selector_values_through(monkeypatch):
    calls = []

    async def fake_what_if(scenario, *, lap=None, compound=None):
        calls.append((scenario, lap, compound))
        return {"available": False, "reason": "recorded"}

    monkeypatch.setattr(app_module.strategy, "what_if", fake_what_if)
    client = TestClient(app_module.app)
    assert client.post("/api/strategy/what-if",
                       json={"scenario": "boxen", "lap": 18, "compound": "soft"}).status_code == 200
    assert client.post("/api/strategy/what-if", json={"scenario": "box lap 18 for hards"}).status_code == 200
    assert client.post("/api/strategy/what-if",
                       json={"scenario": "boxen", "lap": None, "compound": ""}).status_code == 200
    assert calls == [("boxen", 18, "SOFT"), ("box lap 18 for hards", None, None), ("boxen", None, None)]
    assert client.post("/api/strategy/what-if",
                       json={"scenario": "boxen", "compound": "CONCRETE"}).status_code == 400
    assert client.post("/api/strategy/what-if",
                       json={"scenario": "boxen", "lap": 500}).status_code == 400
    assert client.post("/api/strategy/what-if", json={"scenario": "  ", "lap": 18}).status_code == 400
    assert len(calls) == 3
