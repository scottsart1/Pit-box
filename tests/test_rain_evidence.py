"""The laps that carry the change are evidence, and the pit lane is not.

Tyre learning drops every doubtful lap, which is right for a wear rate. The
wet model used the same filter, so the lap the rain arrived on, a lap in a
pack and a lap past a local yellow were all discarded, and a yellow in one
marshal sector switched off the measurement of the whole field. Rivals'
in-laps and out-laps, meanwhile, were read as wet pace. And "it's raining",
"it's wet" and "I need inters" were not recognised as reports at all.
"""

from __future__ import annotations

import pytest

from pitwall import rain
from pitwall.state import StateStore


def _lap(num, ms, *, weather="Clear", tags=(), sectors=None, **extra):
    third = ms // 3
    s1, s2, s3 = sectors or (third, third, ms - 2 * third)
    return {
        "lap_num": num, "lap_time_ms": ms, "valid": True, "weather": weather, "pit_status": 0,
        "compound": "MEDIUM", "s1_ms": s1, "s2_ms": s2, "s3_ms": s3,
        "learning_exclusions": list(tags), **extra,
    }


def _player(laps):
    return {"completed_laps": laps, "tyre": {"compound": "MEDIUM"}, "current_lap": len(laps) + 1}


# --- WX-06: which laps are evidence ------------------------------------------------


def test_the_lap_the_rain_arrived_on_is_evidence():
    laps = [_lap(n, 90_000) for n in (1, 2, 3)]
    laps.append(_lap(4, 102_000, weather="Light rain", tags=["weather_change"]))
    observation = rain.player_pace_observation(_player(laps), 90.0)
    assert observation["ratio"] == pytest.approx(102 / 90, abs=1e-3)


def test_a_lap_in_traffic_counts_for_less_not_nothing():
    dry = [_lap(n, 90_000) for n in (1, 2, 3, 4, 5)]
    in_traffic = rain.player_pace_observation(_player([*dry, _lap(6, 100_000, tags=["traffic"])]), 90.0)
    clear = rain.player_pace_observation(_player([*dry, _lap(6, 100_000)]), 90.0)
    assert in_traffic["ratio"] == clear["ratio"]
    assert in_traffic["evidence_weight"] < clear["evidence_weight"]
    only = rain.player_pace_observation(_player([_lap(1, 100_000, tags=["traffic"])]), 90.0)
    assert only["ratio"] is not None and only["samples"] == 1
    three = [_lap(n, 90_000) for n in (1, 2, 3)] + [_lap(n, 100_000, tags=["traffic"]) for n in (4, 5, 6)]
    assert rain.player_pace_observation(_player(three), 90.0)["samples"] == 2


def test_a_pit_lap_is_still_no_evidence():
    laps = [_lap(n, 90_000) for n in (1, 2, 3)] + [_lap(4, 112_000, tags=["pit_lap"], pit_status=1)]
    observation = rain.player_pace_observation(_player(laps), 90.0)
    assert observation["dropped_laps"] >= 1
    assert observation["ratio"] == pytest.approx(1.0)


def test_a_local_yellow_does_not_switch_off_the_field():
    last = _lap(5, 95_000, tags=["neutralised_lap"], local_yellow=True, full_course_neutralised=False)
    state = {"completed_laps": [_lap(4, 90_000), last]}
    assert rain._field_pace_block_reason(state) is None
    # It still counts for less as the player's own evidence.
    assert rain._wet_evidence_weight(last) == pytest.approx(0.5)


def test_a_safety_car_lap_still_switches_it_off():
    last = _lap(5, 120_000, tags=["neutralised_lap"], full_course_neutralised=True)
    state = {"completed_laps": [_lap(4, 90_000), last]}
    assert rain._field_pace_block_reason(state) == "awaiting_clean_lap_after_neutralisation"
    assert rain._wet_evidence_weight(last) == 0.0


def test_a_lap_recorded_before_the_distinction_keeps_the_old_test():
    state = {"completed_laps": [_lap(5, 95_000, tags=["neutralised_lap"])]}
    assert rain._field_pace_block_reason(state) == "awaiting_clean_lap_after_neutralisation"


def _store_with_lap_in_progress(**flags):
    store = StateStore()
    store.state.current_lap_started = {"learning_exclusions": []}
    for key, value in flags.items():
        setattr(store.state, key, value)
    store._mark_learning_context_locked()
    return store.state.current_lap_started


def test_a_yellow_is_recorded_as_local_and_a_safety_car_as_full_course():
    yellow = _store_with_lap_in_progress(fia_flag="yellow")
    assert yellow.get("local_yellow") is True and "full_course_neutralised" not in yellow
    assert "neutralised_lap" in yellow["learning_exclusions"]
    safety_car = _store_with_lap_in_progress(safety_car="full")
    assert safety_car.get("full_course_neutralised") is True
    clean = _store_with_lap_in_progress()
    assert not clean.get("local_yellow") and not clean.get("full_course_neutralised")


# --- WX-07: rivals' pit laps ---------------------------------------------------------


def _rival(index, latest_ms, *, stint_end=None, latest=8):
    history = [{"lap_num": n, "lap_ms": 90_000, "valid_flags": 1} for n in range(1, latest)]
    history.append({"lap_num": latest, "lap_ms": latest_ms, "valid_flags": 1})
    stints = [{"start_lap": 1, "end_lap": 255, "compound": "MEDIUM"}]
    if stint_end is not None:
        stints = [{"start_lap": 1, "end_lap": stint_end, "compound": "MEDIUM"},
                  {"start_lap": stint_end + 1, "end_lap": 255, "compound": "MEDIUM"}]
    return {"car_idx": index, "name": f"CAR{index}", "active": True, "result_label": "active",
            "tyre_compound": "MEDIUM", "lap_history": history, "tyre_stints": stints}


def _fitted(drivers):
    state = {"drivers": drivers, "player_car_index": 0, "completed_laps": []}
    observations = rain.field_pace_observations(state, dry_until_lap=5)
    return rain.fit_wetness(observations, 0.0)[0], len(observations)


def test_an_in_lap_is_not_a_wet_lap():
    """A car on its in-lap is read from its last racing lap instead."""
    clean = [_rival(index, 90_500) for index in range(1, 7)]
    in_laps = [_rival(index, 112_000, stint_end=8) for index in range(7, 13)]
    without_them = [_rival(index, 90_000, latest=7) for index in range(7, 13)]
    assert _fitted(clean + in_laps) == _fitted(clean + without_them)
    assert _fitted(clean + in_laps)[0] < 0.05


def test_an_out_lap_is_not_a_wet_lap():
    clean = [_rival(index, 90_500) for index in range(1, 7)]
    out_laps = [_rival(index, 112_000, stint_end=7) for index in range(7, 13)]
    # Lap 7 was the in-lap and lap 8 the out-lap: lap 6 is the last racing lap.
    without_them = [_rival(index, 90_000, latest=6) for index in range(7, 13)]
    assert _fitted(clean + out_laps) == _fitted(clean + without_them)
    assert _fitted(clean + out_laps)[0] < 0.05


def test_the_lap_before_the_stop_is_still_read():
    """The car's last racing lap before its in-lap is the track."""
    cars = [_rival(index, 90_500, stint_end=9, latest=8) for index in range(1, 7)]
    assert _fitted(cars)[1] == 6


# --- WX-11: what the driver says ------------------------------------------------------


def test_the_new_reports_sit_on_the_scale():
    assert (rain.GRIP_REPORT_WETNESS["spitting"][0] < rain.WETNESS_SLICK_INTER
            < rain.GRIP_REPORT_WETNESS["wet"][0] < rain.WETNESS_INTER_WET)


def test_a_wet_report_confirms_the_stop_and_spitting_does_not():
    from tools import rain_onset_probe as probe

    def decide(category):
        state = probe.state_at(11, 11, 100, with_field=False)
        state["driver_grip_feedback"] = {"lap": 11, "category": category, "confidence": 1.0}
        return rain.evaluate(state, base_lap_s=probe.DRY_LAP_S, remaining_laps=probe.TOTAL_LAPS - 10,
                             pit_loss_s=probe.PIT_LOSS_S, available_compounds=probe.AVAILABLE)

    assert decide("wet")["confirmed"] is True
    assert decide("spitting")["confirmed"] is False


async def _captured(stack, phrase, *, weather="Light rain"):
    from pitwall.brain import EngineerBrain

    store, database, _strategy, _setup, _analysis, tools = stack

    def grid(state):
        state.current_lap = 14
        state.session_type = "Race"
        state.mode_profile = "race"
        state.weather = weather

    await store.mutate(grid)
    await EngineerBrain(store, tools, database)._capture_feedback(phrase)
    return ((await store.snapshot_analysis()).get("driver_grip_feedback") or {}).get("category")


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase,category", [
    ("It's raining", "spitting"),
    ("it's starting to rain on the back straight", "spitting"),
    ("spots of rain in sector two", "spitting"),
    ("I've got drizzle", "spitting"),
    ("It's wet", "wet"),
    ("the track is wet now", "wet"),
    ("I need inters", "wet"),
    ("box for inters", "wet"),
    ("it's too wet for slicks", "wet"),
    ("I'm losing grip everywhere", "damp"),
    ("it's getting slippery", "damp"),
    ("it's properly wet", "soaked"),
])
async def test_rain_reports_are_captured(stack, phrase, category):
    assert await _captured(stack, phrase) == category


@pytest.mark.asyncio
async def test_a_question_about_rain_is_not_a_report(stack):
    assert await _captured(stack, "Is it raining at the back of the circuit?") is None


@pytest.mark.asyncio
async def test_losing_grip_in_the_dry_is_the_tyres_not_the_track(stack):
    assert await _captured(stack, "I'm losing grip on the rears", weather="Clear") is None


# --- WX-14: smaller items --------------------------------------------------------------


def test_the_dry_benchmark_is_recent():
    """Thirty dry laps, each 0.05 s quicker on fuel: the benchmark is the last five."""
    laps = [_lap(n, 92_000 - 50 * n) for n in range(1, 31)]
    observation = rain.player_pace_observation(_player(laps), None)
    assert observation["dry_base_lap_s"] == pytest.approx((92_000 - 50 * 28) / 1000.0)


def _wet_decision(**overrides):
    from tools import rain_onset_probe as probe

    state = probe.state_at(16, 11, 100)
    arguments = {"base_lap_s": probe.lap_time_s(15, 11), "remaining_laps": probe.TOTAL_LAPS - 15,
                 "pit_loss_s": probe.PIT_LOSS_S, "available_compounds": probe.AVAILABLE, **overrides}
    return rain.evaluate(state, **arguments), probe


def test_costs_are_priced_on_dry_pace():
    """The same call priced on the wet lap time and on the dry benchmark."""
    on_dry, probe = _wet_decision()
    inflated, _ = _wet_decision(base_lap_s=probe.DRY_LAP_S)
    assert on_dry["reading"]["player_pace"]["benchmark"] == "dry_laps"
    # Base lap time no longer scales the cost of each compound.
    assert on_dry["options"] == inflated["options"]


def test_a_later_stop_pays_the_green_flag_loss():
    """Rain forecast, dry now, a safety car out: the stop that suits is laps away."""
    from tools import rain_onset_probe as probe

    state = probe.state_at(12, 15, 100)
    arguments = {"base_lap_s": probe.DRY_LAP_S, "remaining_laps": probe.TOTAL_LAPS - 11,
                 "available_compounds": probe.AVAILABLE, "pit_loss_s": 10.0}
    discounted = rain.evaluate(state, **arguments)
    honest = rain.evaluate(state, **arguments, pit_loss_green_s=22.0)
    assert discounted["best_box_lap_offset"] == honest["best_box_lap_offset"] > 0
    assert discounted["margin_s"] - honest["margin_s"] == pytest.approx(12.0, abs=0.01)


def test_the_trend_follows_the_surface():
    assert rain._wetness_trend([], "Light rain", series=[0.0, 0.1, 0.25], now=0.3) == "wetting"
    assert rain._wetness_trend([], "Light rain", series=[0.5, 0.5, 0.5], now=0.5) == "steady"
    # The label changed to rain three laps ago; the track has stopped soaking.
    assert rain._wetness_trend([], "Light rain", series=[0.50, 0.51, 0.52], now=0.52) == "steady"
    assert rain._wetness_trend([], "Clear", series=[0.5, 0.42, 0.35], now=0.3) == "drying"


def test_the_reading_says_its_curves_are_judgements():
    assert rain.estimate_wetness({"weather": "Clear"})["calibration"] == "judgement"
