import pytest


@pytest.mark.asyncio
async def test_setup_profiles_generate_conservative_changes(stack):
    store, _, _, setup, _, _ = stack
    await store.update(
        track_id=10,
        track_name="Spa",
        session_uid=99,
        car_setup={
            "front_wing": 20,
            "rear_wing": 18,
            "on_throttle": 55,
            "off_throttle": 50,
            "brake_pressure": 100,
            "brake_bias": 56,
            "front_left_tyre_pressure": 23.0,
            "front_right_tyre_pressure": 23.0,
            "rear_left_tyre_pressure": 21.0,
            "rear_right_tyre_pressure": 21.0,
        },
    )
    await store.mutate(
        lambda state: (
            setattr(state.tyre, "inner_temps_c", [105, 103, 95, 95]),
            state.feedback.append({"category": "understeer", "text": "no front grip"}),
        )
    )
    result = await setup.generate("race")
    assert result["available"] is True
    assert result["profile"] == "race"
    assert result["recommended"]["front_wing"] >= 20
    assert (
        result["pit_adjustment"]["next_front_wing"]
        == result["recommended"]["front_wing"]
    )


@pytest.mark.asyncio
async def test_foundational_setup_available_before_live_telemetry(stack):
    store, _, _, setup, _, _ = stack
    result = await setup.generate("race", track_id=10)
    assert result["available"] is True
    assert result["source"] == "foundational_pre_weekend"
    assert result["track"] == "Spa"
    assert len(result["recommended"]) >= 20
    assert "front_wing" in result["recommended"]
    assert "rear_right_tyre_pressure" in result["recommended"]
    assert result["pit_adjustment"]["available"] is False
