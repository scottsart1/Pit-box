"""Published setup provenance, bounded refinement and session isolation."""

from copy import deepcopy

import pytest

from pitwall.setup_advisor import SetupAdvisor
from pitwall.setup_model import foundational_setup, setup_effects
from pitwall.setup_reference import reference_for_track


async def live_context(store, **overrides):
    values = {
        "session_uid": 601,
        "track_id": 10,
        "track_name": "Spa",
        "connected": True,
        "game_presence": "receiving",
        "formula": 13,
        "packet_format": 2026,
        "game_year": 26,
        "packet_group_freshness": {"1": 1.0},
        "weather": "Clear",
    }
    values.update(overrides)
    await store.update(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["race", "quali", "hybrid"])
async def test_reference_preserves_source_despite_live_and_legacy_evidence(
    stack, monkeypatch, profile
):
    store, database, _, advisor, _, _ = stack
    await live_context(
        store,
        car_setup={"front_wing": 1, "on_throttle": 10},
        driver_preferences={"rotation": 3, "rear_stability": 3},
        feedback=[{"category": "understeer"}],
    )
    await database.save_setup_run(600, 10, "Spa", profile, {"front_wing": 0}, {}, 1)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Unmatched legacy evidence must not change a reference")

    monkeypatch.setattr(database, "best_setup_runs", forbidden)
    monkeypatch.setattr(database, "corner_rows_for_track", forbidden)
    monkeypatch.setattr(database, "tyre_history_model", forbidden)
    result = await advisor.generate(profile, 10, basis="reference")
    reference = reference_for_track(10, profile=profile)
    assert result["recommended"] == reference["setup"]
    assert result["baseline_reference"]["sources"] == reference["sources"]
    assert result["current"] == result["driver_preferences"] == {}
    assert result["changes"] == {}
    assert not result["pit_adjustment"]["available"]
    assert result["learning_samples"] == 0
    assert not result["learning_status"]["qualified"]


@pytest.mark.asyncio
async def test_rotation_qualifying_uses_published_qualifying_column(stack):
    _, _, _, advisor, _, _ = stack
    race = await advisor.generate(
        "race", 3, basis="reference", reference_style="rotation"
    )
    quali = await advisor.generate(
        "quali", 3, basis="reference", reference_style="rotation"
    )
    assert race["recommended"]["front_left_tyre_pressure"] == 26.5
    assert quali["recommended"]["front_left_tyre_pressure"] == 23
    assert quali["baseline_reference"]["source_profile"] == "quali"
    assert foundational_setup(3, "quali", style="rotation") == quali["recommended"]
    assert foundational_setup(3, "race") == foundational_setup(3, "quali")


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [70, 110, 150])
async def test_hot_cold_or_high_wear_does_not_invent_pressure_changes(
    stack, temperature
):
    store, _, _, advisor, _, _ = stack
    reference = reference_for_track(10)["setup"]
    await live_context(store, car_setup=reference)
    await store.mutate(
        lambda state: (
            setattr(state.tyre, "inner_temps_c", [temperature] * 4),
            setattr(state.tyre, "wear", [20.0] * 4),
            setattr(state.tyre, "age_laps", 2),
            setattr(state.tyre, "compound", "MEDIUM"),
        )
    )
    result = await advisor.generate("race", 10)
    assert result["recommended"] == reference
    assert result["input_normalizations"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition, weather", [("wet", "Clear"), ("auto", "Light rain")]
)
async def test_wet_without_published_reference_is_explicitly_unavailable(
    stack, condition, weather
):
    store, _, _, advisor, _, _ = stack
    await live_context(store, weather=weather)
    result = await advisor.generate("race", 10, basis="reference", conditions=condition)
    assert not result["available"]
    assert result["baseline_reference"]["setup"] == {}
    assert "wet" in result["reason"].lower()


@pytest.mark.asyncio
async def test_unknown_track_has_no_invented_archetype_setup(stack):
    _, _, _, advisor, _, _ = stack
    result = await advisor.generate("race", 999, basis="reference")
    assert not result["available"]
    assert foundational_setup(999, "race") == {}
    assert setup_effects({}, 999)["lap_time_delta_s"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("formula", [0, 2, 8])
async def test_observed_other_formula_cannot_receive_personalized_2026_setup(
    stack, formula
):
    store, _, _, advisor, _, _ = stack
    await live_context(store, formula=formula, car_setup={"front_wing": 1})
    result = await advisor.generate("race", 10)
    assert not result["available"]
    assert result["baseline_reference"]["applicability"] == "incompatible_formula"
    reference = await advisor.generate("race", 10, basis="reference")
    assert reference["available"]
    assert reference["current"] == {}
    assert reference["baseline_reference"]["applicability"] == "incompatible_formula"


@pytest.mark.asyncio
async def test_formula13_remains_supported_with_older_udp_header(stack):
    store, _, _, advisor, _, _ = stack
    await live_context(
        store, packet_format=2025, game_year=25, car_setup={"front_wing": 24}
    )
    result = await advisor.generate("race", 10)
    assert result["available"]
    assert result["current"]["front_wing"] == 24
    assert result["baseline_reference"]["applicability"] == "compatible_formula"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["formula", "weather", "session", "presence"])
async def test_uncertain_live_context_cannot_refine_stale_car_or_offer_pit_change(
    stack, missing
):
    store, _, _, advisor, _, _ = stack
    overrides = {
        "formula": {"packet_group_freshness": {}},
        "weather": {"weather": "Unknown"},
        "session": {"session_uid": 0},
        "presence": {"connected": False, "game_presence": "absent"},
    }[missing]
    await live_context(
        store,
        car_setup={"front_wing": 1},
        feedback=[{"category": "understeer"}],
        **overrides,
    )
    result = await advisor.generate("race", 10)
    assert result["current"] == {}
    assert result["recommended"] == result["foundational"]
    assert not result["pit_adjustment"]["available"]
    assert not result["baseline_reference"]["live_evidence_applicable"]


@pytest.mark.asyncio
async def test_unknown_axle_lock_does_not_move_bias_or_pressure(stack):
    store, _, _, advisor, _, _ = stack
    reference = reference_for_track(10)["setup"]
    await live_context(store, car_setup=reference)
    await store.mutate(
        lambda state: state.analysis.update(corner_metrics=[{"wheel_lock": True}])
    )
    result = await advisor.generate("hybrid", 10)
    assert result["recommended"]["brake_bias"] == reference["brake_bias"]
    assert result["recommended"]["brake_pressure"] == reference["brake_pressure"]
    assert any("Confirm the axle" in line for line in result["rationale"])


@pytest.mark.asyncio
async def test_personalized_values_fit_supported_controls_and_disclose_invalid_input(
    stack,
):
    store, _, _, advisor, _, _ = stack
    await live_context(
        store,
        car_setup={"on_throttle": 78, "rear_suspension_height": 35},
        driver_preferences={"rotation": 3},
    )
    result = await advisor.generate("race", 10)
    assert result["recommended"]["on_throttle"] % 5 == 0
    assert result["recommended"]["rear_suspension_height"] >= 40
    assert result["current"]["rear_suspension_height"] == 35
    assert result["input_normalizations"]["rear_suspension_height"] == {
        "from": 35,
        "to": 40,
    }
    assert SetupAdvisor._clamp("front_left_tyre_pressure", 20) == 22.5
    assert SetupAdvisor._clamp("rear_left_tyre_pressure", 29.5) == 26.5
    assert SetupAdvisor._clamp("front_camber", -3.45) in {-3.4, -3.5}


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["session", "track", "timeline"])
async def test_pending_recommendation_never_publishes_into_replaced_context(
    stack, monkeypatch, boundary
):
    store, database, _, advisor, _, _ = stack
    await live_context(store)
    save = database.save_setup_recommendation

    async def crossing(*args, **kwargs):
        await save(*args, **kwargs)
        update = (
            {"session_uid": 602}
            if boundary == "session"
            else {"track_id": 11}
            if boundary == "track"
            else {"timeline_epoch": 1}
        )
        await store.update(**update, setup_recommendation={"new_session": True})

    monkeypatch.setattr(database, "save_setup_recommendation", crossing)
    result = await advisor.generate("race", 10, basis="reference")
    assert not result["available"]
    assert (await store.snapshot_analysis())["setup_recommendation"] == {
        "new_session": True
    }


@pytest.mark.asyncio
async def test_legacy_learning_does_not_assign_mixed_laps_to_final_setup(
    stack, monkeypatch
):
    store, database, _, advisor, _, _ = stack
    await live_context(
        store,
        car_setup={"front_wing": 10},
        completed_laps=[
            {"valid": True, "lap_time_ms": 90000, "setup": {"front_wing": 30}},
            {
                "valid": True,
                "lap_time_ms": 95000,
                "setup": {"front_wing": 30},
                "learning_exclusions": ["driver_reported_traffic"],
            },
            {"valid": True, "lap_time_ms": 92000, "setup": {"front_wing": 10}},
        ],
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError(
            "Legacy session aggregation must not write more misleading runs"
        )

    monkeypatch.setattr(database, "save_setup_run", forbidden)
    assert not await advisor.learn_current_session()
    assert SetupAdvisor._profile_for_session("Time Trial") == "time_trial"


@pytest.mark.parametrize("track", [5, 10, 11, 999])
def test_uncalibrated_setup_effects_never_invent_numeric_gain_or_pressure_penalty(
    track,
):
    setup = {
        "front_wing": 0,
        "rear_wing": 0,
        "front_left_tyre_pressure": 29.5,
        "rear_left_tyre_pressure": 26.5,
    }
    original = deepcopy(setup)
    effects = setup_effects(setup, track)
    assert not effects["calibrated"]
    assert effects["lap_time_delta_s"] == 0
    assert effects["wheel_wear_multipliers"] == [1, 1, 1, 1]
    assert effects["source"] == "uncalibrated_neutral"
    assert setup == original
