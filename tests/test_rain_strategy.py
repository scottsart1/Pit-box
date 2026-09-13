"""Wet calls end to end, through the strategy engine and the radio.

The unit tests in ``test_rain_model.py`` pin the model. These pin what the
driver actually hears, which is the only thing that decides a race.
"""

from __future__ import annotations

import pytest

from pitwall.state import DriverState


def _wet_race(state, **overrides) -> None:
    state.session_type = "Race"
    state.mode_profile = "race"
    state.track_id = overrides.get("track_id", 13)
    state.current_lap = overrides["current_lap"]
    state.total_laps = overrides["total_laps"]
    state.player_position = overrides.get("position", 8)
    state.active_cars = overrides.get("active_cars", 20)
    state.player_car_index = 0
    state.weather = overrides.get("weather", "Clear")
    state.rain_now_pct = overrides.get("rain_now_pct", 0)
    state.rain_next_15_pct = overrides.get("rain_next_15_pct", 0)
    state.weather_forecast = overrides.get("forecast", [])
    state.forecast_accuracy = overrides.get("forecast_accuracy", 0)
    state.track_temp_c = overrides.get("track_temp_c", 30)
    state.tyre.compound = overrides.get("compound", "MEDIUM")
    state.tyre.age_laps = overrides.get("age", 10)
    state.tyre.wear = overrides.get("wear", [30.0, 30.0, 30.0, 30.0])
    state.tyre_sets = overrides.get(
        "sets",
        [
            {"compound": "MEDIUM", "available": True},
            {"compound": "HARD", "available": True},
            {"compound": "INTER", "available": True},
            {"compound": "WET", "available": True},
        ],
    )
    state.completed_laps = overrides.get("laps", [])
    state.drivers = overrides.get("drivers", [])


def _player_lap(num, ms, *, weather, rain_pct, sectors=None):
    third = ms // 3
    return {
        "lap_num": num,
        "lap_time_ms": ms,
        "valid": True,
        "pit_status": 0,
        "weather": weather,
        "rain_pct": rain_pct,
        "compound": "MEDIUM",
        "s1_ms": (sectors or (third, third, ms - 2 * third))[0],
        "s2_ms": (sectors or (third, third, ms - 2 * third))[1],
        "s3_ms": (sectors or (third, third, ms - 2 * third))[2],
    }


def _rival(car_idx, compound, dry_ms, recent_ms, *, dry_laps=5, current_lap=20):
    """A car with a dry benchmark and a current lap on a named compound."""
    driver = DriverState(car_idx=car_idx, name=f"CAR{car_idx}")
    driver.active = True
    driver.position = car_idx
    driver.current_lap = current_lap
    driver.result_label = "active"
    driver.tyre_compound = compound
    history = [
        {"lap_num": n, "lap_ms": dry_ms, "s1_ms": 0, "s2_ms": 0, "s3_ms": 0, "valid_flags": 1}
        for n in range(1, dry_laps + 1)
    ]
    history.append(
        {
            "lap_num": current_lap,
            "lap_ms": recent_ms,
            "s1_ms": 0,
            "s2_ms": 0,
            "s3_ms": 0,
            "valid_flags": 1,
        }
    )
    driver.lap_history = history
    driver.tyre_stints = [
        {"start_lap": 1, "end_lap": dry_laps, "compound": "MEDIUM", "actual_compound": 0},
        {
            "start_lap": dry_laps + 1,
            "end_lap": current_lap + 20,
            "compound": compound,
            "actual_compound": 0,
        },
    ]
    return driver


@pytest.mark.asyncio
async def test_the_field_on_wets_going_quicker_is_what_calls_the_stop(stack):
    """The measured crossover, not the forecast, decides it.

    Six cars have switched to intermediates and are lapping four seconds
    quicker than the cars still on slicks. That is the crossover happening in
    front of the pit wall, and it has to reach the driver even though the
    forecast is unremarkable.
    """
    store, _, strategy, _, _, _ = stack
    drivers = [DriverState(car_idx=0, name="PLAYER")]
    drivers[0].active = True
    drivers[0].is_player = True
    drivers[0].result_label = "active"
    for idx in range(1, 7):
        drivers.append(_rival(idx, "INTER", 90000, 101000))
    for idx in range(7, 12):
        drivers.append(_rival(idx, "MEDIUM", 90000, 105500))

    def setup(state):
        _wet_race(
            state,
            current_lap=20,
            total_laps=50,
            weather="Light rain",
            rain_now_pct=50,
            rain_next_15_pct=50,
            compound="MEDIUM",
            drivers=drivers,
            laps=[
                _player_lap(n, 90000, weather="Clear", rain_pct=0) for n in range(14, 18)
            ]
            + [
                _player_lap(n, 105000, weather="Light rain", rain_pct=50)
                for n in range(18, 20)
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    crossover = result.get("weather_crossover") or {}

    split = crossover.get("compound_split")
    assert split is not None, "a field split across compounds was not measured"
    assert split["wet_advantage_fraction"] > 0, (
        "the intermediate runners were quicker and the model did not notice"
    )
    assert float(crossover["wetness"]) > 0.35, crossover["reason"]
    assert crossover["compound"] == "INTER"
    assert result["recommended"]["fit_compound"] == "INTER"


@pytest.mark.asyncio
async def test_a_field_still_at_dry_pace_overrules_a_rainy_forecast(stack):
    """Ten cars lapping at dry pace is not a wet track, whatever the sky says.

    This is the failure mode in the other direction: a forecast full of rain
    while the track is still quick sent the driver to intermediates and threw
    away seven seconds a lap.
    """
    store, _, strategy, _, _, _ = stack
    drivers = [DriverState(car_idx=0, name="PLAYER")]
    drivers[0].active = True
    drivers[0].is_player = True
    drivers[0].result_label = "active"
    for idx in range(1, 11):
        drivers.append(_rival(idx, "MEDIUM", 90000, 90200, dry_laps=14))

    def setup(state):
        _wet_race(
            state,
            current_lap=20,
            total_laps=50,
            weather="Light rain",
            rain_now_pct=70,
            rain_next_15_pct=70,
            compound="MEDIUM",
            drivers=drivers,
            # The race ran dry to lap 14 and the sky has been spitting since,
            # but nobody has actually lost any lap time to it.
            laps=[
                _player_lap(n, 90000, weather="Overcast", rain_pct=0)
                for n in range(10, 15)
            ]
            + [
                _player_lap(n, 90100, weather="Light rain", rain_pct=70)
                for n in range(15, 20)
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    crossover = result.get("weather_crossover") or {}

    assert float(crossover.get("wetness", 1.0)) < 0.35, (
        f"a field at dry pace was read as a wet track: {crossover.get('reason')}"
    )
    assert not crossover.get("worth_stopping"), (
        f"called a weather stop on a track nobody is losing time to: {crossover.get('reason')}"
    )
    assert crossover.get("compound") != "INTER"
    assert result["recommended"].get("fit_compound") != "INTER", (
        "a dry-pace field still ended up on intermediates"
    )


@pytest.mark.asyncio
async def test_a_clearing_forecast_holds_the_driver_out_of_the_pit_lane(stack):
    """Worsening rain, drying forecast, and not much race left: stay out.

    Every input that a weather-label model looks at says box for wets. The one
    that matters — what the track will be doing for the laps that are left —
    says the stop loses time.
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _wet_race(
            state,
            current_lap=42,
            total_laps=50,
            weather="Heavy rain",
            rain_now_pct=95,
            rain_next_15_pct=10,
            track_temp_c=34,
            compound="INTER",
            age=12,
            wear=[45.0, 45.0, 45.0, 45.0],
            forecast=[
                {"time_offset_min": 0, "weather": "Heavy rain", "rain_pct": 95, "track_temp_c": 30},
                {"time_offset_min": 4, "weather": "Light rain", "rain_pct": 30, "track_temp_c": 32},
                {"time_offset_min": 9, "weather": "Overcast", "rain_pct": 0, "track_temp_c": 36},
            ],
            laps=[
                _player_lap(n, 104000, weather="Heavy rain", rain_pct=95)
                for n in range(37, 42)
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    crossover = result.get("weather_crossover") or {}

    assert crossover.get("compound") != "WET", (
        f"called full wets into a clearing forecast: {crossover.get('reason')}"
    )
    assert float(crossover["projected_end_wetness"]) < float(crossover["wetness"]), (
        "the projection did not see the track drying before the flag"
    )


@pytest.mark.asyncio
async def test_the_conditions_are_reported_even_when_no_stop_is_called(stack):
    """Silence is the one answer a driver in the rain must never get."""
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _wet_race(
            state,
            current_lap=49,
            total_laps=50,
            weather="Heavy rain",
            rain_now_pct=95,
            rain_next_15_pct=95,
            compound="MEDIUM",
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    crossover = result.get("weather_crossover") or {}

    assert crossover, "a soaked track produced no conditions report at all"
    assert crossover.get("reason")
    assert crossover.get("wetness") is not None


@pytest.mark.asyncio
async def test_the_wet_call_carries_the_evidence_it_was_made_on(stack):
    """Every wet call has to be auditable, like the rest of the engine's calls."""
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _wet_race(
            state,
            current_lap=15,
            total_laps=50,
            weather="Heavy rain",
            rain_now_pct=90,
            rain_next_15_pct=90,
            compound="MEDIUM",
            laps=[
                _player_lap(n, 103000, weather="Heavy rain", rain_pct=90)
                for n in range(10, 15)
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    crossover = result.get("weather_crossover") or {}

    source = crossover.get("wetness_source") or {}
    assert {"declared", "surface_model", "measured_pace", "pace_confidence"} <= set(source)
    assert crossover.get("options"), "the compounds considered were not published"
    assert {item["compound"] for item in crossover["options"]} >= {"INTER", "WET"}
    assert crossover.get("margin_s") is not None
    assert crossover.get("uncertainty_s") is not None


@pytest.mark.asyncio
async def test_a_driver_reporting_standing_water_moves_the_call(stack):
    """The driver is the only one who can see the track. Let them say so."""
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _wet_race(
            state,
            current_lap=20,
            total_laps=50,
            weather="Heavy rain",
            rain_now_pct=90,
            rain_next_15_pct=90,
            compound="INTER",
            age=8,
        )

    await store.mutate(setup)
    before = (await strategy.recompute()).get("weather_crossover") or {}

    def report(state):
        state.driver_grip_feedback = {
            "lap": 20,
            "category": "flooded",
            "confidence": 1.0,
            "text": "I'm aquaplaning everywhere",
        }

    await store.mutate(report)
    after = (await strategy.recompute()).get("weather_crossover") or {}

    assert float(after["wetness"]) > float(before["wetness"]), (
        "the driver reported standing water and the model did not move"
    )
    assert (after.get("wetness_source") or {}).get("driver_report")


@pytest.mark.asyncio
async def test_a_reported_mistake_keeps_a_slow_lap_out_of_the_weather_read(stack):
    """A spin is not a weather front."""
    store, _, strategy, _, _, _ = stack
    laps = [_player_lap(n, 90000, weather="Clear", rain_pct=0) for n in range(15, 19)]
    # One lap eleven seconds slow, spread evenly so the sector shape alone
    # cannot tell it apart from a wet lap. The driver's report has to.
    laps.append(
        _player_lap(19, 101000, weather="Clear", rain_pct=0, sectors=(33700, 33600, 33700))
    )

    def setup(state):
        _wet_race(
            state,
            current_lap=20,
            total_laps=50,
            weather="Overcast",
            compound="MEDIUM",
            laps=laps,
        )

    await store.mutate(setup)
    misread = (await strategy.recompute()).get("weather_crossover") or {}

    def report(state):
        state.driver_lap_incidents = [{"lap": 19, "text": "I went off at turn nine"}]

    await store.mutate(report)
    corrected = (await strategy.recompute()).get("weather_crossover") or {}

    assert float((corrected or {}).get("wetness", 0.0)) <= float(
        (misread or {}).get("wetness", 0.0)
    ), "a reported mistake still counted as evidence the track had changed"
