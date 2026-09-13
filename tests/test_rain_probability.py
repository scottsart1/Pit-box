"""Issue #38: EA's rain percentage is probability, never rain intensity."""

from copy import deepcopy

import pytest
from f1.packets import PacketSessionData

from pitwall import rain
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol


def _state(**overrides):
    return {
        "current_lap": 10, "total_laps": 30,
        "weather": "Clear", "track_temp_c": 30, "active_cars": 20,
        "tyre": {"compound": "MEDIUM", "age_laps": 5},
        "weather_forecast": [], "forecast_accuracy": 0,
        **overrides,
    }


def _forecast(pct, weather="Heavy rain", minutes=3):
    return {"time_offset_min": minutes, "weather": weather, "rain_pct": pct}


@pytest.mark.parametrize("weather", ["Clear", "Overcast", "Light rain", "Heavy rain", "Storm", "Unknown"])
def test_probability_cannot_change_weather_equilibrium_or_tyre(weather):
    reference = rain.equilibrium_wetness(weather)
    for pct in (None, 0, 20, 35, 50, 100):
        wetness = rain.equilibrium_wetness(weather, pct)
        assert wetness == reference
        assert rain.best_compound_for(wetness) == rain.best_compound_for(reference)


@pytest.mark.parametrize("with_evidence", [False, True])
def test_current_reading_and_stop_ignore_all_forecast_probability_fields(with_evidence):
    base = _state(weather="Light rain")
    if with_evidence:
        base["completed_laps"] = [
            {"lap_num": n, "weather": "Clear" if n < 6 else "Light rain",
             "lap_time_ms": 90000 if n < 6 else 104000,
             "valid": True, "pit_status": 0, "rain_pct": 20}
            for n in range(2, 10)
        ]
        base["driver_grip_feedback"] = {"lap": 9, "category": "damp", "confidence": 1.0}
    readings, calls = [], []
    for pct in (0, 20, 35, 50, 100):
        state = deepcopy(base)
        state.update(rain_now_pct=pct, rain_next_15_pct=pct,
                     weather_forecast=[_forecast(pct, "Storm", minutes=0)])
        for lap in state.get("completed_laps", []):
            lap["rain_pct"] = pct
        decision = rain.evaluate(state, base_lap_s=90, remaining_laps=20, pit_loss_s=22)
        reading = dict(decision["reading"])
        if with_evidence:
            assert reading["driver_report"] is not None
        assert reading.pop("rain_pct") == pct  # Metadata remains a probability.
        readings.append(reading)
        calls.append((decision["best_compound"], decision["should_change"], decision["trajectory"]))
        assert rain.surface_is_wet(state)
    assert all(item == readings[0] for item in readings)
    assert all(item == calls[0] for item in calls)


@pytest.mark.parametrize("pct", [0, 20, 50, 100])
def test_low_probability_heavy_rain_keeps_full_conditional_wetness(pct):
    state = _state(weather_forecast=[_forecast(pct)])
    scenarios = rain.project_wetness_scenarios(state, 0, 12, 90)
    certain = rain.project_wetness_scenarios(_state(weather_forecast=[_forecast(100)]), 0, 12, 90)[0]
    assert sum(item["probability"] for item in scenarios) == pytest.approx(1)
    wet = [item for item in scenarios if item["trajectory"][-1] > 0]
    dry = [item for item in scenarios if item["trajectory"][-1] == 0]
    assert sum(item["probability"] for item in wet) == pytest.approx(pct / 100)
    assert sum(item["probability"] for item in dry) == pytest.approx(1 - pct / 100)
    for item in wet:
        assert item["trajectory"] == certain["trajectory"]
        assert item["trajectory"][-1] > rain.WETNESS_SLICK_INTER


def test_costs_weight_heavy_rain_and_dry_outcomes_after_pricing_each():
    decisions = {
        pct: rain.evaluate(_state(weather_forecast=[_forecast(pct)]),
                           base_lap_s=90, remaining_laps=12, pit_loss_s=22)
        for pct in (0, 20, 100)
    }
    def stay_cost(decision):
        return next(item["weather_cost_s"] for item in decision["options"] if not item["is_change"])
    mixed = decisions[20]
    assert mixed["wetness"] == decisions[100]["wetness"] == 0
    assert stay_cost(mixed) == pytest.approx(0.2 * stay_cost(decisions[100]) + 0.8 * stay_cost(decisions[0]), abs=0.01)
    assert stay_cost(mixed) > stay_cost(decisions[0])
    assert abs(stay_cost(mixed) - rain.stint_cost_s("MEDIUM", mixed["trajectory"], 90)) > 10


def test_approximate_forecast_changes_weights_without_diluting_the_rain():
    state = _state(weather_forecast=[_forecast(20, "Storm")])
    perfect = rain.project_wetness_scenarios(state, 0, 12, 90)
    approximate = rain.project_wetness_scenarios(dict(state, forecast_accuracy=1), 0, 12, 90)
    perfect_wet = next(item for item in perfect if item["trajectory"][-1] > 0)
    approximate_wet = next(item for item in approximate if item["trajectory"][-1] > 0)
    assert approximate_wet["trajectory"] == perfect_wet["trajectory"]
    assert approximate_wet["probability"] < perfect_wet["probability"]
    assert sum(item["probability"] for item in approximate) == pytest.approx(1)


def test_changing_future_chances_preserves_marginals_and_surface_memory():
    state = _state(weather_forecast=[_forecast(20, minutes=3), _forecast(80, minutes=6), _forecast(0, minutes=9)])
    scenarios = rain.project_wetness_scenarios(state, 0, 12, 90)
    assert len(scenarios) == 3
    # Samples are due at 3/6/9 minutes. Conditions affect the next interval,
    # never the whole preceding 90-second lap ending at that boundary.
    assert all(item["trajectory"][1] == 0 for item in scenarios)
    assert sum(item["probability"] for item in scenarios if item["trajectory"][2] > 0) == pytest.approx(0.2)
    assert sum(item["probability"] for item in scenarios if item["trajectory"][4] > 0) == pytest.approx(0.8)
    for item in scenarios:
        if item["trajectory"][5] > 0:
            assert 0 < item["trajectory"][6] < item["trajectory"][5]


def test_future_only_and_beyond_finish_forecasts_cannot_become_current_rain():
    base = _state(weather="Light rain", rain_now_pct=0, rain_next_15_pct=100)
    state = dict(base, weather_forecast=[_forecast(100, "Storm", minutes=45)])
    reading = rain.estimate_wetness(state)
    assert reading["rain_pct"] == 0
    assert rain.project_wetness_scenarios(state, 0.52, 5, 90) == rain.project_wetness_scenarios(base, 0.52, 5, 90)


@pytest.mark.parametrize("weather,pct,expected", [("Heavy rain", None, 0.72), ("Heavy rain", 0, 0), ("Overcast", 20, 0.52), ("Unknown", 20, 0.52)])
def test_missing_chance_and_unspecified_rain_severity_have_explicit_fallbacks(weather, pct, expected):
    scenarios = rain.project_wetness_scenarios(_state(weather_forecast=[_forecast(pct, weather)]), 0, 40, 90)
    assert max(item["trajectory"][-1] for item in scenarios) == pytest.approx(expected, abs=0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_weather,expected_wetness", [(0, 0.0), (3, 0.52)])
async def test_udp_zero_offset_is_probability_and_session_weather_is_observation(actual_weather, expected_wetness):
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    packet = PacketSessionData()
    packet.weather = actual_weather
    packet.session_type = 15
    packet.total_laps = 30
    packet.num_weather_forecast_samples = 2
    now, later = packet.weather_forecast_samples[:2]
    now.time_offset, now.weather, now.rain_percentage = 0, 5, 0
    later.time_offset, later.weather, later.rain_percentage = 15, 4, 100
    await protocol.handle_PacketSessionData(packet)
    state = await store.snapshot()
    assert state["rain_now_pct"] == 0
    assert state["rain_next_15_pct"] == 100
    assert rain.estimate_wetness(state)["declared_wetness"] == expected_wetness
    # Removing offset zero must not promote the fifteen-minute forecast.
    packet.weather_forecast_samples[0] = later
    packet.num_weather_forecast_samples = 1
    await protocol.handle_PacketSessionData(packet)
    assert (await store.snapshot())["rain_now_pct"] == 0


@pytest.mark.asyncio
async def test_strategy_stints_use_expected_scenario_costs_at_their_lap_offset(stack):
    store, _, strategy, _, _, _ = stack
    await store.update(session_type="Race", mode_profile="race", current_lap=10,
                       total_laps=30, weather="Clear", track_temp_c=30, active_cars=20,
                       weather_forecast=[_forecast(20)])
    state = await store.snapshot()
    state["tyre"]["compound"] = "MEDIUM"
    crossover = strategy._weather_crossover(state, 90, 22, ["MEDIUM", "INTER"])
    assert crossover is not None  # Retain forecast risk even on a dry track.
    def simulate(trajectory, penalties=None):
        return strategy._simulate_stint(
            state, "INTER", 5, 2, 10.0, 90, {}, 1.0,
            wetness_trajectory=trajectory, start_offset=5,
            expected_weather_penalties=penalties,
        )["expected_time_s"]
    mixed = simulate(crossover["trajectory"], crossover["expected_lap_penalties"])
    conditional = sum(item["probability"] * simulate(item["trajectory"]) for item in crossover["scenarios"])
    assert mixed == pytest.approx(conditional)
    assert abs(mixed - simulate(crossover["trajectory"])) > 5


@pytest.mark.asyncio
async def test_recomputed_plans_receive_scenario_costs(stack, monkeypatch):
    store, _, strategy, _, _, _ = stack
    await store.update(session_type="Race", mode_profile="race", current_lap=10,
                       total_laps=20, weather="Clear", track_temp_c=30, active_cars=20,
                       weather_forecast=[_forecast(20)])
    seen = []
    original = strategy._simulate_stint
    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        seen.append(args[11] if len(args) > 11 else kwargs.get("expected_weather_penalties"))
        return result
    monkeypatch.setattr(strategy, "_simulate_stint", capture)
    result = await strategy.recompute()
    assert result["weather_crossover"]["scenarios"]
    assert seen and all(item for item in seen)
