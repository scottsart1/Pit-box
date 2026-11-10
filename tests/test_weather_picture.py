"""The wet model as one picture, read by the screen, the engineer and the radio.

The engineer asked "is it going to rain?" answered from the raw forecast and a
60% rule while the radio called the stop from the wet model, so the two could
disagree about the same track. ``rain_next_15_pct`` was one sample, spoken as
"within fifteen minutes".
"""

from __future__ import annotations

import pytest

from pitwall import rain
from pitwall.proactive import WEATHER_LEVEL_LABELS, ProactiveEngineer
from pitwall.weather_picture import _crossover_clock, weather_picture


async def _wet_race(stack):
    from tools.strategy_probe import build_field, wet_setup

    store, _, strategy, *_ = stack

    def setup(state):
        wet_setup(state, total_laps=40, current_lap=30, position=6)
        state.tyre.compound = "MEDIUM"
        state.tyre.age_laps = 8
        state.drivers = build_field(current_lap=30, player_position=6, compound="MEDIUM", tyre_age=8)

    await store.mutate(setup)
    await strategy.recompute()
    return await store.snapshot_analysis()


@pytest.mark.asyncio
async def test_the_picture_is_built_from_the_published_crossover(stack):
    state = await _wet_race(stack)
    crossover = state["strategy"]["weather_crossover"]
    picture = weather_picture(state)
    assert picture["available"] and picture["relevant"]
    assert picture["surface"]["wetness"] == pytest.approx(crossover["wetness"], abs=1e-3)
    assert picture["surface"]["trend"] == crossover["trend"]
    sources = crossover["wetness_source"]
    assert picture["surface"]["sources"]["surface_model"] == sources["surface_model"]
    assert picture["surface"]["sources"]["lap_pace"] == sources["measured_pace"]
    assert picture["projection"]["mean"] == [round(float(v), 3) for v in crossover["trajectory"]]
    by_compound = {item["compound"]: item for item in crossover["options"]}
    for tyre in picture["tyres"]:
        assert tyre["cost_to_flag_s"] == pytest.approx(by_compound[tyre["compound"]]["weather_cost_s"], abs=0.05)
    assert picture["call"]["shows_s"] == crossover["margin_s"]
    assert picture["call"]["needs_s"] == crossover["uncertainty_s"]
    assert 0.0 <= picture["projection"]["p_wet_at_flag"] <= 1.0
    assert picture["calibration"] == "judgement"


@pytest.mark.asyncio
async def test_the_picture_and_the_radio_agree_on_the_call(stack):
    state = await _wet_race(stack)
    picture = weather_picture(state)
    call = ProactiveEngineer._weather_call(state)
    assert picture["call"]["level"] == call["level"]
    assert picture["call"]["label"] == WEATHER_LEVEL_LABELS[call["level"]]


@pytest.mark.asyncio
async def test_the_weather_answer_and_the_weather_call_read_one_model(stack):
    store, *_, tools = stack
    state = await _wet_race(stack)
    await store.mutate(lambda live: live.packet_group_freshness.update({"1": __import__("time").time()}))
    answer = await tools.get_weather_picture()
    assert answer["surface"]["wetness"] == pytest.approx(state["strategy"]["weather_crossover"]["wetness"], abs=1e-3)
    assert answer["call"]["level"] == ProactiveEngineer._weather_call(state)["level"]
    legacy = await tools.get_weather_forecast()
    assert legacy["surface"] == answer["surface"] and "rain_crossover_min" not in legacy


def test_a_dry_race_is_not_relevant():
    state = {
        "weather": "Clear", "current_lap": 10, "tyre": {"compound": "MEDIUM"},
        "weather_forecast": [{"time_offset_min": minutes, "weather": "Clear", "rain_pct": 0} for minutes in (0, 5, 15)],
        "strategy": {},
    }
    picture = weather_picture(state)
    assert picture["relevant"] is False
    assert picture["summary"] == "Dry. No rain forecast inside the race."
    assert picture["call"]["label"] == "none"


def test_a_twenty_percent_sample_makes_it_relevant():
    state = {
        "weather": "Overcast", "tyre": {"compound": "MEDIUM"}, "strategy": {},
        "weather_forecast": [{"time_offset_min": 15, "weather": "Light rain", "rain_pct": 20}],
    }
    assert weather_picture(state)["relevant"] is True


def test_the_forecast_shows_the_chance_given_the_sky_now():
    state = {
        "weather": "Light rain", "tyre": {"compound": "MEDIUM"}, "strategy": {},
        "weather_forecast": [
            {"time_offset_min": 0, "weather": "Light rain", "rain_pct": 50},
            {"time_offset_min": 10, "weather": "Light rain", "rain_pct": 50, "track_temp_c": 24},
        ],
    }
    later = weather_picture(state)["forecast"][1]
    assert later["chance_pct"] == 50
    assert later["chance_given_now_pct"] == round(100 * rain.conditioned_rain_probability(0.5, 0.5, True))
    assert later["in_laps"] == pytest.approx(10 * 60 / 90.0, abs=0.05)


def test_the_clock_counts_laps_to_the_crossover():
    assert _crossover_clock([0.10, 0.22, 0.31, 0.36, 0.40], "MEDIUM")["laps"] == 3
    assert _crossover_clock([0.10, 0.12], "MEDIUM")["laps"] is None
    on_inters = _crossover_clock([0.45, 0.40, 0.34, 0.30], "INTER")
    assert on_inters["to"] == "slicks" and on_inters["laps"] == 2


def test_the_sky_since_lap_is_the_trailing_run():
    laps = [{"lap_num": n, "weather": "Clear" if n < 7 else "Light rain"} for n in range(1, 10)]
    state = {"weather": "Light rain", "completed_laps": laps, "tyre": {"compound": "MEDIUM"}, "strategy": {}}
    assert weather_picture(state)["sky"]["since_lap"] == 7


# --- WX-13 -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_peak_within_a_window_is_the_peak():
    from f1.packets import resolve

    from pitwall.state import StateStore
    from pitwall.udp import F1DatagramProtocol
    from tools import replay_demo as replay

    store = StateStore()
    protocol = F1DatagramProtocol(store)
    packet = replay.PacketSessionData.from_buffer_copy(replay.build_session(100.0, 1, 30, 5))
    packet.num_weather_forecast_samples = 2
    for index, (minutes, chance) in enumerate(((5, 80), (15, 10))):
        sample = packet.weather_forecast_samples[index]
        sample.session_type = packet.session_type
        sample.time_offset = minutes
        sample.weather = 3
        sample.rain_percentage = chance
    await protocol._handle(resolve(bytes(packet)), None)
    assert store.state.rain_next_15_pct == 10
    assert store.state.rain_peak_15_pct == 80
    assert store.state.rain_peak_60_pct == 80
