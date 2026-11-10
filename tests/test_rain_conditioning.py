"""The forecast is read in the light of the sky, and laps in the light of their time.

Three defects made the wet call late or absent:

* every future forecast sample was treated as independent of the rain already
  falling, so with a 50% chance the model kept half its weight on a track that
  started drying in three laps, and never called intermediates however long it
  had been raining;
* a completed lap's pace was compared with the surface *now* rather than the
  surface it was driven on, so the first wet lap talked the model back out of
  the rain a lap after it had called the stop;
* the spoken reason named a dry compound as "the tyre for it" on a wet track
  and blamed race distance with half the race to run.

The scenarios reuse ``tools.rain_onset_probe``: light rain from lap 11 of 40,
with the surface and every lap time following the model's own curves.
"""

from __future__ import annotations

import pytest

from pitwall import rain
from tools import rain_onset_probe as probe


def _forecast(chance: int, *, weather: str = "Light rain", offsets=(0, 5)) -> list[dict]:
    return [
        {"time_offset_min": minutes, "weather": weather, "rain_pct": chance}
        for minutes in offsets
    ]


def _state(weather: str, forecast: list[dict]) -> dict:
    return {
        "weather": weather,
        "weather_forecast": forecast,
        "forecast_accuracy": 0,
        "track_temp_c": 30,
        "active_cars": 20,
    }


def _wet_probability(scenarios, index: int) -> float:
    return sum(
        item["probability"]
        for item in scenarios
        if item["trajectory"][index] > rain.WETNESS_SLICK_INTER
    )


# --- WX-01: condition the forecast on the sky now ------------------------------


def test_rain_falling_now_makes_an_equal_future_chance_near_certain():
    scenarios = rain.project_wetness_scenarios(
        _state("Light rain", _forecast(50)), 0.5, 20, 90.0
    )
    assert sum(item["probability"] for item in scenarios) == pytest.approx(1.0)
    assert _wet_probability(scenarios, 10) == pytest.approx(0.9)


def test_a_dry_sky_under_the_same_chance_discounts_it():
    scenarios = rain.project_wetness_scenarios(
        _state("Overcast", _forecast(50)), 0.0, 20, 90.0
    )
    assert _wet_probability(scenarios, 10) == pytest.approx(0.1)


def test_no_present_sample_leaves_the_published_chance_alone():
    scenarios = rain.project_wetness_scenarios(
        _state("Overcast", _forecast(20, offsets=(3,))), 0.0, 20, 90.0
    )
    assert _wet_probability(scenarios, 10) == pytest.approx(0.2)


def test_rain_under_a_zero_present_chance_trusts_the_sky():
    forecast = [
        {"time_offset_min": 0, "weather": "Light rain", "rain_pct": 0},
        {"time_offset_min": 5, "weather": "Light rain", "rain_pct": 40},
    ]
    scenarios = rain.project_wetness_scenarios(
        _state("Light rain", forecast), 0.5, 20, 90.0
    )
    assert _wet_probability(scenarios, 10) == pytest.approx(0.8 + 0.2 * 0.4)


@pytest.mark.parametrize(
    "chance,now_chance,raining,expected",
    [
        (0.5, 0.5, True, 0.9),
        (0.3, 0.5, True, 0.54),
        (0.6, 0.1, True, 0.92),
        (0.2, 0.8, True, 0.24),
        (0.7, 0.1, False, 0.8 * (0.6 / 0.9) + 0.2 * 0.7),
        (0.5, 0.5, False, 0.1),
        (0.2, 0.0, False, 0.2),
        (0.2, None, False, 0.2),
        (0.2, None, True, 0.2),
    ],
)
def test_conditioned_chances_match_the_specification(chance, now_chance, raining, expected):
    assert rain.conditioned_rain_probability(chance, now_chance, raining) == pytest.approx(expected)


def test_a_fifty_percent_forecast_does_not_hold_a_soaked_car_out():
    decision = probe.evaluate_at(14, 11, 50)
    assert decision["should_change"] is True
    assert decision["best_compound"] == "INTER"


def test_a_label_alone_does_not_call_the_stop():
    """The first lap of rain: the sky says wet, nothing measured agrees yet."""
    decision = probe.evaluate_at(11, 11, 100, with_field=False)
    assert decision["best_compound"] == "INTER"
    assert decision["confirmed"] is False
    assert decision["should_change"] is False
    assert decision["upcoming_change"]["awaiting_confirmation"] is True
    assert "standing by" in decision["reason"]


def test_a_driver_report_confirms_the_label():
    state = probe.state_at(11, 11, 100, with_field=False)
    state["driver_grip_feedback"] = {"lap": 11, "category": "damp", "confidence": 1.0}
    decision = rain.evaluate(
        state,
        base_lap_s=probe.DRY_LAP_S,
        remaining_laps=probe.TOTAL_LAPS - 10,
        pit_loss_s=probe.PIT_LOSS_S,
        available_compounds=probe.AVAILABLE,
    )
    assert decision["confirmed"] is True
    assert decision["should_change"] is True


def test_a_change_during_a_suspension_needs_no_confirmation():
    state = probe.state_at(11, 11, 100, with_field=False)
    decision = rain.evaluate(
        state,
        base_lap_s=probe.DRY_LAP_S,
        remaining_laps=probe.TOTAL_LAPS - 10,
        pit_loss_s=0.0,
        available_compounds=probe.AVAILABLE,
        change_during_suspension=True,
    )
    assert decision["confirmed"] is True


@pytest.mark.parametrize("chance", [30, 50, 70, 100])
def test_the_call_is_made_within_a_lap_and_kept(chance):
    calls = [probe.evaluate_at(lap, 11, chance)["should_change"] for lap in range(11, 20)]
    first = calls.index(True)
    assert first <= 1
    assert all(calls[first:])


def test_a_forecast_that_never_arrives_calls_nothing():
    for lap in (12, 13, 14):
        decision = probe.evaluate_at(lap, 999, 50, weather_now="Overcast")
        assert decision["should_change"] is False
        assert decision["upcoming_change"] is None


# --- WX-02: a lap measures the surface it was driven on --------------------------


def test_the_first_wet_lap_does_not_talk_the_model_out_of_the_rain():
    state = probe.state_at(12, 11, 100)
    state.update(track_length_m=5000, lap_distance_m=2500)
    reading = rain.estimate_wetness(state, dry_base_lap_s=probe.DRY_LAP_S)
    end_of_first_wet_lap = probe.true_wetness(11, 11)
    expected = rain.step_wetness(
        end_of_first_wet_lap, 0.52, laps=0.5, track_temp_c=28, active_cars=20
    )
    assert reading["wetness"] == pytest.approx(expected, abs=0.03)
    assert abs(reading["equilibrium_bias"]) < 0.03
    assert reading["surface_when_measured"] == pytest.approx(end_of_first_wet_lap / 2, abs=0.01)
    assert reading["lap_fraction"] == pytest.approx(0.5)


def test_the_lap_in_progress_soaks_by_the_distance_driven():
    early = probe.state_at(12, 11, 100, with_field=False)
    late = probe.state_at(12, 11, 100, with_field=False)
    early.update(track_length_m=5000, lap_distance_m=250)
    late.update(track_length_m=5000, lap_distance_m=4750)
    early_reading = rain.estimate_wetness(early, dry_base_lap_s=probe.DRY_LAP_S)
    late_reading = rain.estimate_wetness(late, dry_base_lap_s=probe.DRY_LAP_S)
    assert late_reading["dynamic_wetness"] > early_reading["dynamic_wetness"]
    assert early_reading["dynamic_wetness"] == pytest.approx(
        probe.true_wetness(11, 11), abs=0.03
    )


def test_a_state_without_lap_distance_keeps_the_whole_lap_step():
    reading = rain.estimate_wetness(probe.state_at(12, 11, 100, with_field=False))
    assert reading["lap_fraction"] == 1.0
    assert reading["dynamic_wetness"] == pytest.approx(probe.true_wetness(12, 11), abs=0.01)


def test_a_settled_wet_race_reads_as_it_did():
    """Ten laps of the same rain: the measurement and the model describe one surface."""
    reading = rain.estimate_wetness(probe.state_at(22, 11, 100), dry_base_lap_s=probe.DRY_LAP_S)
    assert reading["surface_when_measured"] == pytest.approx(reading["dynamic_wetness"], abs=0.01)
    assert reading["wetness"] == pytest.approx(reading["dynamic_wetness"], abs=0.02)
    assert reading["wetness"] == pytest.approx(0.52, abs=0.02)


# --- WX-05: the reason says what is true ------------------------------------------


@pytest.mark.parametrize("fitted", ["SOFT", "MEDIUM", "HARD"])
def test_a_dry_compound_is_never_named_as_the_wet_answer(fitted):
    for lap in range(12, 20):
        state = probe.state_at(lap, 11, 50)
        state["tyre"] = {"compound": fitted}
        decision = rain.evaluate(
            state,
            base_lap_s=probe.DRY_LAP_S,
            remaining_laps=probe.TOTAL_LAPS - lap + 1,
            pit_loss_s=probe.PIT_LOSS_S,
            available_compounds=probe.AVAILABLE,
        )
        for dry in ("SOFT", "MEDIUM", "HARD"):
            assert f"{dry} is the tyre for it" not in decision["reason"]


def test_between_dry_compounds_the_fitted_one_is_the_answer():
    penalties = {"HARD": [0.0, 0.0], "MEDIUM": [0.0, 0.0], "INTER": [0.08, 0.08]}
    assert rain._pace_best(penalties, [0.0, 0.0], 0.0, "MEDIUM") == "MEDIUM"
    wet = {"HARD": [0.06, 0.06], "MEDIUM": [0.06, 0.06], "INTER": [0.01, 0.01]}
    assert rain._pace_best(wet, [0.5, 0.5], 0.5, "MEDIUM") == "INTER"


def test_the_reason_does_not_blame_distance_with_half_a_race_left():
    """Raining now, certain to clear in minutes: inters are quicker, staying out is right."""
    state = probe.state_at(16, 11, 100)
    state["weather_forecast"] = [
        {"time_offset_min": 0, "weather": "Light rain", "rain_pct": 100},
        *(
            {"time_offset_min": minutes, "weather": "Clear", "rain_pct": 0}
            for minutes in (3, 10, 15, 30, 45, 60)
        ),
    ]
    decision = rain.evaluate(
        state,
        base_lap_s=probe.DRY_LAP_S,
        remaining_laps=probe.TOTAL_LAPS - 15,
        pit_loss_s=probe.PIT_LOSS_S,
        available_compounds=probe.AVAILABLE,
    )
    assert decision["should_change"] is False
    assert "cannot repay" not in decision["reason"]
    assert "lap(s)" not in decision["reason"]


def test_a_short_race_still_blames_distance():
    state = probe.state_at(38, 11, 100)
    state["tyre"] = {"compound": "MEDIUM"}
    decision = rain.evaluate(
        state,
        base_lap_s=probe.DRY_LAP_S,
        remaining_laps=1,
        pit_loss_s=probe.PIT_LOSS_S,
        available_compounds=probe.AVAILABLE,
    )
    assert "cannot repay" in decision["reason"]
    assert "1 lap left" in decision["reason"]


def test_raining_now_is_not_described_as_drying():
    decision = probe.evaluate_at(14, 11, 50)
    assert "drying" not in decision["reason"]
