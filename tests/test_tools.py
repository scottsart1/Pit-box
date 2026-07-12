import pytest

from pitwall.state import DriverState


@pytest.mark.asyncio
async def test_gap_ahead(stack):
    store, _, _, _, _, tools = stack

    def setup(state):
        state.player_position = 4
        state.active_cars = 2
        state.drivers[2] = DriverState(
            2,
            "Norris",
            active=True,
            position=3,
            gap_to_player_s=-2.8,
        )

    await store.mutate(setup)
    result = await tools.get_gap("ahead")
    assert result["available"] is True
    assert result["driver"] == "Norris"
    assert result["gap_s"] == 2.8


@pytest.mark.asyncio
async def test_player_lap_history_resolves_me(stack):
    store, _, _, _, _, tools = stack

    def setup(state):
        state.player_car_index = 3
        state.active_cars = 1
        state.drivers[3] = DriverState(
            3,
            "Sarthak",
            active=True,
            position=5,
            lap_history=[
                {
                    "lap_num": 1,
                    "lap_ms": 90000,
                    "s1_ms": 30000,
                    "s2_ms": 30000,
                    "s3_ms": 30000,
                    "valid_flags": 1,
                }
            ],
        )

    await store.mutate(setup)
    result = await tools.get_driver_lap_history("me", 3)
    assert result["available"] is True
    assert result["driver"] == "Sarthak"
    assert result["laps"][0]["lap"] == "1:30.000"


@pytest.mark.asyncio
async def test_all_strict_tool_schemas_require_every_property(stack):
    *_, tools = stack
    schemas = tools.schemas()
    assert len(schemas) >= 27
    for schema in schemas:
        parameters = schema["parameters"]
        assert schema["strict"] is True
        assert set(parameters["required"]) == set(parameters["properties"])
        assert parameters["additionalProperties"] is False


@pytest.mark.asyncio
async def test_qualifying_targets_rank_best_laps_not_race_gaps(stack):
    store, _, _, _, _, tools = stack

    def setup(state):
        state.mode_profile = "qualifying"
        state.player_car_index = 2
        state.drivers[0] = DriverState(
            0,
            "Norris",
            active=True,
            position=4,
            best_lap_ms=88500,
            gap_to_player_s=-12.0,
        )
        state.drivers[1] = DriverState(
            1,
            "Leclerc",
            active=True,
            position=1,
            best_lap_ms=88900,
            gap_to_player_s=-3.0,
        )
        state.drivers[2] = DriverState(
            2,
            "Sarthak",
            active=True,
            position=8,
            best_lap_ms=89700,
            gap_to_player_s=0.0,
        )
        state.analysis["target"] = {
            "target_ms": 88400,
            "target": "1:28.400",
            "theoretical_ms": 88400,
            "theoretical": "1:28.400",
            "basis": "session best / theoretical best",
        }

    await store.mutate(setup)
    result = await tools.get_qualifying_targets(3)

    assert [item["name"] for item in result["field"]] == [
        "Norris",
        "Leclerc",
        "Sarthak",
    ]
    assert result["session_best"] == "1:28.500"
    assert result["player_best"] == "1:29.700"
    assert result["target"] == "1:28.400"
    assert "gap" not in result["field"][0]
