"""Conditional track positions cannot become earnable points for bad plans."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("legal,feasible,valid,expected", [
    (False, False, False, 0),
    (True, False, False, None),
    (True, True, False, None),
    (True, True, True, 25),
])
async def test_championship_scores_only_supported_finishes(stack, legal, feasible, valid, expected):
    store, _, engine, *_ = stack
    await store.update(mode_profile="race", player_position=1, strategy={"plans": [{
        "legal": legal, "feasible": feasible, "finish_projection_valid": valid,
        "projected_finish_position": 1, "projected_rejoin_position": 8,
        "points_expected": 24.5, "stops_remaining": 1,
    }]})
    result = await engine.championship_scenario()
    row = result["plans"][0]
    assert row["projected_points"] == expected
    assert row["points_expected"] == (24.5 if valid else 0 if not legal else None)
    assert row["legal"] is legal and row["feasible"] is feasible
    assert row["finish_projection_valid"] is valid
    assert row["conditional_projected_points"] == 25
    assert row["conditional_points_expected"] == 24.5
    assert row["projection_caveat"] if not valid else not row["projection_caveat"]
    assert result["best_projected_points"] == expected
    assert result["has_supported_finish"] is valid
    assert "safe finish" not in result["note"].lower()


@pytest.mark.asyncio
async def test_illegal_podium_cannot_outrank_supported_lower_finish(stack):
    store, _, engine, *_ = stack
    await store.update(mode_profile="race", player_position=1, strategy={"plans": [
        {"legal": False, "feasible": False, "projected_finish_position": 1,
         "points_expected": 25, "stops_remaining": 0},
        {"legal": True, "feasible": True, "projected_finish_position": 5,
         "points_expected": 9.5, "stops_remaining": 1},
    ]})
    result = await engine.championship_scenario()
    assert result["best_projected_points"] == 10
    assert result["plans"][0]["conditional_projected_points"] == 25
    assert result["plans"][0]["projected_points"] == 0
    assert result["plans"][1]["projected_points"] == 10


@pytest.mark.asyncio
async def test_legacy_snapshot_without_validity_is_identified_as_unverified(stack):
    store, _, engine, *_ = stack
    await store.update(mode_profile="sprint", player_position=7, strategy={"plans": [
        {"projected_finish_position": 1, "projected_rejoin_position": 7,
         "points_expected": 8, "stops_remaining": 1},
    ]})
    result = await engine.championship_scenario()
    assert result["plans"][0]["conditional_projected_points"] == 8
    assert result["plans"][0]["projected_points"] is None
    assert result["plans"][0]["finish_projection_valid"] is False
    assert "not established" in result["plans"][0]["projection_caveat"]
    assert result["best_projected_points"] is None
    assert result["current_points_if_held"] == 2
    assert "legal" in result["current_points_basis"].lower()


@pytest.mark.asyncio
async def test_computed_illegal_finish_keeps_its_warning_in_championship_tool(stack):
    store, _, engine, _, _, tools = stack
    await store.update(
        session_uid=90913, mode_profile="race", session_type="Race",
        current_lap=15, total_laps=20, track_id=11, player_car_index=0,
        player_position=1, active_cars=20,
        tyre={"compound": "HARD", "age_laps": 14, "wear": [20] * 4},
        tyre_sets=[{"index": 0, "compound": "HARD", "available": True, "fitted": True}],
    )
    result = engine.compute(await store.snapshot_analysis())
    assert result["recommended"]["legal"] is False
    await store.update(strategy=result)
    scored = await tools.get_championship_scenario()
    assert scored["has_supported_finish"] is False
    assert scored["best_projected_points"] == 0
    assert all(row["projected_points"] == row["points_expected"] == 0
               for row in scored["plans"])
    assert all("illegal" in row["projection_caveat"] for row in scored["plans"])
