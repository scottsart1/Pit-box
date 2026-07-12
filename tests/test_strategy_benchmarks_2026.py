import json
from pathlib import Path

import pytest


BENCHMARKS = json.loads(
    (Path(__file__).parents[1] / "data" / "strategy_benchmarks_2026.json").read_text(
        encoding="utf-8"
    )
)


def _base_race(state, *, current_lap, total_laps, track_id=0):
    state.session_uid = 2026
    state.session_type = "Race"
    state.mode_profile = "race"
    state.current_lap = current_lap
    state.total_laps = total_laps
    state.track_id = track_id
    state.player_position = 2
    state.active_cars = 20
    state.tyre.compound = "MEDIUM"
    state.tyre.age_laps = max(1, current_lap - 1)
    state.tyre.wear = [24, 25, 27, 26]
    state.completed_laps = [
        {
            "lap_num": 1,
            "lap_time_ms": 92_000,
            "valid": True,
            "compound": "HARD",
            "wear_start": [0, 0, 0, 0],
            "wear_end": [2, 2, 2, 2],
            "pit_status": 0,
        }
    ]
    state.tyre_sets = [
        {"compound": "SOFT", "available": True, "wear_pct": 0, "usable_life_laps": 14},
        {"compound": "MEDIUM", "available": True, "wear_pct": 0, "usable_life_laps": 28},
        {"compound": "HARD", "available": True, "wear_pct": 0, "usable_life_laps": 45},
    ]
    for index, driver in enumerate(state.drivers[:20]):
        driver.active = True
        driver.position = index + 1
        driver.name = f"Driver {index + 1}"
        driver.gap_to_player_s = float(index - 1) * 1.2


def test_benchmark_manifest_contains_expected_2026_cases():
    ids = {case["id"] for case in BENCHMARKS["cases"]}
    assert {
        "australia_vsc_discount",
        "japan_safety_car_opportunity",
        "britain_late_sc_track_position",
        "red_flag_free_change",
        "high_degradation_multistop",
    } <= ids


@pytest.mark.asyncio
async def test_australia_vsc_reduces_strategy_cost(stack):
    store, _, strategy, _, _, _ = stack
    await store.mutate(lambda state: _base_race(state, current_lap=11, total_laps=58, track_id=0))
    green = await strategy.recompute()
    await store.update(safety_car="virtual", race_control_phase="vsc")
    vsc = await strategy.recompute()

    assert vsc["neutralisation"]["effective_pit_loss_s"] < green["neutralisation"]["effective_pit_loss_s"]
    assert vsc["neutralisation"]["saving_vs_green_s"] >= 6
    assert vsc["recommended"]["risk_adjusted_time_s"] < green["recommended"]["risk_adjusted_time_s"]


@pytest.mark.asyncio
async def test_japan_full_sc_is_bigger_opportunity_than_vsc(stack):
    store, _, strategy, _, _, _ = stack
    await store.mutate(lambda state: _base_race(state, current_lap=22, total_laps=53, track_id=13))
    await store.update(safety_car="virtual", race_control_phase="vsc")
    vsc = await strategy.recompute()
    await store.update(safety_car="full", race_control_phase="safety_car")
    full_sc = await strategy.recompute()

    assert full_sc["neutralisation"]["effective_pit_loss_s"] < vsc["neutralisation"]["effective_pit_loss_s"]
    assert full_sc["neutralisation"]["field_compression_expected"] is True


@pytest.mark.asyncio
async def test_britain_late_sc_protects_track_position(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _base_race(state, current_lap=50, total_laps=52, track_id=7)
        state.safety_car = "full"
        state.race_control_phase = "safety_car"
        state.tyre.wear = [42, 43, 45, 44]
        # Cars directly behind will pass in the pit sequence under a compressed field.
        for index, driver in enumerate(state.drivers[:8]):
            driver.gap_to_player_s = float(index - 1) * 0.8

    await store.mutate(setup)
    result = await strategy.recompute()
    assert result["neutralisation"]["late_neutralisation"] is True
    assert result["neutralisation"]["finish_under_neutralisation_risk"] in {"medium", "high"}
    assert result["recommended"]["stops_remaining"] == 0


@pytest.mark.asyncio
async def test_red_flag_change_has_zero_normal_pit_loss(stack):
    store, _, strategy, _, _, _ = stack
    await store.mutate(lambda state: _base_race(state, current_lap=20, total_laps=50, track_id=4))
    await store.update(red_flag_active=True, race_control_phase="red_flag", safety_car="none")
    result = await strategy.recompute()
    assert result["neutralisation"]["effective_pit_loss_s"] == 0
    assert result["neutralisation"]["red_flag_tyre_change"] is True


@pytest.mark.asyncio
async def test_high_personal_degradation_can_make_multistop_optimal(stack):
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _base_race(state, current_lap=3, total_laps=35, track_id=9)
        state.tyre.age_laps = 2
        state.tyre.wear = [9, 10, 15, 14]
        state.completed_laps = []

    await store.mutate(setup)
    state = await store.snapshot_analysis()
    rates = 6.5
    historical = {
        "compounds": {
            "MEDIUM": {
                "max_wear_per_lap_pct": rates,
                "wheel_wear_per_lap_pct": [5.2, 5.2, 6.5, 6.2],
                "wear_sample_size": 8,
                "slope_s_per_lap": 0.12,
                "sample_size": 8,
            },
            "HARD": {
                "max_wear_per_lap_pct": rates * 0.65,
                "wheel_wear_per_lap_pct": [3.6, 3.6, 4.225, 4.0],
                "wear_sample_size": 8,
                "slope_s_per_lap": 0.07,
                "sample_size": 8,
            },
            "SOFT": {
                "max_wear_per_lap_pct": rates * 1.35,
                "wheel_wear_per_lap_pct": [7.8, 7.8, 8.775, 8.45],
                "wear_sample_size": 8,
                "slope_s_per_lap": 0.20,
                "sample_size": 8,
            },
        }
    }
    result = strategy.compute(state, historical)
    assert result["recommended"]["stops_remaining"] >= 2
    assert result["recommended"]["monte_carlo"]["p75_s"] >= result["recommended"]["monte_carlo"]["p50_s"]
    assert result["confidence"] == "high"
