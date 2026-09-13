"""Causal weather/learning scenarios defined before strategy refinement.

These use synthetic observations and exact timing boundaries, not saved user
sessions or the model's own chosen strategy as a reference answer.
"""

from copy import deepcopy

import pytest

from pitwall import rain
from pitwall.tyre_learning import stint_pace_model, wear_deltas


def lap(number, seconds=90.0, **changes):
    fuel = 60 - 1.5 * number
    return {
        "lap_num": number, "lap_time_ms": round(seconds * 1000),
        "valid": True, "weather": "Clear", "compound": "MEDIUM",
        "tyre_age_start": number - 1, "tyre_age_end": number,
        "fuel_start_kg": fuel + 0.75, "fuel_end_kg": fuel - 0.75,
        "wear_start": [2.5 * (number - 1)] * 4,
        "wear_end": [2.5 * number] * 4,
        "s1_ms": round(seconds * 1000 / 3),
        "s2_ms": round(seconds * 1000 / 3),
        "s3_ms": round(seconds * 1000 / 3),
        **changes,
    }


def state(**changes):
    return {
        "weather": "Clear", "track_temp_c": 25, "active_cars": 20,
        "current_lap": 7, "tyre": {"compound": "MEDIUM"},
        "completed_laps": [lap(n) for n in range(2, 7)],
        "player_car_index": 0, "race_control_phase": "green",
        **changes,
    }


@pytest.mark.parametrize("interruption", [
    {"learning_exclusions": ["neutralised_lap"]},
    {"learning_exclusions": ["pit_lap"]},
    {"race_control_phase": "vsc"},
    {"safety_car": "full"},
    {"red_flag_active": True},
])
def test_recorded_interruption_is_not_rain_even_after_green_returns(interruption):
    fixture = state()
    fixture["completed_laps"][-1] = lap(6, 110, weather="Overcast", **interruption)
    reading = rain.player_pace_observation(fixture, 90)
    assert reading["ratio"] is None or reading["ratio"] < 1.02, reading
    assert reading["dropped_laps"] >= 1


@pytest.mark.parametrize("phase,previous_interrupted", [("vsc", False), ("green", True)])
def test_field_neutralisation_cannot_outvote_dry_weather(phase, previous_interrupted):
    fixture = state(race_control_phase=phase)
    if previous_interrupted:
        fixture["completed_laps"][-1]["learning_exclusions"] = ["neutralised_lap"]
    fixture["drivers"] = [
        {"active": True, "car_idx": car, "tyre_compound": "MEDIUM",
         "lap_history": [{"lap_num": n, "lap_ms": 90000 if n < 6 else 110000,
                          "valid_flags": 1} for n in range(2, 7)]}
        for car in range(1, 9)
    ]
    assert rain.field_pace_observations(fixture, dry_until_lap=5) == []
    assert rain.estimate_wetness(fixture)["field_pace_exclusion"] is not None


def test_a_completed_lap_keeps_its_compound_after_a_pit_stop():
    fixture = state()
    fixture["completed_laps"][-1] = lap(6, 101, weather="Light rain")
    before = rain.player_pace_observation(fixture, 90)
    fixture["tyre"]["compound"] = "INTER"
    after = rain.player_pace_observation(fixture, 90)
    assert after["compound"] == before["compound"] == "MEDIUM"
    assert after["ratio"] == before["ratio"]
    fixture["completed_laps"].append(lap(7, 98, compound="INTER", weather="Light rain"))
    assert rain.player_pace_observation(fixture, 90)["compound"] == "INTER"


def forecast(minutes):
    return {"time_offset_min": minutes, "weather": "Heavy rain", "rain_pct": 100}


def test_rain_in_last_six_seconds_does_not_wet_an_entire_two_minute_lap():
    fixture = state(weather_forecast=[forecast(3.9)])
    trajectory = rain.project_wetness(fixture, 0, 3, 120)
    assert trajectory[0] == 0
    # Only 0.05 lap of soaking: independently 0.72 * (1 - 0.55**0.05).
    assert trajectory[1] == pytest.approx(0.72 * (1 - 0.55 ** 0.05), abs=0.0001)
    assert trajectory[2] > trajectory[1]


def test_weather_arriving_at_the_finish_cannot_change_the_race():
    fixture = state(weather_forecast=[forecast(4)])
    assert rain.project_wetness(fixture, 0, 2, 120) == [0, 0]


@pytest.mark.parametrize("capture_time,now", [(300, 480), (0, 180)])
def test_forecast_ages_in_session_time_instead_of_restarting_its_countdown(capture_time, now):
    fixture = state(weather_forecast=[forecast(5)],
                    weather_forecast_session_time_s=capture_time, session_time_s=now)
    trajectory = rain.project_wetness(fixture, 0, 4, 60)
    assert trajectory[:2] == [0, 0]
    assert trajectory[2] > 0, trajectory
    legacy = deepcopy(fixture)
    legacy.pop("weather_forecast_session_time_s")
    assert rain.project_wetness(legacy, 0, 4, 60) == [0] * 4


def test_wetting_under_unchanged_sky_label_does_not_become_intrinsic_degradation():
    fixture = [lap(n, 90 + 0.15 * n + 0.50 * n + 0.030 * (60 - 1.5 * n),
                   weather="Light rain") for n in range(2, 12)]
    fitted = stint_pace_model(fixture)
    assert fitted["slope_s_per_lap"] is None, fitted
    assert fitted["sample_size"] == 0
    assert fitted["excluded_laps"]["unresolved_wet_conditions"] == len(fixture)
    assert all(wear_deltas(item) == [2.5] * 4 for item in fixture)


def test_dry_and_legacy_weather_runs_still_learn_the_known_slope():
    fixture = [lap(n, 90 + 0.15 * n + 0.030 * (60 - 1.5 * n)) for n in range(2, 12)]
    for omitted in (False, True):
        records = deepcopy(fixture)
        if omitted:
            for item in records:
                item.pop("weather")
        fitted = stint_pace_model(records)
        assert fitted["slope_s_per_lap"] == pytest.approx(0.15, abs=0.001)


@pytest.mark.asyncio
async def test_udp_forecast_preserves_capture_time_and_ignores_other_sessions():
    from f1.packets import PacketSessionData

    from pitwall.state import StateStore
    from pitwall.udp import F1DatagramProtocol

    store = StateStore()
    protocol = F1DatagramProtocol(store)
    packet = PacketSessionData()
    packet.header.session_time = 300
    packet.session_type = 15
    packet.num_weather_forecast_samples = 2
    current, qualifying = packet.weather_forecast_samples[:2]
    current.session_type, current.time_offset, current.weather, current.rain_percentage = 15, 5, 0, 0
    qualifying.session_type, qualifying.time_offset, qualifying.weather, qualifying.rain_percentage = 5, 5, 5, 100
    await protocol.handle_PacketSessionData(packet)
    snapshot = await store.snapshot()
    assert len(snapshot["weather_forecast"]) == 1, snapshot["weather_forecast"]
    assert snapshot["weather_forecast_session_time_s"] == 300
    assert snapshot["rain_next_15_pct"] == 0


@pytest.mark.asyncio
async def test_wet_pace_exclusion_and_measured_wear_survive_database_reopening(stack):
    from pitwall.database import PitWallDatabase

    _, database, *_ = stack
    for n in range(2, 12):
        await database.save_lap(lap(n, 90 + 0.65 * n + 0.030 * (60 - 1.5 * n),
                                   session_uid=701, track_id=7, track_name="Montreal", mode_profile="race",
                                   session_type="Race", weather="Light rain"), [])
    reopened = PitWallDatabase(database.path)
    await reopened.initialize()
    model = (await reopened.tyre_history_model(7))["compounds"]["MEDIUM"]
    assert model["slope_s_per_lap"] is None
    assert model["pace_excluded_laps"] == {"unresolved_wet_conditions": 10}
    assert model["wear_sample_size"] == 10
    assert model["max_wear_per_lap_pct"] == 2.5


def test_weather_call_never_invents_an_unavailable_wet_compound():
    fixture = state(weather="Heavy rain", completed_laps=[])
    decision = rain.evaluate(fixture, base_lap_s=90, remaining_laps=40, pit_loss_s=22,
                             available_compounds=["MEDIUM", "WET"])
    assert {item["compound"] for item in decision["options"]} == {"MEDIUM", "WET"}
    assert decision["best_compound"] == "WET"
    assert decision["should_change"]
