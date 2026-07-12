import pytest


@pytest.mark.asyncio
async def test_strategy_ignores_expired_game_window_and_gives_tyre_and_lap(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 11
        state.total_laps = 22
        state.player_position = 10
        state.active_cars = 20
        state.track_id = 10
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 10
        state.tyre.wear = [39, 40, 74, 67]
        state.strategy = {
            "game_ideal_lap": 9,
            "game_latest_lap": 10,
            "game_rejoin_position": 22,
        }
        state.tyre_sets = [
            {"compound": "HARD", "available": True},
            {"compound": "SOFT", "available": True},
        ]

    await store.mutate(setup)
    result = await strategy.recompute()
    assert result["game_window"]["status"] == "expired"
    recommendation = result["recommended"]
    if recommendation["stops_remaining"]:
        assert recommendation["box_lap"] >= 11
        assert recommendation["fit_compound"] in {"HARD", "SOFT"}
        assert "Box lap" in recommendation["instruction"]


@pytest.mark.asyncio
async def test_qualifying_has_no_race_strategy(stack):
    store, _, strategy, _, _, _ = stack
    await store.update(
        session_type="Qualifying 1",
        mode_profile="qualifying",
        current_lap=2,
        total_laps=10,
    )
    result = await strategy.recompute()
    assert result["available"] is False


@pytest.mark.asyncio
async def test_live_rain_creates_explicit_inter_stop(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 8
        state.total_laps = 20
        state.player_position = 5
        state.active_cars = 20
        state.weather = "Light rain"
        state.rain_next_15_pct = 70
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 7
        state.tyre.wear = [30, 31, 28, 29]

    await store.mutate(setup)
    result = await strategy.recompute()
    recommendation = result["recommended"]
    assert recommendation["box_lap"] == 8
    assert recommendation["fit_compound"] == "INTER"
    assert "Box lap 8 for INTER" in recommendation["instruction"]


@pytest.mark.asyncio
async def test_dry_race_requires_two_distinct_compounds(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 3
        state.total_laps = 22
        state.track_id = 10
        state.track_name = "Spa"
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 2
        state.tyre.wear = [8, 8, 8, 8]
        state.tyre_sets = [
            {
                "compound": "SOFT",
                "available": True,
                "wear_pct": 0,
                "usable_life_laps": 10,
            },
            {
                "compound": "HARD",
                "available": True,
                "wear_pct": 0,
                "usable_life_laps": 25,
            },
        ]

    await store.mutate(setup)
    result = await strategy.recompute()
    assert result["compound_rule"]["applies"] is True
    assert result["compound_rule"]["compliant"] is True
    assert result["recommended"]["stops_remaining"] >= 1
    assert result["recommended"]["fit_compound"] in {"SOFT", "HARD"}
    assert all(plan["stops_remaining"] >= 1 for plan in result["plans"])


@pytest.mark.asyncio
async def test_wet_use_waives_dry_compound_requirement(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 8
        state.total_laps = 20
        state.track_id = 10
        state.weather = "Light rain"
        state.rain_next_15_pct = 75
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 7
        state.tyre.wear = [30, 30, 30, 30]

    await store.mutate(setup)
    result = await strategy.recompute()
    rule = result["recommended"]["compound_rule"]
    assert result["recommended"]["fit_compound"] == "INTER"
    assert rule["wet_waiver"] is True
    assert result["recommended"]["legal"] is True


@pytest.mark.asyncio
async def test_spa_high_personal_wear_rejects_long_soft_finish(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 3
        state.total_laps = 22
        state.track_id = 10
        state.track_name = "Spa"
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 2
        # Mirrors the user's early-race rear wear pattern closely enough to
        # establish a high personal wear factor before historical data exists.
        state.tyre.wear = [9, 10, 16, 15]
        state.tyre_sets = [
            {
                "compound": "SOFT",
                "available": True,
                "wear_pct": 0,
                "usable_life_laps": 10,
            },
            {
                "compound": "HARD",
                "available": True,
                "wear_pct": 0,
                "usable_life_laps": 25,
            },
        ]

    await store.mutate(setup)
    result = await strategy.recompute()
    assert result["personal_wear_model"]["source"] == "current_stint_level"
    assert result["personal_wear_model"]["factor"] > 1.4
    # A lap-12 soft stint would be roughly ten laps and should be rejected or
    # lose on risk-adjusted time under this wear profile.
    soft_lap_12 = [
        plan
        for plan in result["plans"]
        if plan.get("stops_remaining") == 1
        and plan.get("box_laps") == [12]
        and plan.get("compounds", [None, None])[1] == "SOFT"
    ]
    if soft_lap_12:
        assert soft_lap_12[0]["feasible"] is False
    assert result["recommended"].get("fit_compound") == "HARD"
    assert not (
        result["recommended"].get("box_lap") == 12
        and result["recommended"].get("fit_compound") == "SOFT"
    )
    assert result["model_summary"]["limiting_wear_per_lap_pct"] == 8.0
    assert result["confidence"] == "low"


@pytest.mark.asyncio
async def test_neutralisation_and_red_flag_pit_loss_models(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        state.session_type = "Race"
        state.mode_profile = "race"
        state.current_lap = 10
        state.total_laps = 22
        state.track_id = 10
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 9
        state.tyre.wear = [35, 35, 35, 35]

    await store.mutate(setup)
    green = await strategy.recompute()
    await store.update(safety_car="virtual", race_control_phase="vsc")
    vsc = await strategy.recompute()
    await store.update(safety_car="full", race_control_phase="safety_car")
    sc = await strategy.recompute()
    await store.update(
        safety_car="none",
        race_control_phase="red_flag",
        red_flag_active=True,
    )
    red = await strategy.recompute()

    assert (
        green["neutralisation"]["effective_pit_loss_s"]
        > vsc["neutralisation"]["effective_pit_loss_s"]
    )
    assert (
        vsc["neutralisation"]["effective_pit_loss_s"]
        > sc["neutralisation"]["effective_pit_loss_s"]
    )
    assert red["neutralisation"]["effective_pit_loss_s"] == 0
    assert red["neutralisation"]["red_flag_tyre_change"] is True
    assert "red flag" in red["recommended"]["instruction"].lower()
