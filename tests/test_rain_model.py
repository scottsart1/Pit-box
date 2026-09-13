"""The wet-weather model, pinned to the calls it exists to get right.

Each test is written from a race situation rather than from the implementation,
because the numbers inside ``rain.py`` are a means to these answers and not the
point. The two calibration tests are the exception: they pin the crossovers the
whole model is anchored to, so a reshaped curve that moves them fails loudly
instead of quietly changing every call in the file.
"""

from __future__ import annotations

import pytest

from pitwall import rain


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def test_slick_to_inter_crossover_sits_at_112_percent_of_dry_pace():
    """The conventional figure wet strategy is actually called on."""
    wetness = rain.WETNESS_SLICK_INTER
    slick = 1.0 + rain.lap_penalty_fraction("MEDIUM", wetness)
    inter = 1.0 + rain.lap_penalty_fraction("INTER", wetness)
    assert slick == pytest.approx(inter, abs=1e-4), "the crossover must be a crossover"
    assert slick == pytest.approx(1.12, abs=1e-3), (
        f"slick/inter crossover landed at {slick:.4f} of dry pace, not 1.12"
    )


def test_inter_to_wet_crossover_sits_at_118_percent_of_dry_pace():
    wetness = rain.WETNESS_INTER_WET
    inter = 1.0 + rain.lap_penalty_fraction("INTER", wetness)
    full_wet = 1.0 + rain.lap_penalty_fraction("WET", wetness)
    assert inter == pytest.approx(full_wet, abs=1e-4)
    assert inter == pytest.approx(1.18, abs=1e-3), (
        f"inter/wet crossover landed at {inter:.4f} of dry pace, not 1.18"
    )


def test_full_wets_only_win_in_standing_water():
    """The tyre nobody fits. A model that reaches for it in heavy rain is wrong.

    Full wets clear far more water than intermediates but give up several
    seconds a lap doing it, which is why pit walls pass over them in all but
    genuinely flooded conditions.
    """
    for wetness in (0.0, 0.2, 0.35, 0.5, 0.6, 0.7):
        assert rain.best_compound_for(wetness, ("MEDIUM", "INTER", "WET")) != "WET", (
            f"full wets chosen at wetness {wetness}"
        )
    assert rain.best_compound_for(0.95, ("MEDIUM", "INTER", "WET")) == "WET"


def test_slicks_are_quicker_than_inters_on_a_merely_damp_track():
    """Below the crossover the dry tyre still wins, which is half the problem.

    Reaching for intermediates the moment it starts spitting throws away the
    laps where a slick is still the faster tyre.
    """
    assert rain.best_compound_for(0.2, ("MEDIUM", "INTER", "WET")) == "MEDIUM"
    assert rain.best_compound_for(0.5, ("MEDIUM", "INTER", "WET")) == "INTER"


# ---------------------------------------------------------------------------
# The surface lags the sky
# ---------------------------------------------------------------------------


def test_a_track_does_not_soak_the_instant_it_starts_raining():
    """A dry surface takes time to give up its grip, and slicks keep it meanwhile."""
    target = rain.equilibrium_wetness("Heavy rain", 100)
    wet = rain.step_wetness(0.0, target, laps=1, track_temp_c=30, active_cars=20)
    assert wet < rain.WETNESS_SLICK_INTER, (
        "one lap of rain put the track straight past the slick crossover"
    )
    soaked = rain.step_wetness(0.0, target, laps=4, track_temp_c=30, active_cars=20)
    assert soaked > rain.WETNESS_SLICK_INTER, (
        "four laps of heavy rain left the track dry enough for slicks"
    )


def test_a_track_dries_far_more_slowly_than_it_soaks():
    soaking = rain.step_wetness(0.2, 0.9, laps=3, track_temp_c=25, active_cars=20)
    drying = rain.step_wetness(0.9, 0.0, laps=3, track_temp_c=25, active_cars=20)
    assert (soaking - 0.2) > (0.9 - drying), (
        "wetting and drying came out symmetric; a track that dries as fast as it "
        "soaks will send the driver to slicks the moment the rain eases"
    )


def test_a_hot_track_dries_faster_than_a_cold_one():
    hot = rain.step_wetness(0.8, 0.0, laps=5, track_temp_c=45, active_cars=20)
    cold = rain.step_wetness(0.8, 0.0, laps=5, track_temp_c=8, active_cars=20)
    assert hot < cold


# ---------------------------------------------------------------------------
# Lap-time evidence, and what it is not
# ---------------------------------------------------------------------------


def _lap(num, ms, *, weather="Clear", rain_pct=0, sectors=(30000, 30000, 30000), valid=True):
    return {
        "lap_num": num,
        "lap_time_ms": ms,
        "valid": valid,
        "weather": weather,
        "rain_pct": rain_pct,
        "s1_ms": sectors[0],
        "s2_ms": sectors[1],
        "s3_ms": sectors[2],
        "pit_status": 0,
    }


def test_a_lap_lost_in_one_sector_is_a_mistake_not_rain():
    """The attribution the whole pace channel depends on.

    A spin costs five seconds in one sector. Rain costs time everywhere. Read
    the first as the second and a dry race ends up on intermediates.
    """
    state = {
        "completed_laps": [
            _lap(1, 90000),
            _lap(2, 90000),
            _lap(3, 90000),
            # Same lap, five seconds of it dropped into sector two alone.
            _lap(4, 95000, sectors=(30000, 35000, 30000)),
        ],
        "tyre": {"compound": "MEDIUM"},
        "current_lap": 5,
    }
    observation = rain.player_pace_observation(state, 90.0)
    assert observation["dropped_laps"] >= 1
    assert observation["ratio"] is None or observation["ratio"] < 1.02, (
        "a one-sector loss was read as a slower track"
    )


def test_a_lap_that_slowed_in_every_sector_is_the_track():
    state = {
        "completed_laps": [
            _lap(1, 90000),
            _lap(2, 90000),
            _lap(3, 90000),
            _lap(
                4,
                101000,
                weather="Light rain",
                rain_pct=80,
                sectors=(33700, 33600, 33700),
            ),
        ],
        "tyre": {"compound": "MEDIUM"},
        "current_lap": 5,
    }
    observation = rain.player_pace_observation(state, 90.0)
    assert observation["ratio"] is not None
    assert observation["ratio"] > 1.10


def test_a_lap_the_driver_reported_a_mistake_on_is_dropped():
    laps = [_lap(n, 90000) for n in (1, 2, 3)] + [_lap(4, 99000, sectors=(33000, 33000, 33000))]
    state = {"completed_laps": laps, "tyre": {"compound": "MEDIUM"}, "current_lap": 5}
    without = rain.player_pace_observation(state, 90.0)
    with_report = rain.player_pace_observation(state, 90.0, incident_laps=[4])
    assert without["ratio"] > 1.05
    assert with_report["dropped_laps"] > without["dropped_laps"]


def test_a_split_field_measures_the_crossover_instead_of_guessing_it():
    """Cars on both tyres at once is the strongest evidence there is."""
    observations = [
        {"compound": "INTER", "ratio": 1.13, "samples": 2},
        {"compound": "INTER", "ratio": 1.14, "samples": 2},
        {"compound": "INTER", "ratio": 1.13, "samples": 2},
        {"compound": "MEDIUM", "ratio": 1.19, "samples": 2},
        {"compound": "MEDIUM", "ratio": 1.20, "samples": 2},
    ]
    split = rain.compound_split(observations)
    assert split is not None
    assert split["wet_advantage_fraction"] > 0, "wet runners were quicker and it was missed"
    fitted, confidence, samples = rain.fit_wetness(observations, prior=0.0)
    assert samples == 10
    assert fitted > rain.WETNESS_SLICK_INTER, (
        "the field was measurably quicker on intermediates and the fit stayed dry"
    )


def test_the_prior_does_not_survive_a_field_that_disagrees_with_it():
    """A forecast saying storm loses to six cars lapping at dry pace."""
    observations = [
        {"compound": "MEDIUM", "ratio": 1.01, "samples": 3},
        {"compound": "MEDIUM", "ratio": 1.02, "samples": 3},
        {"compound": "MEDIUM", "ratio": 1.00, "samples": 3},
    ]
    fitted, confidence, _ = rain.fit_wetness(observations, prior=0.9)
    assert fitted < rain.WETNESS_SLICK_INTER
    assert confidence > 0.5


# ---------------------------------------------------------------------------
# The decisions
# ---------------------------------------------------------------------------


def _race_state(**overrides):
    state = {
        "current_lap": 10,
        "total_laps": 50,
        "weather": "Clear",
        "rain_now_pct": 0,
        "rain_next_15_pct": 0,
        "weather_forecast": [],
        "forecast_accuracy": 0,
        "track_temp_c": 28,
        "air_temp_c": 20,
        "active_cars": 20,
        "player_car_index": 0,
        "drivers": [],
        "completed_laps": [],
        "tyre": {"compound": "MEDIUM", "age_laps": 8},
    }
    tyre = overrides.pop("tyre", None)
    state.update(overrides)
    if tyre:
        state["tyre"] = tyre
    return state


def test_settled_rain_on_slicks_calls_the_stop():
    state = _race_state(
        weather="Heavy rain",
        rain_now_pct=95,
        rain_next_15_pct=95,
        completed_laps=[
            _lap(n, 92000, weather="Heavy rain", rain_pct=95) for n in range(5, 10)
        ],
    )
    decision = rain.evaluate(
        state,
        base_lap_s=90.0,
        remaining_laps=41,
        pit_loss_s=22.0,
        available_compounds=("MEDIUM", "INTER", "WET"),
    )
    assert decision["should_change"] is True
    assert decision["best_compound"] == "INTER"


def test_a_shower_that_clears_before_the_flag_is_left_alone():
    """The call the user is really asking for: forecast beats conditions.

    It is raining harder than it was and the driver is on intermediates that
    are about to be wrong. Swapping to full wets wins the next three laps and
    loses the fifteen after them, so the answer is to stay out — which no
    amount of looking at the current weather can produce.
    """
    state = _race_state(
        current_lap=32,
        total_laps=50,
        weather="Heavy rain",
        rain_now_pct=90,
        rain_next_15_pct=10,
        track_temp_c=34,
        weather_forecast=[
            {"time_offset_min": 0, "weather": "Heavy rain", "rain_pct": 90, "track_temp_c": 30},
            {"time_offset_min": 5, "weather": "Light rain", "rain_pct": 35, "track_temp_c": 32},
            {"time_offset_min": 10, "weather": "Overcast", "rain_pct": 5, "track_temp_c": 35},
            {"time_offset_min": 30, "weather": "Clear", "rain_pct": 0, "track_temp_c": 38},
        ],
        tyre={"compound": "INTER", "age_laps": 9},
        completed_laps=[
            _lap(n, 104000, weather="Heavy rain", rain_pct=90) for n in range(27, 32)
        ],
    )
    decision = rain.evaluate(
        state,
        base_lap_s=90.0,
        remaining_laps=19,
        pit_loss_s=22.0,
        available_compounds=("MEDIUM", "INTER", "WET"),
    )
    assert decision["best_compound"] != "WET", (
        "went to full wets into a clearing forecast: "
        f"{decision['reason']}"
    )
    assert decision["projected_end_wetness"] < decision["wetness"], (
        "the projection failed to see the track drying"
    )


def test_a_drying_track_reaches_for_slicks_while_it_is_still_spitting():
    state = _race_state(
        current_lap=30,
        total_laps=50,
        weather="Light rain",
        rain_now_pct=20,
        rain_next_15_pct=5,
        track_temp_c=36,
        weather_forecast=[
            {"time_offset_min": 0, "weather": "Light rain", "rain_pct": 20, "track_temp_c": 34},
            {"time_offset_min": 15, "weather": "Overcast", "rain_pct": 0, "track_temp_c": 38},
        ],
        tyre={"compound": "INTER", "age_laps": 12},
        completed_laps=[
            # The track has been drying for a while: the surface model has to
            # carry that history rather than reading the current label.
            _lap(25, 99000, weather="Light rain", rain_pct=30),
            _lap(26, 97000, weather="Light rain", rain_pct=25),
            _lap(27, 95000, weather="Light rain", rain_pct=20),
            _lap(28, 94000, weather="Light rain", rain_pct=20),
            _lap(29, 93000, weather="Light rain", rain_pct=20),
        ],
        driver_grip_feedback={"lap": 29, "category": "dry_line", "confidence": 1.0},
    )
    decision = rain.evaluate(
        state,
        base_lap_s=90.0,
        remaining_laps=21,
        pit_loss_s=22.0,
        available_compounds=("MEDIUM", "HARD", "INTER"),
    )
    assert decision["best_compound"] in {"MEDIUM", "HARD"}, decision["reason"]
    assert decision["should_change"] is True


def test_no_stop_is_called_when_there_is_no_distance_left_to_pay_for_it():
    state = _race_state(
        current_lap=50,
        total_laps=50,
        weather="Heavy rain",
        rain_now_pct=95,
        rain_next_15_pct=95,
    )
    decision = rain.evaluate(
        state,
        base_lap_s=90.0,
        remaining_laps=1,
        pit_loss_s=22.0,
        available_compounds=("MEDIUM", "INTER", "WET"),
    )
    assert decision["should_change"] is False
    assert decision["best_compound"] == "MEDIUM"
    assert "cannot repay" in decision["reason"]


def test_a_free_change_under_a_red_flag_is_taken_even_on_the_last_lap():
    state = _race_state(
        current_lap=50,
        total_laps=50,
        weather="Storm",
        rain_now_pct=100,
        rain_next_15_pct=100,
        race_control_phase="red_flag",
        red_flag_active=True,
    )
    decision = rain.evaluate(
        state,
        base_lap_s=90.0,
        remaining_laps=1,
        pit_loss_s=0.0,
        available_compounds=("MEDIUM", "INTER", "WET"),
        change_during_suspension=True,
    )
    assert decision["should_change"] is True
    assert decision["best_compound"] in {"INTER", "WET"}


def test_a_marginal_call_asks_the_driver_instead_of_guessing():
    """Sitting on the crossover with a hedged forecast is a question, not a call."""
    state = _race_state(
        current_lap=20,
        total_laps=50,
        weather="Light rain",
        rain_now_pct=55,
        rain_next_15_pct=55,
        forecast_accuracy=1,
        weather_forecast=[
            {"time_offset_min": 0, "weather": "Light rain", "rain_pct": 55, "track_temp_c": 26},
            {"time_offset_min": 20, "weather": "Light rain", "rain_pct": 50, "track_temp_c": 25},
        ],
    )
    decision = rain.evaluate(
        state,
        base_lap_s=90.0,
        remaining_laps=31,
        pit_loss_s=22.0,
        available_compounds=("MEDIUM", "INTER"),
    )
    assert decision["should_ask_driver"] != decision["should_change"], (
        "a call cannot be both made and deferred"
    )
    if decision["should_ask_driver"]:
        assert decision["driver_question"]
        assert decision["driver_question"].endswith("?")


def test_the_driver_report_moves_the_estimate():
    base = _race_state(
        weather="Light rain",
        rain_now_pct=60,
        rain_next_15_pct=60,
        completed_laps=[_lap(n, 95000, weather="Light rain", rain_pct=60) for n in range(5, 10)],
    )
    flooded = dict(base, driver_grip_feedback={"lap": 9, "category": "flooded", "confidence": 1.0})
    drying = dict(base, driver_grip_feedback={"lap": 9, "category": "dry_line", "confidence": 1.0})
    assert (
        rain.estimate_wetness(flooded, dry_base_lap_s=90.0)["wetness"]
        > rain.estimate_wetness(base, dry_base_lap_s=90.0)["wetness"]
        > rain.estimate_wetness(drying, dry_base_lap_s=90.0)["wetness"]
    )


def test_a_stale_driver_report_stops_counting():
    state = _race_state(
        current_lap=40,
        driver_grip_feedback={"lap": 10, "category": "flooded", "confidence": 1.0},
    )
    assert rain.driver_grip_observation(state) is None


def test_an_approximate_forecast_is_trusted_less_than_a_perfect_one():
    forecast = [
        {"time_offset_min": 0, "weather": "Clear", "rain_pct": 0, "track_temp_c": 30},
        {"time_offset_min": 10, "weather": "Storm", "rain_pct": 100, "track_temp_c": 20},
    ]
    perfect = _race_state(weather_forecast=forecast, forecast_accuracy=0)
    approximate = _race_state(weather_forecast=forecast, forecast_accuracy=1)
    perfect_end = rain.project_wetness(perfect, 0.0, 30, 90.0)[-1]
    approximate_end = rain.project_wetness(approximate, 0.0, 30, 90.0)[-1]
    assert approximate_end < perfect_end


def test_a_forecast_beyond_the_finish_does_not_reach_the_projection():
    """Race duration is part of the question, not context for it."""
    state = _race_state(
        current_lap=46,
        total_laps=50,
        weather_forecast=[
            {"time_offset_min": 0, "weather": "Clear", "rain_pct": 0, "track_temp_c": 30},
            {"time_offset_min": 45, "weather": "Storm", "rain_pct": 100, "track_temp_c": 20},
        ],
    )
    trajectory = rain.project_wetness(state, 0.0, 5, 90.0)
    assert max(trajectory) < 0.05, (
        "a storm forty-five minutes after a race that ends in seven reached the call"
    )
