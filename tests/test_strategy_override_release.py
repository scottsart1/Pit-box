"""Returning to automatic strategy must release a driver's previous pit call."""

import pytest

from pitwall.race_plan import normalise_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("whole_race_plan", [False, True])
async def test_clearing_driver_plan_restores_automatic_call_immediately(stack, whole_race_plan):
    store, database, strategy, *_ = stack
    await store.update(
        session_uid=42, session_type="Race", mode_profile="race",
        current_lap=14, total_laps=28, player_position=5, active_cars=20,
        track_id=13,
        tyre={"compound": "HARD", "age_laps": 12, "wear": [40] * 4},
        tyre_sets=[
            {"compound": "MEDIUM", "available": True},
            {"compound": "SOFT", "available": True},
        ],
    )
    automatic = await strategy.recompute()
    automatic_signature = strategy._radio_signature(automatic["recommended"])
    requested = next(
        plan for plan in strategy._candidate_pool
        if plan["feasible"] and plan["legal"] and plan["stops_remaining"] == 1
        and strategy._radio_signature(plan) != automatic_signature
    )
    requested_signature = strategy._radio_signature(requested)
    override = {
        "enabled": True, "locked": True,
        "next_box_lap": requested["box_laps"][0],
        "next_compound": requested["compounds"][1],
        "preferred_stops": 1,
    }
    if whole_race_plan:
        override["plan"] = normalise_plan({
            "compounds": requested["compounds"],
            "box_laps": requested["box_laps"],
            "lap_tolerance": 0,
        }, total_laps=28)
    await store.update(strategy_override=override)
    committed = await strategy.recompute()
    assert strategy._radio_signature(committed["recommended"]) == requested_signature
    assert committed["recommended"]["driver_override"]["honored"] is True

    # Dashboard clearing, radio cancellation and pre-race discard all disable
    # the override then recompute at this same lap, inside the radio hold window.
    await store.update(strategy_override={"enabled": False, "locked": False, "plan": {}})
    result = await strategy.recompute()
    assert strategy._radio_signature(result["recommended"]) == automatic_signature
    assert result["stability"]["held"] is False
    assert not result["recommended"].get("driver_override", {}).get("active")
    assert (await store.peek("strategy"))["strategy"] == result
    history = await database.history_query(session_uid=42)
    assert history["strategies"][0]["recommended"] == result["recommended"]
